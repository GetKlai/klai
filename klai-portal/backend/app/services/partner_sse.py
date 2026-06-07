"""Chat-completion wire decoders for the partner/widget API.

Two pure decoders extracted from ``app/api/partner.py``: pull ``content`` text +
``sources`` out of an SSE ``data:`` block (streaming) and out of a non-streaming
result dict / Pydantic-shaped object. No auth, DB, or request coupling.
``app.api.partner`` re-imports both — the audit streaming wrapper and the
non-streaming chat branch call them — so the call sites are unchanged.
"""

from __future__ import annotations

import json
from typing import Any


def _parse_audit_sse_chunk(chunk: bytes) -> tuple[str | None, list[dict] | None]:
    """Pull ``delta.content`` text and ``delta.sources`` list out of one
    SSE ``data: …\\n\\n`` block. Returns (text, sources) — either side
    may be None when the chunk doesn't carry that field."""
    text_part: str | None = None
    src_part: list[dict] | None = None
    for raw in chunk.split(b"\n"):
        if not raw.startswith(b"data: "):
            continue
        payload = raw[6:].strip()
        if payload in (b"", b"[DONE]"):
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        delta = (event.get("choices") or [{}])[0].get("delta") or {}
        if isinstance(delta.get("content"), str):
            text_part = (text_part or "") + delta["content"]
        srcs = delta.get("sources")
        if isinstance(srcs, list):
            src_part = [s for s in srcs if isinstance(s, dict)]
    return text_part, src_part


def _extract_assistant_text_and_sources(
    result: Any,
) -> tuple[str, list[dict] | None]:
    """Pull the assistant message + sources out of a non-streaming
    chat-completions result (a dict or Pydantic-shaped object)."""
    payload: dict[str, Any]
    if hasattr(result, "model_dump"):
        payload = result.model_dump()
    elif isinstance(result, dict):
        payload = result
    else:
        return "", None
    choices = payload.get("choices") or []
    if not choices:
        return "", None
    message = choices[0].get("message") or {}
    content = str(message.get("content") or "")
    sources_raw = message.get("sources")
    sources = [s for s in sources_raw if isinstance(s, dict)] if isinstance(sources_raw, list) else None
    return content, sources
