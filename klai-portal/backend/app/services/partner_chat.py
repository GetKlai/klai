"""Partner chat completions service.

SPEC-API-001 TASK-008/009:
- Retrieve context from retrieval-api
- Forward to LiteLLM for non-streaming and streaming completions
- Build augmented system prompt with retrieved chunks
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import httpx
import structlog

from app.core.config import Settings
from app.trace import get_trace_headers

logger = structlog.get_logger()


def _last_user_message(messages: list[dict]) -> str | None:
    """Extract the last user message from the messages array."""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join(p.get("text", "") for p in content if p.get("type") == "text")
    return None


def _build_conversation_history(messages: list[dict]) -> list[dict]:
    """Return up to the last 6 turns (3 exchanges), excluding the last user message."""
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in messages[:-1]
        if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)
    ]
    return history[-6:]


_GROUNDED_SYSTEM_PROMPT = (
    "[CRITICAL] Respond in the language of the user's question. "
    "Als de gebruiker Nederlands schrijft, antwoord je in het Nederlands. "
    "If the user writes English, respond in English. Never switch mid-conversation.\n\n"
    "You are Klai AI, a knowledge assistant. You answer questions based on the knowledge base chunks provided.\n\n"
    "## How to answer\n"
    "Start with the answer. No warm-up, no rephrasing the question, no 'great question!'\n"
    "Simple question: 1-3 sentences. Complex question: the core answer first, then the detail.\n"
    "Be direct. Be honest. If the sources say something unexpected, say it.\n\n"
    "## How to cite\n"
    "Every factual claim gets a [n] citation where n is the chunk number. "
    "If a chunk includes a URL or help page link, include it: [n] (https://...). "
    "If sources contradict each other, say so — don't pick a side silently.\n\n"
    "## When the answer isn't there\n"
    "Say it plainly: 'That's not in the knowledge base.' "
    "Don't guess. Don't fill the gap with general knowledge. "
    "If you're partially sure, say that too: 'The knowledge base touches on this, but doesn't fully answer it.'"
)


def _build_system_prompt(chunks: list[dict], original_system: str | None = None) -> str:
    """Build a grounded system prompt augmented with retrieved context chunks."""
    base = original_system or _GROUNDED_SYSTEM_PROMPT

    if not chunks:
        return base

    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        text = chunk.get("text", "")
        if text:
            context_parts.append(f"[{i}] {text}")

    if not context_parts:
        return base

    context_block = "\n\n".join(context_parts)
    return f"{base}\n\nContext:\n{context_block}"


async def retrieve_context(
    org_id: int,
    zitadel_org_id: str,
    kb_slugs: list[str],
    messages: list[dict],
    settings: Settings,
) -> tuple[list[dict], str]:
    """Call retrieval-api and return (chunks, augmented_system_prompt).

    Follows the pattern from deploy/litellm/klai_knowledge.py.
    """
    query = _last_user_message(messages)
    if not query:
        return [], _build_system_prompt([])

    conversation_history = _build_conversation_history(messages)

    # Extract original system message if present
    original_system = None
    for msg in messages:
        if msg.get("role") == "system":
            original_system = msg.get("content", "")
            break

    retrieve_body: dict = {
        "query": query,
        "org_id": zitadel_org_id,  # retrieval-api expects string org_id
        "scope": "org",
        "top_k": 8,
        "conversation_history": conversation_history,
    }
    if kb_slugs:
        retrieve_body["kb_slugs"] = kb_slugs

    retrieval_url = settings.knowledge_retrieve_url
    if not retrieval_url:
        logger.warning("partner_chat_no_retrieval_url")
        return [], _build_system_prompt([], original_system)

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{retrieval_url}/retrieve",
            json=retrieve_body,
            headers={
                "X-Internal-Secret": settings.internal_secret,
                **get_trace_headers(),
            },
        )
        resp.raise_for_status()
        result = resp.json()

    chunks = result.get("chunks", [])
    system_prompt = _build_system_prompt(chunks, original_system)

    return chunks, system_prompt


async def chat_completion_non_streaming(
    messages: list[dict],
    model: str,
    temperature: float,
    system_prompt: str,
    settings: Settings,
) -> dict:
    """Forward to LiteLLM and return complete response as dict.

    POST to litellm with stream=false.
    """
    # Replace/prepend system message
    augmented_messages = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        if msg.get("role") != "system":
            augmented_messages.append(msg)

    litellm_url = settings.litellm_base_url

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{litellm_url}/v1/chat/completions",
            json={
                "model": model,
                "messages": augmented_messages,
                "temperature": temperature,
                "stream": False,
            },
            headers={
                "Authorization": f"Bearer {settings.litellm_master_key}",
                **get_trace_headers(),
            },
        )
        resp.raise_for_status()
        return resp.json()


async def chat_completion_streaming(
    messages: list[dict],
    model: str,
    temperature: float,
    system_prompt: str,
    settings: Settings,
) -> AsyncGenerator[bytes]:
    """Stream LiteLLM SSE response byte-for-byte.

    POST to LiteLLM with stream=true, yield each chunk as-is.
    """
    augmented_messages = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        if msg.get("role") != "system":
            augmented_messages.append(msg)

    litellm_url = settings.litellm_base_url

    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST",
            f"{litellm_url}/v1/chat/completions",
            json={
                "model": model,
                "messages": augmented_messages,
                "temperature": temperature,
                "stream": True,
            },
            headers={
                "Authorization": f"Bearer {settings.litellm_master_key}",
                **get_trace_headers(),
            },
        ) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes():
                yield chunk
