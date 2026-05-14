"""Partner chat completions service.

SPEC-API-001 TASK-008/009:
- Retrieve context from retrieval-api
- Forward to LiteLLM for non-streaming and streaming completions
- Build augmented system prompt with retrieved chunks

SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-02: the grounded system prompt is
imported from the shared library ``klai-chat-prompts``. Do NOT inline
the prompt here — both this service and ``klai-retrieval-api``'s
``services/synthesis.py`` MUST load the same constant. A CI lint
asserts no service contains a hardcoded copy.

REQ-07 wires a passive ``lingua``-based language detector on both the
user query and the model response so VictoriaLogs gets
``language_correctness`` per ``chat_synthesis_complete`` event.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncGenerator
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
import structlog
from klai_chat_prompts import GROUNDED_CHAT_SYSTEM_PROMPT

from app.core.config import Settings
from app.trace import get_trace_headers
from app.utils.language_detect import (
    detect_language,
    language_correctness,
)

logger = structlog.get_logger()

_MARKDOWN_LINK_RE = re.compile(r"!?\[([^\]]*)\]\(([^)]*)\)")
_BARE_CITATION_RE = re.compile(r"(?<!!)\[(\d+)\](?!\()")
_CITATION_LINK_RE = re.compile(r"\[(\d+)\]\(([^)]*)\)")
_RAW_URL_RE = re.compile(r"https?://[^\s<>)]+")
_EMPTY_PARENS_RE = re.compile(r"\s*\(\s*\)")
_STREAM_GUARD_TAIL_CHARS = 16


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


def _emit_language_correctness_log(
    *,
    org_id: int | str | None,
    query: str,
    response_text: str,
) -> None:
    """Emit chat_synthesis_complete with passive language metrics.

    SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-07. Failure-safe: any exception
    inside detection MUST NOT block the chat completion path.
    """
    try:
        query_lang = detect_language(query)
        response_lang = detect_language(response_text)
        correct = language_correctness(query_lang, response_lang)
        logger.info(
            "chat_synthesis_complete",
            org_id=org_id,
            query_language_detected=query_lang,
            response_language_detected=response_lang,
            language_correctness=correct,
            response_length_chars=len(response_text or ""),
            service="portal-api",
        )
    except Exception:
        logger.warning("chat_synthesis_language_log_failed", exc_info=True)


def _normalise_guard_url(url: object) -> str:
    if not isinstance(url, str):
        return ""
    value = url.strip().strip("<>")
    if value.lower() in {"", "undefined", "null", "none"}:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return urlunparse((parsed.scheme.lower(), netloc, parsed.path or "/", "", parsed.query, ""))


def _source_url_key(url: object) -> str:
    normalised = _normalise_guard_url(url)
    if not normalised:
        return ""
    parsed = urlparse(normalised)
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", parsed.query, ""))


def _chunk_source_url(chunk: dict) -> str:
    candidates = (
        chunk.get("source_url"),
        chunk.get("url"),
        chunk.get("sourceUrl"),
        chunk.get("canonical_url"),
        chunk.get("page_url"),
    )
    for candidate in candidates:
        normalised = _normalise_guard_url(candidate)
        if normalised:
            return normalised

    metadata = chunk.get("metadata")
    if isinstance(metadata, dict):
        for key in ("source_url", "url", "sourceUrl", "canonical_url", "page_url"):
            normalised = _normalise_guard_url(metadata.get(key))
            if normalised:
                return normalised

    source = chunk.get("source")
    if isinstance(source, dict):
        normalised = _normalise_guard_url(source.get("url") or source.get("source_url"))
        if normalised:
            return normalised

    return ""


def _source_urls_from_chunks(chunks: list[dict]) -> set[str]:
    return {normalised for normalised in (_chunk_source_url(chunk) for chunk in chunks) if normalised}


def _citation_source_urls_from_chunks(chunks: list[dict]) -> dict[int, str]:
    citation_urls: dict[int, str] = {}
    first_url_by_key: dict[str, str] = {}
    for index, chunk in enumerate(chunks, 1):
        source_url = _chunk_source_url(chunk)
        key = _source_url_key(source_url)
        if not source_url or not key:
            continue
        first_url_by_key.setdefault(key, source_url)
        citation_urls[index] = first_url_by_key[key]
    return citation_urls


def _citation_url_for_label(label: str, citation_source_urls: dict[int, str]) -> str:
    label = label.strip()
    if not label.isdigit():
        return ""
    return _normalise_guard_url(citation_source_urls.get(int(label), ""))


def _format_citation_label(label: str, citation_source_urls: dict[int, str]) -> str:
    label = label.strip()
    url = _citation_url_for_label(label, citation_source_urls)
    if not url:
        if label.isdigit():
            return f"[{label}]"
        return label
    return f"[{label}]({url})"


def _dedupe_adjacent_citation_links(text: str) -> str:
    output: list[str] = []
    pos = 0

    while True:
        match = _CITATION_LINK_RE.search(text, pos)
        if not match:
            output.append(text[pos:])
            return "".join(output)

        output.append(text[pos : match.start()])
        kept: list[str] = []
        seen_urls: set[str] = set()
        current = match
        run_end = match.end()

        while current:
            url_key = _source_url_key(current.group(2))
            if url_key and url_key not in seen_urls:
                kept.append(current.group(0))
                seen_urls.add(url_key)
            run_end = current.end()

            separator_start = run_end
            separator_end = separator_start
            while separator_end < len(text) and text[separator_end] in " \t\r\n,;":
                separator_end += 1
            next_match = _CITATION_LINK_RE.match(text, separator_end)
            if not next_match:
                break
            current = next_match

        output.append(" ".join(kept))
        pos = run_end


def _parse_bare_citation_run(
    buffer: str,
    *,
    citation_source_urls: dict[int, str],
    final: bool,
) -> tuple[str, str, bool] | None:
    pos = 0
    labels: list[str] = []
    separators: list[str] = []

    while True:
        match = _BARE_CITATION_RE.match(buffer, pos)
        if not match:
            break
        labels.append(match.group(1))
        pos = match.end()
        sep_start = pos
        while pos < len(buffer) and buffer[pos] in " \t\r\n,;":
            pos += 1
        separators.append(buffer[sep_start:pos])

    if not labels:
        return None
    if not final and pos >= len(buffer):
        return "", buffer, False
    if not final and separators and separators[-1]:
        return "", buffer, False

    kept: list[str] = []
    seen_urls: set[str] = set()
    changed = False
    for label in labels:
        url = _citation_url_for_label(label, citation_source_urls)
        url_key = _source_url_key(url)
        if url_key:
            if url_key in seen_urls:
                changed = True
                continue
            seen_urls.add(url_key)
        kept.append(_format_citation_label(label, citation_source_urls))
    return " ".join(kept), buffer[pos:], changed


def _sanitize_kb_markdown_output(
    text: str,
    *,
    allowed_source_urls: set[str],
    citation_source_urls: dict[int, str] | None = None,
) -> tuple[str, int]:
    """Remove source links that were not present in retrieved chunk metadata."""
    citation_source_urls = citation_source_urls or {}
    allowed_source_urls = {
        normalised
        for normalised in (_normalise_guard_url(url) for url in (*allowed_source_urls, *citation_source_urls.values()))
        if normalised
    }
    changed = 0

    def _replace_link(match: re.Match[str]) -> str:
        nonlocal changed
        marker = match.group(0)
        label = match.group(1)
        url = _normalise_guard_url(match.group(2))
        citation_url = _citation_url_for_label(label, citation_source_urls)
        if marker.startswith("!"):
            changed += 1
            return label or "[image unavailable in knowledge base]"
        if citation_url:
            if url != citation_url:
                changed += 1
            return _format_citation_label(label, citation_source_urls)
        if url in allowed_source_urls:
            return marker
        changed += 1
        if label.strip().isdigit():
            return _format_citation_label(label, citation_source_urls)
        return label

    def _replace_raw_url(match: re.Match[str]) -> str:
        nonlocal changed
        raw = match.group(0)
        url = raw.rstrip(".,;:")
        suffix = raw[len(url) :]
        if _normalise_guard_url(url) in allowed_source_urls:
            return raw
        changed += 1
        return suffix

    sanitized = _MARKDOWN_LINK_RE.sub(_replace_link, text)
    sanitized = _BARE_CITATION_RE.sub(
        lambda match: _format_citation_label(match.group(1), citation_source_urls),
        sanitized,
    )
    before_dedupe = sanitized
    sanitized = _dedupe_adjacent_citation_links(sanitized)
    if sanitized != before_dedupe:
        changed += 1
    sanitized = _RAW_URL_RE.sub(_replace_raw_url, sanitized)
    sanitized = _EMPTY_PARENS_RE.sub("", sanitized)
    return sanitized, changed


def _earliest_guard_start(text: str) -> int:
    starts = [
        idx
        for idx in (
            text.find("["),
            text.find("!["),
            text.find("http://"),
            text.find("https://"),
        )
        if idx >= 0
    ]
    return min(starts) if starts else -1


def _pop_sanitized_stream_text(  # noqa: C901 - small streaming state machine
    buffer: str,
    *,
    allowed_source_urls: set[str],
    citation_source_urls: dict[int, str] | None = None,
    final: bool,
) -> tuple[str, str, int]:
    """Return safe text to stream now, retaining incomplete link/URL tails."""
    citation_source_urls = citation_source_urls or {}
    allowed_source_urls = {
        normalised
        for normalised in (_normalise_guard_url(url) for url in (*allowed_source_urls, *citation_source_urls.values()))
        if normalised
    }
    out: list[str] = []
    changed = 0

    while buffer:
        start = _earliest_guard_start(buffer)
        if start < 0:
            if final:
                out.append(buffer)
                return "".join(out), "", changed
            if len(buffer) <= _STREAM_GUARD_TAIL_CHARS:
                return "".join(out), buffer, changed
            safe_len = len(buffer) - _STREAM_GUARD_TAIL_CHARS
            out.append(buffer[:safe_len])
            buffer = buffer[safe_len:]
            return "".join(out), buffer, changed

        if start > 0:
            if start > 1 and buffer[start - 1] == "(" and _RAW_URL_RE.match(buffer[start:]):
                prefix_end = start - 1
                if prefix_end > 0 and buffer[prefix_end - 1].isspace():
                    prefix_end -= 1
                out.append(buffer[:prefix_end])
                buffer = buffer[start - 1 :]
                continue
            if start == 1 and buffer.startswith("(") and _RAW_URL_RE.match(buffer[1:]):
                pass
            else:
                out.append(buffer[:start])
                buffer = buffer[start:]
                continue

        if buffer.startswith("("):
            raw_match = _RAW_URL_RE.match(buffer[1:])
            if raw_match:
                raw = raw_match.group(0)
                close_idx = 1 + len(raw)
                if not final and close_idx >= len(buffer):
                    return "".join(out), buffer, changed
                if close_idx < len(buffer) and buffer[close_idx] == ")":
                    url = raw.rstrip(".,;:")
                    if _normalise_guard_url(url) in allowed_source_urls:
                        out.append(buffer[: close_idx + 1])
                    else:
                        changed += 1
                        if close_idx + 1 < len(buffer) and buffer[close_idx + 1] in ".,;:" and out:
                            out[-1] = out[-1].rstrip()
                    buffer = buffer[close_idx + 1 :]
                    continue
                out.append("(")
                buffer = buffer[1:]
                continue

        link_match = _MARKDOWN_LINK_RE.match(buffer)
        if link_match:
            marker = link_match.group(0)
            label = link_match.group(1)
            url = _normalise_guard_url(link_match.group(2))
            citation_url = _citation_url_for_label(label, citation_source_urls)
            if marker.startswith("!"):
                out.append(label or "[image unavailable in knowledge base]")
                changed += 1
            elif citation_url:
                out.append(_format_citation_label(label, citation_source_urls))
                if url != citation_url:
                    changed += 1
            elif url in allowed_source_urls:
                out.append(marker)
            else:
                out.append(_format_citation_label(label, citation_source_urls) if label.strip().isdigit() else label)
                changed += 1
            buffer = buffer[len(marker) :]
            continue

        if buffer.startswith("![") or buffer.startswith("["):
            citation_run = _parse_bare_citation_run(
                buffer,
                citation_source_urls=citation_source_urls,
                final=final,
            )
            if citation_run is not None:
                replacement, buffer, citation_changed = citation_run
                if replacement:
                    out.append(replacement)
                if citation_changed:
                    changed += 1
                if not replacement and buffer:
                    return "".join(out), buffer, changed
                continue

            end = buffer.find("]")
            if end < 0:
                if final:
                    out.append(buffer)
                    return "".join(out), "", changed
                return "".join(out), buffer, changed
            if not final and len(buffer) == end + 1:
                return "".join(out), buffer, changed
            if len(buffer) > end + 1 and buffer[end + 1] == "(":
                if final:
                    label = buffer[2:end] if buffer.startswith("![") else buffer[1:end]
                    out.append(_format_citation_label(label, citation_source_urls))
                    buffer = buffer[end + 1 :]
                    continue
                return "".join(out), buffer, changed
            if buffer.startswith("!["):
                out.append(buffer[: end + 1])
            else:
                label = buffer[1:end]
                out.append(_format_citation_label(label, citation_source_urls))
            buffer = buffer[end + 1 :]
            continue

        raw_match = _RAW_URL_RE.match(buffer)
        if raw_match:
            raw = raw_match.group(0)
            if not final and len(raw) == len(buffer):
                return "".join(out), buffer, changed
            url = raw.rstrip(".,;:")
            suffix = raw[len(url) :]
            if _normalise_guard_url(url) in allowed_source_urls:
                out.append(raw)
            else:
                out.append(suffix)
                changed += 1
            buffer = buffer[len(raw) :]
            continue

        if final:
            out.append(buffer[0])
            buffer = buffer[1:]
            continue
        return "".join(out), buffer, changed

    return "".join(out), "", changed


def _sanitize_completion_body(
    body: dict,
    *,
    allowed_source_urls: set[str],
    citation_source_urls: dict[int, str] | None = None,
) -> int:
    changed = 0
    for choice in body.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        sanitized, content_changed = _sanitize_kb_markdown_output(
            content,
            allowed_source_urls=allowed_source_urls,
            citation_source_urls=citation_source_urls,
        )
        if content_changed:
            message["content"] = sanitized
            changed += content_changed
    return changed


def _sse_content_delta(text: str) -> bytes:
    payload = {"choices": [{"delta": {"content": text}}]}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def _build_system_prompt(
    chunks: list[dict],
    original_system: str | None = None,
    widget_system_prompt: str | None = None,
) -> str:
    """Build a grounded system prompt augmented with retrieved context chunks."""
    base = original_system or GROUNDED_CHAT_SYSTEM_PROMPT
    widget_system_prompt = (widget_system_prompt or "").strip()
    if widget_system_prompt:
        base = (
            f"{base}\n\n"
            "[Widget behaviour instructions: apply these to tone, persona, "
            "scope, and escalation style. They do not override the source URL "
            "rules below.]\n"
            f"{widget_system_prompt}"
        )

    if not chunks:
        return base

    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        text = chunk.get("text", "")
        if text:
            title = (
                chunk.get("title")
                or (chunk.get("metadata") or {}).get("title")
                or chunk.get("source_label")
                or "Knowledge Base"
            )
            source_url = _chunk_source_url(chunk)
            source_line = f"\nsource_url: {source_url}" if source_url else ""
            context_parts.append(f"[{i}] title: {title}{source_line}\n{text}")

    if not context_parts:
        return base

    context_block = "\n\n".join(context_parts)
    url_guard = (
        "URL rules for citations and source links:\n"
        "- Use only literal source_url values shown in the context below.\n"
        "- Copy source_url values exactly; do not invent, rewrite, or guess URLs.\n"
        "- If a cited chunk has no source_url, cite it as [n] without adding a link.\n"
        "- Never turn a title, heading, or documentation phrase into a URL.\n"
    )
    return f"{base}\n\n{url_guard}\nContext:\n{context_block}"


async def retrieve_context(
    org_id: int,
    zitadel_org_id: str,
    kb_slugs: list[str],
    messages: list[dict],
    settings: Settings,
    *,
    partner_user_id: str | None = None,
    widget_system_prompt: str | None = None,
) -> tuple[list[dict], str]:
    """Call retrieval-api and return (chunks, augmented_system_prompt).

    Follows the pattern from deploy/litellm/klai_knowledge.py.

    ``partner_user_id`` (F2 audit cleanup, 2026-05-06): when given, attached
    to the /retrieve body as ``user_id``. retrieval-api recognizes the
    ``partner:`` prefix and pins ``verified_caller`` for product_events
    integrity (SPEC-SEC-IDENTITY-ASSERT-001 REQ-6) without a round-trip
    to portal-api's /internal/identity/verify (which would 403 on the
    synthetic identity). Without this, ``knowledge.queried`` events for
    partner traffic are silently dropped via the
    ``product_event_skipped_no_identity`` warning branch in retrieve.py.
    Audit ref: .moai/audits/retrieval-coupling-2026-05-06/findings/F2-...md.
    """
    query = _last_user_message(messages)
    if not query:
        return [], _build_system_prompt([], widget_system_prompt=widget_system_prompt)

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
    if partner_user_id is not None:
        # F2: synthetic partner-level identity for product_events tagging.
        retrieve_body["user_id"] = partner_user_id

    retrieval_url = settings.knowledge_retrieve_url
    if not retrieval_url:
        logger.warning("partner_chat_no_retrieval_url")
        return [], _build_system_prompt([], original_system, widget_system_prompt)

    # SPEC-SEC-010 REQ-6.1: authenticate to retrieval-api with the dedicated
    # retrieval_api_internal_secret (separate from portal-api's mailer secret).
    # SPEC-SEC-IDENTITY-ASSERT-001 REQ-4.2: X-Caller-Service is REQUIRED;
    # without it retrieval-api returns 400 missing_caller_service. Phase D
    # landed 2026-04-28 and silently broke partner chat for 7 days because
    # the header was never added here. See pitfalls →
    # retrieve-caller-service-header-mismatch.
    retrieval_secret = settings.retrieval_api_internal_secret or settings.internal_secret
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{retrieval_url}/retrieve",
            json=retrieve_body,
            headers={
                "X-Internal-Secret": retrieval_secret,
                "X-Caller-Service": "portal-api",
                **get_trace_headers(),
            },
        )
        resp.raise_for_status()
        result = resp.json()

    chunks = result.get("chunks", [])
    system_prompt = _build_system_prompt(chunks, original_system, widget_system_prompt)

    return chunks, system_prompt


def _extract_completion_text(body: dict) -> str:
    """Pull the assistant text out of a LiteLLM /v1/chat/completions
    response body. Returns "" if the body shape is unexpected — callers
    use this only for observability, never for user-visible behaviour.
    """
    try:
        choice = body["choices"][0]
        message = choice.get("message") or {}
        text = message.get("content")
        return text if isinstance(text, str) else ""
    except (KeyError, IndexError, TypeError):
        return ""


async def chat_completion_non_streaming(
    messages: list[dict],
    model: str,
    temperature: float,
    system_prompt: str,
    settings: Settings,
    *,
    org_id: int | str | None = None,
    allowed_source_urls: set[str] | None = None,
    citation_source_urls: dict[int, str] | None = None,
) -> dict:
    """Forward to LiteLLM and return complete response as dict.

    POST to litellm with stream=false. Emits the
    ``chat_synthesis_complete`` log event before returning so
    cross-lingual correctness is observable on every call (REQ-07).
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
        body = resp.json()

    allowed_source_urls = allowed_source_urls or set()
    citation_source_urls = citation_source_urls or {}
    stripped_links = _sanitize_completion_body(
        body,
        allowed_source_urls=allowed_source_urls,
        citation_source_urls=citation_source_urls,
    )
    if stripped_links:
        logger.warning(
            "partner_chat_unretrieved_links_stripped",
            org_id=org_id,
            stripped_links=stripped_links,
        )

    # Passive language-correctness telemetry (SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-07).
    user_query = _last_user_message(messages) or ""
    response_text = _extract_completion_text(body)
    _emit_language_correctness_log(
        org_id=org_id,
        query=user_query,
        response_text=response_text,
    )
    return body


