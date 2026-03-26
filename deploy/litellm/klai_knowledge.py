"""
KlaiKnowledgeHook — LiteLLM pre-call hook that enriches LibreChat messages
with relevant organizational knowledge from the Klai Knowledge Service.

Mount into LiteLLM container at /app/custom/ and set PYTHONPATH=/app/custom.
Configure in config.yaml:
  litellm_settings:
    callbacks:
      - klai_knowledge.klai_knowledge_hook

Authorization is fail-closed: any user without a verified knowledge entitlement
receives no KB injection. If the portal authorization endpoint is unreachable,
injection is silently skipped (WARNING logged).

KB-context presence is signalled to downstream hooks via data["_klai_kb_meta"].
The custom_router uses this to prevent model downgrade for KB-enriched requests.
"""

import logging
import os
import re
import time

import httpx
from litellm.integrations.custom_logger import CustomLogger

logger = logging.getLogger(__name__)

KNOWLEDGE_RETRIEVE_URL = os.getenv(
    "KNOWLEDGE_RETRIEVE_URL",
    "http://retrieval-api:8040/retrieve",
)
PORTAL_API_URL = os.getenv("PORTAL_API_URL", "http://portal-api:8000")
PORTAL_INTERNAL_SECRET = os.getenv("PORTAL_INTERNAL_SECRET", "")
RETRIEVE_TIMEOUT = float(os.getenv("KNOWLEDGE_RETRIEVE_TIMEOUT", "3.0"))
RETRIEVE_TOP_K = int(os.getenv("KNOWLEDGE_RETRIEVE_TOP_K", "5"))

# Trivial message patterns — skip retrieval (NL + EN)
_TRIVIAL_PATTERNS = re.compile(
    r"^(ok|okay|oke|oké|ja|nee|yes|no|bedankt|thanks|thank you|"
    r"dank je|dank u|graag|np|prima|goed|good|sure|hmm+|ah+|oh+|"
    r"begrepen|understood|clear|got it|doei|bye|hoi|hallo|hello|hi)[\s!.?]*$",
    re.IGNORECASE,
)


def _is_trivial(text: str) -> bool:
    text = text.strip()
    if len(text) < 8:
        return True
    return bool(_TRIVIAL_PATTERNS.match(text))


def _last_user_message(messages: list[dict]) -> str | None:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                # Multi-modal message — extract text parts
                return " ".join(
                    p.get("text", "") for p in content if p.get("type") == "text"
                )
    return None


def _build_conversation_history(messages: list[dict]) -> list[dict]:
    """Return up to the last 6 turns (3 exchanges) of user/assistant history.

    The last user message is excluded — it is the current query being retrieved for.
    Used by retrieval-api for coreference resolution ("hij" → "Jan Pietersen").
    """
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in messages[:-1]
        if m.get("role") in ("user", "assistant")
        and isinstance(m.get("content"), str)
    ]
    return history[-6:]


async def _check_user_feature(user_id: str, org_id: str, cache) -> bool:
    """Check whether the user has the knowledge product entitlement.

    Result is cached in LiteLLM DualCache for 300s to avoid a portal API call
    per chat turn. Entitlements rarely change mid-session; 5-minute lag on
    revocation is acceptable.

    Fail-closed: any HTTP error or unreachable portal returns False.
    """
    cache_key = f"kb_authz:{org_id}:{user_id}"
    cached = await cache.async_get_cache(cache_key)
    if cached is not None:
        return cached == "1"

    if not PORTAL_INTERNAL_SECRET:
        logger.warning("KlaiKnowledgeHook: PORTAL_INTERNAL_SECRET not set — fail-closed")
        return False

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(
                f"{PORTAL_API_URL}/internal/v1/users/{user_id}/feature/knowledge",
                params={"org_id": org_id},
                headers={"Authorization": f"Bearer {PORTAL_INTERNAL_SECRET}"},
            )
            resp.raise_for_status()
            enabled = resp.json().get("enabled", False)
    except Exception as exc:
        logger.warning("KlaiKnowledgeHook: portal authz failed (%s) — fail-closed", exc)
        return False

    # Cache result: True as "1", False as "0" (DualCache stores strings)
    await cache.async_set_cache(cache_key, "1" if enabled else "0", ttl=300)
    return enabled


