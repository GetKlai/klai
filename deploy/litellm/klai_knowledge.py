"""
KlaiKnowledgeHook — LiteLLM pre-call hook that enriches LibreChat messages
with relevant organizational knowledge from the Klai Knowledge Service.

Mount into LiteLLM container at /app/custom/ and set PYTHONPATH=/app/custom.
Configure in config.yaml:
  litellm_settings:
    callbacks:
      - klai_knowledge.klai_knowledge_hook
"""

import logging
import os
import re

import httpx
from litellm.integrations.custom_logger import CustomLogger

logger = logging.getLogger(__name__)

KNOWLEDGE_RETRIEVE_URL = os.getenv(
    "KNOWLEDGE_RETRIEVE_URL",
    "http://knowledge-ingest:8000/knowledge/v1/retrieve",
)
RETRIEVE_TIMEOUT = float(os.getenv("KNOWLEDGE_RETRIEVE_TIMEOUT", "2.0"))
RETRIEVE_TOP_K = int(os.getenv("KNOWLEDGE_RETRIEVE_TOP_K", "5"))
RETRIEVE_MIN_SCORE = float(os.getenv("KNOWLEDGE_RETRIEVE_MIN_SCORE", "0.4"))
KNOWLEDGE_INGEST_SECRET = os.getenv("KNOWLEDGE_INGEST_SECRET", "")

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

        try:
            headers: dict[str, str] = {}
            if KNOWLEDGE_INGEST_SECRET:
                headers["X-Internal-Secret"] = KNOWLEDGE_INGEST_SECRET
            async with httpx.AsyncClient(timeout=RETRIEVE_TIMEOUT) as client:
                resp = await client.post(
                    KNOWLEDGE_RETRIEVE_URL,
                    json={
                        "query": query,
                        "org_id": org_id,
                        "top_k": RETRIEVE_TOP_K,
                        "kb_slugs": ["org"],
                    },
                    headers=headers,
                )
                resp.raise_for_status()
                result = resp.json()
        except Exception as exc:
            logger.warning("KlaiKnowledgeHook: retrieval failed (%s) — degrading", exc)
            return data

        chunks = [c for c in result.get("chunks", []) if c.get("score", 0) >= RETRIEVE_MIN_SCORE]
        if not chunks:
            return data

        # Build context block and inject as system message prefix
        lines = ["[Klai Knowledge — relevant context from your organisation's knowledge base]\n"]
        for chunk in chunks:
            title = chunk.get("metadata", {}).get("title", "")
            text = chunk.get("text", "").strip()
            if title:
                lines.append(f"### {title}")
            lines.append(text)
            lines.append("")
        lines.append("[End knowledge base context]")
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
        return data

    async def async_post_call_success_hook(self, *args, **kwargs):
        pass

    async def async_post_call_failure_hook(self, *args, **kwargs):
        pass


# Module-level instance (some LiteLLM versions require this form)
klai_knowledge_hook = KlaiKnowledgeHook()
