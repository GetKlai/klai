"""
KlaiKnowledgeHook — LiteLLM pre-call hook that enriches LibreChat messages
with relevant organizational knowledge from the Klai Knowledge Service.

Mount into LiteLLM container at /app/custom/ and set PYTHONPATH=/app/custom.
Configure in config.yaml:
  litellm_settings:
    callbacks:
      - klai_knowledge.klai_knowledge_hook
"""

import asyncio
import logging
import os
import re
import time

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

# Trivial message patterns — skip retrieval (NL + EN)
_TRIVIAL_PATTERNS = re.compile(
    r"^(ok|okay|oke|oké|ja|nee|yes|no|bedankt|thanks|thank you|"
    r"dank je|dank u|graag|np|prima|goed|good|sure|hmm+|ah+|oh+|"
    r"begrepen|understood|clear|got it|doei|bye|hoi|hallo|hello|hi)[\s!.?]*$",
    re.IGNORECASE,
)


def _render_chunks(chunks: list[dict], header: str) -> list[str]:
    """Render a list of knowledge chunks into context block lines.

    Produces: a header line, one entry per chunk (optional ### title + text),
    and a trailing blank line between entries.
    Returns an empty list when chunks is empty.
    """
    if not chunks:
        return []
    lines: list[str] = [f"{header}\n"]
    for chunk in chunks:
        title = chunk.get("metadata", {}).get("title", "")
        text = chunk.get("text", "").strip()
        if title:
            lines.append(f"### {title}")
        lines.append(text)
        lines.append("")
    return lines


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


async def _retrieve(client: httpx.AsyncClient, payload: dict) -> list[dict] | BaseException:
    """Issue one retrieve POST and return filtered chunks, or the exception on failure.

    Catches BaseException (not just Exception) so that asyncio.CancelledError
    and other BaseException subclasses are returned rather than propagating,
    which would cause asyncio.gather(return_exceptions=True) to swallow them
    with an empty string representation.
    """
    try:
        resp = await client.post(KNOWLEDGE_RETRIEVE_URL, json=payload)
        resp.raise_for_status()
        chunks = resp.json().get("chunks", [])
        return [c for c in chunks if c.get("score", 0) >= RETRIEVE_MIN_SCORE]
    except BaseException as exc:
        return exc


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

        user_id: str | None = data.get("user")

        org_payload = {"query": query, "org_id": org_id, "top_k": RETRIEVE_TOP_K}
        personal_payload = {
            "query": query,
            "org_id": org_id,
            "top_k": RETRIEVE_TOP_K,
            "user_id": user_id,
            "kb_slugs": ["personal"],
        } if user_id else None

        # Per-request client — avoids event loop issues with class-level singletons.
        t_retrieve = time.perf_counter()
        async with httpx.AsyncClient(timeout=RETRIEVE_TIMEOUT) as client:
            # Fire both retrieves concurrently when a user_id is present.
            if personal_payload is not None:
                org_result, personal_result = await asyncio.gather(
                    _retrieve(client, org_payload),
                    _retrieve(client, personal_payload),
                    return_exceptions=True,
                )
            else:
                org_result = await _retrieve(client, org_payload)
                personal_result = []
        retrieve_ms = (time.perf_counter() - t_retrieve) * 1000

        # Handle per-scope failures independently — degrade gracefully.
        if isinstance(org_result, BaseException):
            logger.warning(
                "KlaiKnowledgeHook: org retrieval failed (%s: %s) after %.0fms — degrading",
                type(org_result).__name__,
                org_result,
                retrieve_ms,
            )
            return data
        if isinstance(personal_result, BaseException):
            logger.warning(
                "KlaiKnowledgeHook: personal retrieval failed (%s: %s) — continuing with org-only",
                type(personal_result).__name__,
                personal_result,
            )
            personal_result = []

        org_chunks: list[dict] = org_result
        personal_chunks: list[dict] = personal_result

        if not org_chunks and not personal_chunks:
            return data

        # Build context block: personal chunks first, then org chunks
        lines: list[str] = (
            _render_chunks(personal_chunks, "[Persoonlijke kennis]")
            + _render_chunks(
                org_chunks,
                "[Klai Knowledge — relevant context from your organisation's knowledge base]",
            )
        )

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
        logger.info(
            "KlaiKnowledgeHook: injected %d org + %d personal chunks for org=%s in %.0fms",
            len(org_chunks), len(personal_chunks), org_id, retrieve_ms,
        )
        return data

    async def async_post_call_success_hook(self, *args, **kwargs):
        pass

    async def async_post_call_failure_hook(self, *args, **kwargs):
        pass


# Module-level instance (some LiteLLM versions require this form)
klai_knowledge_hook = KlaiKnowledgeHook()
