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

KNOWLEDGE_RETRIEVE_URL = os.getenv("KNOWLEDGE_RETRIEVE_URL")
if not KNOWLEDGE_RETRIEVE_URL:
    raise RuntimeError("KNOWLEDGE_RETRIEVE_URL is not set")
PORTAL_API_URL = os.getenv("PORTAL_API_URL", "http://portal-api:8000")
PORTAL_INTERNAL_SECRET = os.getenv("PORTAL_INTERNAL_SECRET", "")
RETRIEVE_TIMEOUT = float(os.getenv("KNOWLEDGE_RETRIEVE_TIMEOUT", "3.0"))
RETRIEVE_TOP_K = int(os.getenv("KNOWLEDGE_RETRIEVE_TOP_K", "5"))
KLAI_GAP_SOFT_THRESHOLD = float(os.getenv("KLAI_GAP_SOFT_THRESHOLD", "0.4"))
KLAI_GAP_DENSE_THRESHOLD = float(os.getenv("KLAI_GAP_DENSE_THRESHOLD", "0.35"))
PORTAL_RETRIEVAL_LOG_URL = os.getenv(
    "PORTAL_RETRIEVAL_LOG_URL", f"{PORTAL_API_URL}/internal/v1/retrieval-log"
)
EMBEDDING_MODEL_VERSION = os.getenv("EMBEDDING_MODEL_VERSION", "bge-m3-v1")

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