class KlaiKnowledgeHook(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if call_type not in ("completion", "acompletion"):
            return data

        messages = data.get("messages", [])
        query = _last_user_message(messages)
        if not query or _is_trivial(query):
            return data

        # org_id lives in LiteLLM team key metadata
        metadata = getattr(user_api_key_dict, "metadata", {}) or {}
        org_id = metadata.get("org_id")
        if not org_id:
            # Master key usage — no org scope available, skip silently
            return data

        # user_id = LibreChat MongoDB ObjectId sent as the "user" field
        user_id = data.get("user", "")
        if not user_id:
            return data

        # Authorization check (fail-closed, cached 300s)
        if not await _check_user_feature(user_id, org_id, cache):
            return data

        conversation_history = _build_conversation_history(messages)

        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=RETRIEVE_TIMEOUT) as client:
                resp = await client.post(
                    KNOWLEDGE_RETRIEVE_URL,
                    json={
                        "query": query,
                        "org_id": org_id,
                        "user_id": user_id,
                        "scope": "both",
                        "top_k": RETRIEVE_TOP_K,
                        "conversation_history": conversation_history,
                    },
                    headers={"X-Internal-Secret": PORTAL_INTERNAL_SECRET} if PORTAL_INTERNAL_SECRET else {},
                )
                resp.raise_for_status()
                result = resp.json()
        except Exception as exc:
            logger.warning("KlaiKnowledgeHook: retrieval failed (%s) — degrading", exc)
            return data

        retrieval_ms = int((time.monotonic() - t0) * 1000)

        # If the retrieval-gate determined no KB context is needed, skip injection
        if result.get("retrieval_bypassed"):
            data["_klai_kb_meta"] = {
                "org_id": org_id,
                "user_id": user_id,
                "chunks_injected": 0,
                "retrieval_ms": retrieval_ms,
                "gate_bypassed": True,
            }
            return data

        chunks = result.get("chunks", [])
        if not chunks:
            return data

        # Build context block with provenance labels per chunk
        lines = [
            "[Klai Kennisbank — gebruik dit als primaire informatiebron voor deze vraag]\n"
        ]
        for chunk in chunks:
            title = chunk.get("metadata", {}).get("title", "")
            scope_label = chunk.get("scope", "org")
            label = "[persoonlijk]" if scope_label == "personal" else "[org]"
            text = chunk.get("text", "").strip()
            if title:
                lines.append(f"### {title}  {label}")
            else:
                lines.append(f"### Kennisbank  {label}")
            lines.append(text)
            lines.append("")
        lines.append("[Einde kennisbank-context]")
        context_block = "\n".join(lines)

        # Prepend to existing system message or insert new one
        system_idx = next(
            (i for i, m in enumerate(messages) if m.get("role") == "system"), None
        )
        if system_idx is not None:
            existing = messages[system_idx].get("content", "")
            messages[system_idx] = {
                "role": "system",
                "content": f"{context_block}\n\n{existing}",
            }
        else:
            messages.insert(0, {"role": "system", "content": context_block})

        data["messages"] = messages
        # Signal KB injection to downstream hooks (e.g. custom_router, post-call logger)
        data["_klai_kb_meta"] = {
            "org_id": org_id,
            "user_id": user_id,
            "chunks_injected": len(chunks),
            "retrieval_ms": retrieval_ms,
            "gate_bypassed": False,
        }
        return data

    async def async_post_call_success_hook(self, data, user_api_key_dict, response):
        kb_meta = data.get("_klai_kb_meta")
        if kb_meta and not kb_meta.get("gate_bypassed"):
            logger.info(
                "KB injection: org=%s user=%s chunks=%d retrieval_ms=%d",
                kb_meta["org_id"],
                kb_meta["user_id"],
                kb_meta["chunks_injected"],
                kb_meta["retrieval_ms"],
            )

    async def async_post_call_failure_hook(self, *args, **kwargs):
        pass


# Module-level instance (some LiteLLM versions require this form)
klai_knowledge_hook = KlaiKnowledgeHook()