async def chat_completion_streaming(
    messages: list[dict],
    model: str,
    temperature: float,
    system_prompt: str,
    settings: Settings,
    *,
    org_id: int | str | None = None,
    allowed_source_urls: set[str] | None = None,
    citation_source_urls: dict[int, str] | None = None,
) -> AsyncGenerator[bytes]:
    """Stream LiteLLM SSE response with KB-source URL sanitization.

    POST to LiteLLM with stream=true, yield sanitized content deltas. Collects
    the streamed assistant text alongside the byte forwarding so the
    ``chat_synthesis_complete`` log event (REQ-07) gets the full
    response text even though we never buffer it for the client.
    """

    augmented_messages = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        if msg.get("role") != "system":
            augmented_messages.append(msg)

    litellm_url = settings.litellm_base_url
    user_query = _last_user_message(messages) or ""
    allowed_source_urls = allowed_source_urls or set()
    citation_source_urls = citation_source_urls or {}
    collected_text_parts: list[str] = []
    pending_text = ""
    stripped_links = 0

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
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if not payload:
                    continue
                if payload == "[DONE]":
                    safe_text, pending_text, changed = _pop_sanitized_stream_text(
                        pending_text,
                        allowed_source_urls=allowed_source_urls,
                        citation_source_urls=citation_source_urls,
                        final=True,
                    )
                    stripped_links += changed
                    if safe_text:
                        collected_text_parts.append(safe_text)
                        yield _sse_content_delta(safe_text)
                    yield b"data: [DONE]\n\n"
                    continue
                try:
                    evt: dict[str, Any] = json.loads(payload)
                except json.JSONDecodeError:
                    logger.debug("partner_chat_sse_parse_skipped", exc_info=True)
                    continue
                delta = (evt.get("choices") or [{}])[0].get("delta") or {}
                text = delta.get("content")
                if not isinstance(text, str) or not text:
                    continue
                pending_text += text
                safe_text, pending_text, changed = _pop_sanitized_stream_text(
                    pending_text,
                    allowed_source_urls=allowed_source_urls,
                    citation_source_urls=citation_source_urls,
                    final=False,
                )
                stripped_links += changed
                if safe_text:
                    collected_text_parts.append(safe_text)
                    yield _sse_content_delta(safe_text)

    if pending_text:
        safe_text, pending_text, changed = _pop_sanitized_stream_text(
            pending_text,
            allowed_source_urls=allowed_source_urls,
            citation_source_urls=citation_source_urls,
            final=True,
        )
        stripped_links += changed
        if safe_text:
            collected_text_parts.append(safe_text)
            yield _sse_content_delta(safe_text)

    if stripped_links:
        logger.warning(
            "partner_chat_unretrieved_links_stripped",
            org_id=org_id,
            stripped_links=stripped_links,
        )

    _emit_language_correctness_log(
        org_id=org_id,
        query=user_query,
        response_text="".join(collected_text_parts),
    )