async def _get_kb_feature(user_id: str, org_id: str, cache) -> dict:
    """Return the user's KB feature state including entitlement and scope preference.

    Two-level cache strategy:
    - Version pointer (kb_ver:...) — 30s TTL. Expires when kb_pref_version increments,
      forcing a fresh portal fetch within 30s of a preference change.
    - Feature data (kb_feature:...:version) — 300s TTL.

    Fail-closed for entitlement: portal errors return enabled=False.
    Fail-open for retrieval preference: portal errors leave kb_retrieval_enabled=True
    so existing retrieval behavior is preserved (REQ-N1).

    Backward compatible: handles old {"enabled": bool} portal responses gracefully.
    """
    if not PORTAL_INTERNAL_SECRET:
        logger.warning("KlaiKnowledgeHook: PORTAL_INTERNAL_SECRET not set — fail-closed")
        return {"enabled": False, "kb_retrieval_enabled": True, "kb_personal_enabled": True,
                "kb_slugs_filter": None, "kb_narrow": False, "version": 0}

    # Step 1: check version pointer (short-lived — invalidated by preference changes)
    version_key = f"kb_ver:{org_id}:{user_id}"
    cached_version = await cache.async_get_cache(version_key)

    if cached_version is not None:
        feature_key = f"kb_feature:{org_id}:{user_id}:{cached_version}"
        cached = await cache.async_get_cache(feature_key)
        if cached is not None:
            return cached

    # Cache miss — fetch fresh from portal
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(
                f"{PORTAL_API_URL}/internal/v1/users/{user_id}/feature/knowledge",
                params={"org_id": org_id},
                headers={"Authorization": f"Bearer {PORTAL_INTERNAL_SECRET}"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("KlaiKnowledgeHook: portal feature fetch failed (%s) — fail-closed", exc)
        return {"enabled": False, "kb_retrieval_enabled": True, "kb_personal_enabled": True,
                "kb_slugs_filter": None, "kb_narrow": False, "version": 0}

    version = data.get("kb_pref_version", 0)
    result = {
        "enabled": data.get("enabled", False),
        "kb_retrieval_enabled": data.get("kb_retrieval_enabled", True),
        "kb_personal_enabled": data.get("kb_personal_enabled", True),
        "kb_slugs_filter": data.get("kb_slugs_filter"),
        "kb_narrow": data.get("kb_narrow", False),
        "version": version,
    }

    # Store version pointer (30s) and feature data (300s) separately
    await cache.async_set_cache(version_key, str(version), ttl=30)
    await cache.async_set_cache(f"kb_feature:{org_id}:{user_id}:{version}", result, ttl=300)
    return result


# @MX:NOTE: [AUTO] Gap thresholds (0.4 reranker, 0.35 dense) are configurable via
# @MX:NOTE: KLAI_GAP_SOFT_THRESHOLD / KLAI_GAP_DENSE_THRESHOLD env vars (SPEC-KB-014)
def _classify_gap(chunks: list[dict]) -> str | None:
    """Classify retrieval result. Returns 'hard', 'soft', or None (success)."""
    if not chunks:
        return "hard"
    reranker_scores = [
        c.get("reranker_score")
        for c in chunks
        if c.get("reranker_score") is not None
    ]
    if reranker_scores:
        if all(s < KLAI_GAP_SOFT_THRESHOLD for s in reranker_scores):
            return "soft"
    else:
        dense_scores = [c.get("score", 0.0) for c in chunks]
        if all(s < KLAI_GAP_DENSE_THRESHOLD for s in dense_scores):
            return "soft"
    return None


# @MX:WARN: [AUTO] Fire-and-forget via create_task — caller must be inside a running event loop.
# @MX:REASON: Wraps in try/except RuntimeError to handle test environments without a loop.
def _fire_gap_event(
    org_id: str,
    user_id: str,
    query_text: str,
    gap_type: str,
    chunks: list[dict],
    retrieval_ms: int,
    taxonomy_node_ids: list[int] | None = None,
) -> None:
    """Schedule an async gap event POST without blocking the pre-call hook."""
    import asyncio

    top_chunk = (
        max(chunks, key=lambda c: c.get("reranker_score") or c.get("score", 0.0))
        if chunks
        else None
    )
    top_score = (
        (top_chunk.get("reranker_score") or top_chunk.get("score"))
        if top_chunk
        else None
    )
    nearest_kb_slug = (
        top_chunk.get("metadata", {}).get("kb_slug")
        if top_chunk and gap_type == "soft"
        else None
    )

    try:
        org_id_int = int(org_id)
    except (ValueError, TypeError):
        logger.warning("KlaiKnowledgeHook: non-numeric org_id '%s', skipping gap event", org_id)
        return

    payload = {
        "org_id": org_id_int,
        "user_id": user_id,
        "query_text": query_text,
        "gap_type": gap_type,
        "top_score": top_score,
        "nearest_kb_slug": nearest_kb_slug,
        "chunks_retrieved": len(chunks),
        "retrieval_ms": retrieval_ms,
    }
    # SPEC-KB-021 R6: include taxonomy filter when it was part of the retrieve request
    if taxonomy_node_ids:
        payload["taxonomy_node_ids"] = taxonomy_node_ids

    async def _post():
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                await client.post(
                    f"{PORTAL_API_URL}/internal/v1/gap-events",
                    json=payload,
                    headers={"Authorization": f"Bearer {PORTAL_INTERNAL_SECRET}"},
                )
        except Exception as exc:
            logger.warning("KlaiKnowledgeHook: gap event POST failed (%s)", exc)

    try:
        asyncio.get_running_loop().create_task(_post())
    except RuntimeError:
        pass  # No running event loop (test context) — skip silently


# @MX:NOTE: [AUTO] Fire-and-forget retrieval log -- mirrors _fire_gap_event pattern. SPEC-KB-015.
# @MX:WARN: [AUTO] Uses create_task -- caller must be inside running event loop.
# @MX:REASON: Silently discards on no-loop (test context) and any HTTP error (REQ-KB-015-03).
def _fire_retrieval_log(
    org_id: str,
    user_id: str,
    chunk_ids: list,
    reranker_scores: list,
    query_resolved: str,
) -> None:
    """Schedule an async retrieval log POST without blocking the pre-call hook."""
    import asyncio
    from datetime import datetime

    try:
        int(org_id)
    except (ValueError, TypeError):
        logger.warning("KlaiKnowledgeHook: non-numeric org_id '%s', skipping retrieval log", org_id)
        return

    payload = {
        "org_id": str(org_id),
        "user_id": user_id,
        "chunk_ids": chunk_ids,
        "reranker_scores": reranker_scores,
        "query_resolved": query_resolved,
        "embedding_model_version": EMBEDDING_MODEL_VERSION,
        "retrieved_at": datetime.utcnow().isoformat(),
    }

    async def _post():
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                await client.post(
                    PORTAL_RETRIEVAL_LOG_URL,
                    json=payload,
                    headers={"Authorization": f"Bearer {PORTAL_INTERNAL_SECRET}"},
                )
        except Exception as exc:
            logger.warning("KlaiKnowledgeHook: retrieval log POST failed (%s)", exc)

    try:
        asyncio.get_running_loop().create_task(_post())
    except RuntimeError:
        pass  # No running event loop (test context)


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

        # Feature gate + KB scope preference (version-based cache, 30s propagation)
        feature = await _get_kb_feature(user_id, org_id, cache)
        if not feature["enabled"]:
            return data  # no entitlement — fail-closed

        if not feature["kb_retrieval_enabled"]:
            return data  # user disabled KB retrieval (REQ-E4 pre-step skip)

        # Determine retrieval scope, KB slug filter, and answer mode
        scope = "both" if feature.get("kb_personal_enabled", True) else "org"
        kb_slugs = feature.get("kb_slugs_filter")  # None = all org KBs
        kb_narrow = feature.get("kb_narrow", False)

        conversation_history = _build_conversation_history(messages)

        retrieve_body: dict = {
            "query": query,
            "org_id": org_id,
            "user_id": user_id,
            "scope": scope,
            "top_k": RETRIEVE_TOP_K,
            "conversation_history": conversation_history,
        }
        if kb_slugs:
            retrieve_body["kb_slugs"] = kb_slugs

        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=RETRIEVE_TIMEOUT) as client:
                resp = await client.post(
                    KNOWLEDGE_RETRIEVE_URL,
                    json=retrieve_body,
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
            data.setdefault("metadata", {})["_klai_kb_meta"] = {
                "org_id": org_id,
                "user_id": user_id,
                "chunks_injected": 0,
                "retrieval_ms": retrieval_ms,
                "gate_bypassed": True,
            }
            return data

        chunks = result.get("chunks", [])

        # --- Gap detection (KB-014) ---
        gap_type = _classify_gap(chunks)
        if gap_type is not None and org_id and user_id:
            _fire_gap_event(
                org_id=org_id,
                user_id=user_id,
                query_text=query,
                gap_type=gap_type,
                chunks=chunks,
                retrieval_ms=retrieval_ms,
                taxonomy_node_ids=retrieve_body.get("taxonomy_node_ids") or None,
            )

        # --- Retrieval log (SPEC-KB-015-01) ---
        chunk_ids = [c.get("chunk_id") for c in chunks if c.get("chunk_id")]
        reranker_scores = [c.get("reranker_score") or 0.0 for c in chunks]
        if chunk_ids and not result.get("retrieval_bypassed"):
            _fire_retrieval_log(org_id, user_id, chunk_ids, reranker_scores, query)

        if not chunks:
            return data

        # Build context block with provenance labels per chunk
        # Narrow: model must answer strictly from KB chunks only.
        # Broad (default): KB as additional context, general knowledge allowed.
        if kb_narrow:
            header = (
                "[Klai Kennisbank — beantwoord uitsluitend op basis van onderstaande bronnen. "
                "Gebruik geen algemene kennis buiten deze bronnen. "
                "Staat het antwoord er niet in? Zeg dan: 'Ik kan dit niet vinden in de kennisbank.']\n"
            )
        else:
            header = (
                "[Klai Kennisbank — gebruik dit als aanvullende context bij je antwoord. "
                "Je mag dit aanvullen met je algemene kennis.]\n"
            )
        source_link_instruction = (
            "[ANTWOORDFORMAAT — volg dit ALTIJD:\n"
            "1. Begin met een korte TLDR (2-3 zinnen) van het antwoord.\n"
            "2. Direct daarna een lijst met links naar de relevante bronnen:\n"
            "   📎 [Paginatitel](source_url) | [Paginatitel](source_url)\n"
            "3. Daarna het uitgebreide antwoord met [n] citaties.\n\n"
            "Wanneer een bron een source_url heeft, gebruik die ALTIJD als markdown-link. "
            "Laat GEEN enkele source_url weg uit de bronnenlijst.]\n"
        )
        lines = [header, source_link_instruction]
        for chunk in chunks:
            title = chunk.get("title") or chunk.get("metadata", {}).get("title", "")
            source_url = chunk.get("source_url", "")
            scope_label = chunk.get("scope", "org")
            label = "[persoonlijk]" if scope_label == "personal" else "[org]"
            text = chunk.get("text", "").strip()
            if title and source_url:
                lines.append(f"### [{title}]({source_url})  {label}")
            elif title:
                lines.append(f"### {title}  {label}")
            elif source_url:
                lines.append(f"### [Bron]({source_url})  {label}")
            else:
                lines.append(f"### Kennisbank  {label}")
            lines.append(text)
            if source_url:
                lines.append(f"source_url: {source_url}")
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
        # Stored in data["metadata"] so it is never forwarded to the LLM provider.
        data.setdefault("metadata", {})["_klai_kb_meta"] = {
            "org_id": org_id,
            "user_id": user_id,
            "chunks_injected": len(chunks),
            "chunk_ids": chunk_ids,
            "retrieval_ms": retrieval_ms,
            "gate_bypassed": False,
        }
        return data

    async def async_post_call_success_hook(self, data, user_api_key_dict, response):
        kb_meta = data.get("metadata", {}).get("_klai_kb_meta")
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
