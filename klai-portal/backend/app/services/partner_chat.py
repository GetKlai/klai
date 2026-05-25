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
from typing import Any, Literal
from urllib.parse import urlparse, urlunparse

import httpx
import structlog
from klai_chat_prompts import GROUNDED_CHAT_SYSTEM_PROMPT

from app.core.config import Settings
from app.services.citations import (
    compose_answer_with_trusted_sources,
    evidence_pack_items_as_chunks,
    render_evidence_context,
    trusted_sources_from_evidence_pack,
)
from app.trace import get_trace_headers
from app.utils.language_detect import (
    detect_language,
    language_correctness,
)

logger = structlog.get_logger()

_MARKDOWN_LINK_RE = re.compile(r"!?\[([^\]]*)\]\(([^)]*)\)")
_BARE_CITATION_RE = re.compile(r"(?<!!)\[(\d+)\](?!\()")
_BARE_CITATION_NUMBER_RUN_RE = re.compile(r"(?<![\w/\]\)])(\d{1,3}(?:\s*[,;]\s*\d{1,3})+)(?=(?:[.!?])?(?:\s|$))")
_CITATION_LINK_RE = re.compile(r"\[(\d+)\]\(([^)]*)\)")
_MALFORMED_CITATION_LINK_RE = re.compile(r"(?<!\[)\b(\d+)\((https?://[^)\s]+)\)")
_RAW_URL_RE = re.compile(r"https?://[^\s<>)]+")
_EMPTY_PARENS_RE = re.compile(r"\s*\(\s*\)")
_CITATION_MARKER_RE = re.compile(r"\((\d+)\)")
_STREAM_GUARD_TAIL_CHARS = 32

CitationOutput = Literal["links", "markers"]


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


def _normalize_llm_message(message: dict) -> dict[str, str] | None:
    """Keep only provider-supported chat message fields."""
    role = message.get("role")
    if role not in ("user", "assistant"):
        return None

    content = message.get("content")
    if isinstance(content, str):
        return {"role": role, "content": content}
    if isinstance(content, list):
        text = " ".join(
            part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"
        ).strip()
        if text:
            return {"role": role, "content": text}
    return None


def _build_conversation_history(messages: list[dict]) -> list[dict]:
    """Return up to the last 6 turns (3 exchanges), excluding the last user message."""
    history = [msg for m in messages[:-1] if (msg := _normalize_llm_message(m)) is not None]
    return history[-6:]


def _augment_messages_with_system_prompt(messages: list[dict], system_prompt: str) -> list[dict]:
    normalized = [msg for m in messages if (msg := _normalize_llm_message(m)) is not None]
    return [{"role": "system", "content": system_prompt}, *normalized]


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
    hostname = (parsed.hostname or "").lower()
    if hostname in {"undefined", "null", "none"}:
        return ""
    placeholder_path = (parsed.path or "").strip("/").lower()
    if placeholder_path in {"undefined", "null", "none"}:
        return ""
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
        chunk.get("source_ref"),
    )
    for candidate in candidates:
        normalised = _normalise_guard_url(candidate)
        if normalised:
            return normalised

    metadata = chunk.get("metadata")
    if isinstance(metadata, dict):
        for key in ("source_url", "url", "sourceUrl", "canonical_url", "page_url", "source_ref"):
            normalised = _normalise_guard_url(metadata.get(key))
            if normalised:
                return normalised

    source = chunk.get("source")
    if isinstance(source, dict):
        for key in ("url", "source_url", "href"):
            normalised = _normalise_guard_url(source.get(key))
            if normalised:
                return normalised

    return ""


def _source_urls_from_chunks(chunks: list[dict]) -> set[str]:
    return {normalised for normalised in (_chunk_source_url(chunk) for chunk in chunks) if normalised}


def _chunk_source_title(chunk: dict) -> str:
    candidates = (
        chunk.get("title"),
        (chunk.get("metadata") or {}).get("title") if isinstance(chunk.get("metadata"), dict) else None,
        chunk.get("source_label"),
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return "Source"


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


def _citation_source_metadata_from_chunks(chunks: list[dict]) -> dict[str, dict[str, str]]:
    sources: dict[str, dict[str, str]] = {}
    for chunk in chunks:
        source_url = _chunk_source_url(chunk)
        key = _source_url_key(source_url)
        if not source_url or not key or key in sources:
            continue
        sources[key] = {"url": source_url, "title": _chunk_source_title(chunk)}
    return sources


def _citation_url_for_label(label: str, citation_source_urls: dict[int, str]) -> str:
    label = label.strip()
    if not label.isdigit():
        return ""
    return _normalise_guard_url(citation_source_urls.get(int(label), ""))


def _citation_display_label(url: str, citation_source_urls: dict[int, str]) -> str:
    """Map a chunk citation URL to a stable document-level display number."""
    url_key = _source_url_key(url)
    if not url_key:
        return ""

    seen: dict[str, int] = {}
    for source_url in citation_source_urls.values():
        source_key = _source_url_key(source_url)
        if source_key and source_key not in seen:
            seen[source_key] = len(seen) + 1
        if source_key == url_key:
            return str(seen[source_key])
    return ""


def _format_citation_label(
    label: str,
    citation_source_urls: dict[int, str],
    display_label: str | None = None,
    *,
    citation_output: CitationOutput = "links",
) -> str:
    label = label.strip()
    url = _citation_url_for_label(label, citation_source_urls)
    if not url:
        if label.isdigit():
            if citation_output == "markers":
                return f"({label})"
            return f"[{label}]"
        return label
    visible_label = display_label or _citation_display_label(url, citation_source_urls) or label
    if citation_output == "markers":
        return f"({visible_label})"
    return f"[{visible_label}]({url})"


def _format_citation_marker(label: str) -> str:
    return f"({label.strip()})"


def _join_formatted_citations(citations: list[str], *, citation_output: CitationOutput) -> str:
    if citation_output != "markers":
        return ", ".join(citations)

    labels: list[str] = []
    for citation in citations:
        match = _CITATION_MARKER_RE.fullmatch(citation)
        if not match:
            return ", ".join(citations)
        labels.append(match.group(1))
    return f"({','.join(labels)})" if labels else ""


def _format_bare_number_citation_run(
    labels: list[str],
    *,
    citation_source_urls: dict[int, str],
    emitted_source_keys: set[str],
    emitted_source_key_order: list[str] | None = None,
    citation_output: CitationOutput,
) -> tuple[str, bool]:
    if len(labels) < 2:
        return "", False

    kept: list[str] = []
    seen_urls: set[str] = set()

    for label in labels:
        url = _citation_url_for_label(label, citation_source_urls)
        url_key = _source_url_key(url)
        if not url_key:
            return "", False
        if url_key in seen_urls or url_key in emitted_source_keys:
            continue
        display_label = str(len(emitted_source_keys) + 1)
        seen_urls.add(url_key)
        _record_emitted_source_key(url_key, emitted_source_keys, emitted_source_key_order)
        kept.append(_format_citation_label(label, citation_source_urls, display_label, citation_output=citation_output))

    return _join_formatted_citations(kept, citation_output=citation_output), True


def _sanitize_bare_number_citation_runs(
    text: str,
    *,
    citation_source_urls: dict[int, str],
    emitted_source_keys: set[str],
    emitted_source_key_order: list[str] | None = None,
    citation_output: CitationOutput,
) -> tuple[str, int]:
    changed = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal changed
        original = match.group(0)
        labels = re.findall(r"\d+", match.group(1))
        replacement, citation_changed = _format_bare_number_citation_run(
            labels,
            citation_source_urls=citation_source_urls,
            emitted_source_keys=emitted_source_keys,
            emitted_source_key_order=emitted_source_key_order,
            citation_output=citation_output,
        )
        if not replacement:
            return original
        if citation_changed or replacement != original:
            changed += 1
        return replacement

    return _BARE_CITATION_NUMBER_RUN_RE.sub(_replace, text), changed


def _record_emitted_source_key(
    url_key: str,
    emitted_source_keys: set[str],
    emitted_source_key_order: list[str] | None = None,
) -> bool:
    if not url_key or url_key in emitted_source_keys:
        return False
    emitted_source_keys.add(url_key)
    if emitted_source_key_order is not None:
        emitted_source_key_order.append(url_key)
    return True


def _source_payload_from_keys(
    source_keys: list[str],
    citation_source_metadata: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    for index, source_key in enumerate(source_keys, 1):
        metadata = citation_source_metadata.get(source_key) or {}
        url = _normalise_guard_url(metadata.get("url") or source_key)
        if not url:
            continue
        title = (metadata.get("title") or "").strip() or "Source"
        sources.append({"label": str(index), "title": title, "url": url})
    return sources


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

        output.append(", ".join(kept))
        pos = run_end


def _dedupe_adjacent_citation_markers(text: str) -> str:
    output: list[str] = []
    pos = 0

    while True:
        match = _CITATION_MARKER_RE.search(text, pos)
        if not match:
            output.append(text[pos:])
            return "".join(output)

        output.append(text[pos : match.start()])
        labels: list[str] = []
        seen: set[str] = set()
        current = match
        run_end = match.end()

        while current:
            label = current.group(1)
            if label not in seen:
                labels.append(label)
                seen.add(label)
            run_end = current.end()

            separator_start = run_end
            separator_end = separator_start
            while separator_end < len(text) and text[separator_end] in " \t\r\n,;":
                separator_end += 1
            next_match = _CITATION_MARKER_RE.match(text, separator_end)
            if not next_match:
                break
            current = next_match

        output.append(f"({','.join(labels)})")
        pos = run_end


def _dedupe_repeated_citation_links(text: str, emitted_source_keys: set[str] | None = None) -> str:
    """Keep only the first citation link per source URL in a rendered answer."""
    emitted_source_keys = emitted_source_keys if emitted_source_keys is not None else set()
    output: list[str] = []
    pos = 0

    for match in _CITATION_LINK_RE.finditer(text):
        output.append(text[pos : match.start()])
        url_key = _source_url_key(match.group(2))
        if not url_key or url_key not in emitted_source_keys:
            output.append(match.group(0))
            if url_key:
                emitted_source_keys.add(url_key)
        pos = match.end()

    output.append(text[pos:])
    return re.sub(r"[ \t]+([.,;:])", r"\1", "".join(output))


def _parse_bare_citation_run(
    buffer: str,
    *,
    citation_source_urls: dict[int, str],
    final: bool,
    emitted_source_keys: set[str] | None = None,
    emitted_source_key_order: list[str] | None = None,
    citation_output: CitationOutput = "links",
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
    emitted_source_keys = emitted_source_keys if emitted_source_keys is not None else set()
    for label in labels:
        url = _citation_url_for_label(label, citation_source_urls)
        url_key = _source_url_key(url)
        display_label: str | None = None
        if url_key:
            if url_key in seen_urls or url_key in emitted_source_keys:
                changed = True
                continue
            display_label = str(len(emitted_source_keys) + 1)
            seen_urls.add(url_key)
            _record_emitted_source_key(url_key, emitted_source_keys, emitted_source_key_order)
        kept.append(_format_citation_label(label, citation_source_urls, display_label, citation_output=citation_output))
    return _join_formatted_citations(kept, citation_output=citation_output), buffer[pos:], changed


def _parse_citation_link_run(
    buffer: str,
    *,
    citation_source_urls: dict[int, str],
    allowed_source_urls: set[str],
    final: bool,
    emitted_source_keys: set[str] | None = None,
    emitted_source_key_order: list[str] | None = None,
    citation_output: CitationOutput = "links",
) -> tuple[str, str, bool] | None:
    pos = 0
    links: list[re.Match[str]] = []

    while True:
        match = _CITATION_LINK_RE.match(buffer, pos)
        if not match:
            break
        links.append(match)
        pos = match.end()
        while pos < len(buffer) and buffer[pos] in " \t\r\n,;":
            pos += 1

    if not links:
        return None
    if not final and pos >= len(buffer):
        return "", buffer, False

    kept: list[str] = []
    seen_urls: set[str] = set()
    changed = False
    emitted_source_keys = emitted_source_keys if emitted_source_keys is not None else set()

    for match in links:
        label = match.group(1)
        provided_url = _normalise_guard_url(match.group(2))
        citation_url = _citation_url_for_label(label, citation_source_urls)
        output_url = citation_url or provided_url
        url_key = _source_url_key(output_url)

        marker_url_is_allowed = output_url in allowed_source_urls
        if not citation_url and not marker_url_is_allowed:
            changed = True
            continue
        if url_key:
            if url_key in seen_urls or url_key in emitted_source_keys:
                changed = True
                continue
            display_label = str(len(emitted_source_keys) + 1)
            seen_urls.add(url_key)
            _record_emitted_source_key(url_key, emitted_source_keys, emitted_source_key_order)
        else:
            display_label = None
        if citation_url:
            if provided_url != citation_url:
                changed = True
            kept.append(
                _format_citation_label(label, citation_source_urls, display_label, citation_output=citation_output)
            )
        else:
            kept.append(
                _format_citation_marker(display_label or label)
                if citation_output == "markers"
                else f"[{label}]({output_url})"
            )

    return _join_formatted_citations(kept, citation_output=citation_output), buffer[pos:], changed


def _format_provided_citation_link(
    *,
    label: str,
    provided_url: str,
    citation_source_urls: dict[int, str],
    allowed_source_urls: set[str],
    emitted_source_keys: set[str] | None = None,
    emitted_source_key_order: list[str] | None = None,
    citation_output: CitationOutput = "links",
) -> tuple[str, bool]:
    citation_url = _citation_url_for_label(label, citation_source_urls)
    output_url = citation_url or _normalise_guard_url(provided_url)
    url_key = _source_url_key(output_url)
    changed = False
    emitted_source_keys = emitted_source_keys if emitted_source_keys is not None else set()

    if not output_url or (not citation_url and output_url not in allowed_source_urls):
        return "", True
    if url_key and url_key in emitted_source_keys:
        return "", True
    display_label = str(len(emitted_source_keys) + 1) if url_key else None
    if url_key:
        _record_emitted_source_key(url_key, emitted_source_keys, emitted_source_key_order)
    if citation_url and _normalise_guard_url(provided_url) != citation_url:
        changed = True

    if citation_url:
        return _format_citation_label(
            label,
            citation_source_urls,
            display_label,
            citation_output=citation_output,
        ), changed
    if citation_output == "markers" and label.strip().isdigit():
        return _format_citation_marker(display_label or label.strip()), changed
    return f"[{label}]({output_url})", changed


def _sanitize_kb_markdown_output(  # noqa: C901 - citation/link guard has several Markdown cases
    text: str,
    *,
    allowed_source_urls: set[str],
    citation_source_urls: dict[int, str] | None = None,
    emitted_source_keys: set[str] | None = None,
    emitted_source_key_order: list[str] | None = None,
    citation_output: CitationOutput = "links",
) -> tuple[str, int]:
    """Remove source links that were not present in retrieved chunk metadata."""
    citation_source_urls = citation_source_urls or {}
    emitted_source_keys = emitted_source_keys if emitted_source_keys is not None else set()
    allowed_source_urls = {
        normalised
        for normalised in (_normalise_guard_url(url) for url in (*allowed_source_urls, *citation_source_urls.values()))
        if normalised
    }
    changed = 0

    def _replace_malformed_citation(match: re.Match[str]) -> str:
        nonlocal changed
        replacement, citation_changed = _format_provided_citation_link(
            label=match.group(1),
            provided_url=match.group(2),
            citation_source_urls=citation_source_urls,
            allowed_source_urls=allowed_source_urls,
            emitted_source_keys=emitted_source_keys,
            emitted_source_key_order=emitted_source_key_order,
            citation_output=citation_output,
        )
        if citation_changed or replacement != match.group(0):
            changed += 1
        return replacement

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
            url_key = _source_url_key(citation_url)
            if url_key and url_key in emitted_source_keys:
                changed += 1
                return ""
            display_label = str(len(emitted_source_keys) + 1) if url_key else None
            if url_key:
                _record_emitted_source_key(url_key, emitted_source_keys, emitted_source_key_order)
            if url != citation_url:
                changed += 1
            return _format_citation_label(
                label,
                citation_source_urls,
                display_label,
                citation_output=citation_output,
            )
        if url in allowed_source_urls:
            if citation_output == "markers" and label.strip().isdigit():
                url_key = _source_url_key(url)
                if url_key and url_key in emitted_source_keys:
                    changed += 1
                    return ""
                display_label = str(len(emitted_source_keys) + 1) if url_key else label.strip()
                if url_key:
                    _record_emitted_source_key(url_key, emitted_source_keys, emitted_source_key_order)
                changed += 1
                return _format_citation_marker(display_label)
            return marker
        changed += 1
        if label.strip().isdigit():
            return _format_citation_label(label, citation_source_urls, citation_output=citation_output)
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

    def _replace_bare_citation(match: re.Match[str]) -> str:
        nonlocal changed
        label = match.group(1)
        url = _citation_url_for_label(label, citation_source_urls)
        url_key = _source_url_key(url)
        display_label: str | None = None
        if url_key:
            if url_key in emitted_source_keys:
                changed += 1
                return ""
            display_label = str(len(emitted_source_keys) + 1)
            _record_emitted_source_key(url_key, emitted_source_keys, emitted_source_key_order)
        return _format_citation_label(label, citation_source_urls, display_label, citation_output=citation_output)

    sanitized = _MALFORMED_CITATION_LINK_RE.sub(_replace_malformed_citation, text)
    sanitized = _MARKDOWN_LINK_RE.sub(_replace_link, sanitized)
    sanitized = _BARE_CITATION_RE.sub(_replace_bare_citation, sanitized)
    sanitized, bare_number_changed = _sanitize_bare_number_citation_runs(
        sanitized,
        citation_source_urls=citation_source_urls,
        emitted_source_keys=emitted_source_keys,
        emitted_source_key_order=emitted_source_key_order,
        citation_output=citation_output,
    )
    changed += bare_number_changed
    before_dedupe = sanitized
    if citation_output == "markers":
        sanitized = _dedupe_adjacent_citation_markers(sanitized)
    else:
        sanitized = _dedupe_adjacent_citation_links(sanitized)
        sanitized = _dedupe_repeated_citation_links(sanitized)
    if sanitized != before_dedupe:
        changed += 1
    sanitized = _RAW_URL_RE.sub(_replace_raw_url, sanitized)
    sanitized = _EMPTY_PARENS_RE.sub("", sanitized)
    sanitized = re.sub(r"(\[[^\]]+\]\([^)]*\)),\s{2,}", r"\1 ", sanitized)
    sanitized = re.sub(r"[ \t]+([.,;:])", r"\1", sanitized)
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
    emitted_source_keys: set[str] | None = None,
    emitted_source_key_order: list[str] | None = None,
    citation_output: CitationOutput = "links",
    final: bool,
) -> tuple[str, str, int]:
    """Return safe text to stream now, retaining incomplete link/URL tails."""
    citation_source_urls = citation_source_urls or {}
    emitted_source_keys = emitted_source_keys if emitted_source_keys is not None else set()
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
                sanitized, bare_number_changed = _sanitize_bare_number_citation_runs(
                    buffer,
                    citation_source_urls=citation_source_urls,
                    emitted_source_keys=emitted_source_keys,
                    emitted_source_key_order=emitted_source_key_order,
                    citation_output=citation_output,
                )
                out.append(sanitized)
                changed += bare_number_changed
                return "".join(out), "", changed
            if len(buffer) <= _STREAM_GUARD_TAIL_CHARS:
                return "".join(out), buffer, changed
            safe_len = len(buffer) - _STREAM_GUARD_TAIL_CHARS
            out.append(buffer[:safe_len])
            buffer = buffer[safe_len:]
            return "".join(out), buffer, changed

        if start > 0:
            if start > 1 and buffer[start - 1] == "(" and _RAW_URL_RE.match(buffer[start:]):
                label_start = start - 1
                while label_start > 0 and buffer[label_start - 1].isdigit():
                    label_start -= 1
                if label_start < start - 1:
                    raw_match = _RAW_URL_RE.match(buffer[start:])
                    raw = raw_match.group(0) if raw_match else ""
                    close_idx = start + len(raw)
                    if not final and close_idx >= len(buffer):
                        out.append(buffer[:label_start])
                        return "".join(out), buffer[label_start:], changed
                    if close_idx < len(buffer) and buffer[close_idx] == ")":
                        replacement, citation_changed = _format_provided_citation_link(
                            label=buffer[label_start : start - 1],
                            provided_url=raw,
                            citation_source_urls=citation_source_urls,
                            allowed_source_urls=allowed_source_urls,
                            emitted_source_keys=emitted_source_keys,
                            emitted_source_key_order=emitted_source_key_order,
                            citation_output=citation_output,
                        )
                        out.append(buffer[:label_start])
                        if replacement:
                            out.append(replacement)
                        if citation_changed or replacement != buffer[label_start : close_idx + 1]:
                            changed += 1
                        buffer = buffer[close_idx + 1 :]
                        continue

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
            original_buffer = buffer
            citation_link_run = _parse_citation_link_run(
                buffer,
                citation_source_urls=citation_source_urls,
                allowed_source_urls=allowed_source_urls,
                emitted_source_keys=emitted_source_keys,
                emitted_source_key_order=emitted_source_key_order,
                citation_output=citation_output,
                final=final,
            )
            if citation_link_run is not None:
                replacement, buffer, citation_changed = citation_link_run
                if replacement:
                    out.append(replacement)
                elif buffer and buffer[0] in ".,;:" and out:
                    out[-1] = out[-1].rstrip()
                if citation_changed:
                    changed += 1
                if not replacement and buffer:
                    if buffer == original_buffer:
                        return "".join(out), buffer, changed
                    continue
                continue

            marker = link_match.group(0)
            label = link_match.group(1)
            url = _normalise_guard_url(link_match.group(2))
            citation_url = _citation_url_for_label(label, citation_source_urls)
            if marker.startswith("!"):
                out.append(label or "[image unavailable in knowledge base]")
                changed += 1
            elif citation_url:
                url_key = _source_url_key(citation_url)
                if url_key and url_key in emitted_source_keys:
                    if buffer[len(marker) : len(marker) + 1] in ".,;:" and out:
                        out[-1] = out[-1].rstrip()
                    changed += 1
                else:
                    display_label = str(len(emitted_source_keys) + 1) if url_key else None
                    out.append(
                        _format_citation_label(
                            label,
                            citation_source_urls,
                            display_label,
                            citation_output=citation_output,
                        )
                    )
                    if url_key:
                        _record_emitted_source_key(url_key, emitted_source_keys, emitted_source_key_order)
                if url != citation_url:
                    changed += 1
            elif url in allowed_source_urls:
                if citation_output == "markers" and label.strip().isdigit():
                    url_key = _source_url_key(url)
                    if url_key and url_key in emitted_source_keys:
                        if buffer[len(marker) : len(marker) + 1] in ".,;:" and out:
                            out[-1] = out[-1].rstrip()
                        changed += 1
                    else:
                        display_label = str(len(emitted_source_keys) + 1) if url_key else label.strip()
                        out.append(_format_citation_marker(display_label))
                        if url_key:
                            _record_emitted_source_key(url_key, emitted_source_keys, emitted_source_key_order)
                        changed += 1
                else:
                    out.append(marker)
            else:
                out.append(
                    _format_citation_label(label, citation_source_urls, citation_output=citation_output)
                    if label.strip().isdigit()
                    else label
                )
                changed += 1
            buffer = buffer[len(marker) :]
            continue

        if buffer.startswith("![") or buffer.startswith("["):
            original_buffer = buffer
            citation_run = _parse_bare_citation_run(
                buffer,
                citation_source_urls=citation_source_urls,
                emitted_source_keys=emitted_source_keys,
                emitted_source_key_order=emitted_source_key_order,
                citation_output=citation_output,
                final=final,
            )
            if citation_run is not None:
                replacement, buffer, citation_changed = citation_run
                if replacement:
                    out.append(replacement)
                elif buffer and buffer[0] in ".,;:" and out:
                    out[-1] = out[-1].rstrip()
                if citation_changed:
                    changed += 1
                if not replacement and buffer:
                    if buffer == original_buffer:
                        return "".join(out), buffer, changed
                    continue
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
                    out.append(_format_citation_label(label, citation_source_urls, citation_output=citation_output))
                    buffer = buffer[end + 1 :]
                    continue
                return "".join(out), buffer, changed
            if buffer.startswith("!["):
                out.append(buffer[: end + 1])
            else:
                label = buffer[1:end]
                out.append(_format_citation_label(label, citation_source_urls, citation_output=citation_output))
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
    emitted_source_key_order: list[str] | None = None,
    citation_output: CitationOutput = "links",
) -> int:
    changed = 0
    emitted_source_keys: set[str] = set()
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
            emitted_source_keys=emitted_source_keys,
            emitted_source_key_order=emitted_source_key_order,
            citation_output=citation_output,
        )
        if content_changed:
            message["content"] = sanitized
            changed += content_changed
    return changed


def _sse_content_delta(text: str) -> bytes:
    payload = {"choices": [{"delta": {"content": text}}]}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def _sse_sources_delta(sources: list[dict[str, str]]) -> bytes:
    payload = {"choices": [{"delta": {"sources": sources}}]}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def _no_citable_sources_message(user_query: str) -> str:
    dutch_markers = {"wat", "waar", "welke", "hoe", "waarom", "gegevens", "bronnen", "klopt"}
    query_tokens = {token.lower() for token in re.findall(r"[a-zA-ZÀ-ÿ]+", user_query)}
    if query_tokens & dutch_markers:
        return "Ik kan dit niet betrouwbaar beantwoorden op basis van de beschikbare kennisbronnen."
    return "I cannot answer this reliably from the available knowledge sources."


def _compose_backend_managed_answer(
    text: str,
    trusted_sources: list[dict[str, Any]] | None,
    user_query: str,
) -> tuple[str, list[dict]]:
    composed = compose_answer_with_trusted_sources(text, trusted_sources or [])
    if not composed.sources:
        return _no_citable_sources_message(user_query), []
    if not composed.content:
        return _no_citable_sources_message(user_query), []
    return composed.content, composed.sources


async def _chat_completion_streaming_with_composed_citations(
    *,
    augmented_messages: list[dict],
    model: str,
    temperature: float,
    settings: Settings,
    org_id: int | str | None,
    user_query: str,
    trusted_sources: list[dict[str, Any]] | None,
) -> AsyncGenerator[bytes]:
    """Collect widget text, compose deterministic citations, then stream once."""
    raw_text_parts: list[str] = []
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST",
            f"{settings.litellm_base_url}/v1/chat/completions",
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
                    break
                try:
                    evt: dict[str, Any] = json.loads(payload)
                except json.JSONDecodeError:
                    logger.debug("partner_chat_sse_parse_skipped", exc_info=True)
                    continue
                delta = (evt.get("choices") or [{}])[0].get("delta") or {}
                text = delta.get("content")
                if isinstance(text, str) and text:
                    raw_text_parts.append(text)

    content, sources = _compose_backend_managed_answer(
        "".join(raw_text_parts),
        trusted_sources,
        user_query,
    )
    if not sources:
        yield _sse_content_delta(content)
        yield b"data: [DONE]\n\n"
        _emit_language_correctness_log(
            org_id=org_id,
            query=user_query,
            response_text=content,
        )
        return

    yield _sse_sources_delta(sources)
    yield _sse_content_delta(content)
    yield b"data: [DONE]\n\n"
    _emit_language_correctness_log(
        org_id=org_id,
        query=user_query,
        response_text=content,
    )


def _maybe_sse_sources_delta(
    *,
    citation_output: CitationOutput,
    emitted_source_key_order: list[str],
    citation_source_metadata: dict[str, dict[str, str]],
) -> bytes | None:
    if citation_output != "markers":
        return None
    sources = _source_payload_from_keys(emitted_source_key_order, citation_source_metadata)
    return _sse_sources_delta(sources) if sources else None


def _build_system_prompt(
    chunks: list[dict],
    original_system: str | None = None,
    widget_system_prompt: str | None = None,
    backend_managed_citations: bool = False,
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

    context_block = render_evidence_context(chunks, include_source_urls=not backend_managed_citations)
    if not context_block:
        return base
    if backend_managed_citations:
        url_guard = (
            "Source handling rules:\n"
            "- Answer only from the context below.\n"
            "- Do not write URLs, Markdown links, footnotes, source lists, or citation numbers.\n"
            "- Do not write references such as [1], (1), or 1,2; the application adds citations after generation.\n"
            "- Keep the answer clean for a small web chat widget.\n"
        )
    else:
        url_guard = (
            "URL rules for citations and source links:\n"
            "- Use only literal source_url values shown in the context below.\n"
            "- Copy source_url values exactly; do not invent, rewrite, or guess URLs.\n"
            "- If a cited chunk has no source_url, cite it as [n] without adding a link.\n"
            "- Never turn a title, heading, or documentation phrase into a URL.\n"
            "- Optimize for a clean web-widget answer: do not cite the same document repeatedly.\n"
            "- If several facts in one paragraph or list come from the same source_url, cite that source once.\n"
            "- If you cite multiple different documents at the same spot, separate citation numbers with commas.\n"
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
    backend_managed_citations: bool = False,
) -> tuple[list[dict], str, list[dict[str, Any]]]:
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
        return (
            [],
            _build_system_prompt(
                [],
                widget_system_prompt=widget_system_prompt,
                backend_managed_citations=backend_managed_citations,
            ),
            [],
        )

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
        return (
            [],
            _build_system_prompt(
                [],
                original_system,
                widget_system_prompt,
                backend_managed_citations=backend_managed_citations,
            ),
            [],
        )

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

    evidence_pack = result.get("evidence_pack")
    chunks = evidence_pack_items_as_chunks(evidence_pack)
    trusted_sources = trusted_sources_from_evidence_pack(evidence_pack)
    system_prompt = _build_system_prompt(
        chunks,
        original_system,
        widget_system_prompt,
        backend_managed_citations=backend_managed_citations,
    )

    return chunks, system_prompt, trusted_sources


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
    citation_source_metadata: dict[str, dict[str, str]] | None = None,
    citation_chunks: list[dict] | None = None,
    trusted_sources: list[dict[str, Any]] | None = None,
    citation_output: CitationOutput = "links",
) -> dict:
    """Forward to LiteLLM and return complete response as dict.

    POST to litellm with stream=false. Emits the
    ``chat_synthesis_complete`` log event before returning so
    cross-lingual correctness is observable on every call (REQ-07).
    """
    augmented_messages = _augment_messages_with_system_prompt(messages, system_prompt)

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
    citation_source_metadata = citation_source_metadata or {}
    emitted_source_key_order: list[str] = []
    if citation_output == "markers":
        for choice in body.get("choices") or []:
            message = choice.get("message") if isinstance(choice, dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(message, dict) and isinstance(content, str):
                rendered_content, sources = _compose_backend_managed_answer(
                    content,
                    trusted_sources,
                    _last_user_message(messages) or "",
                )
                message["content"] = rendered_content
                message["sources"] = sources
        stripped_links = 0
    else:
        stripped_links = _sanitize_completion_body(
            body,
            allowed_source_urls=allowed_source_urls,
            citation_source_urls=citation_source_urls,
            emitted_source_key_order=emitted_source_key_order,
            citation_output=citation_output,
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
    citation_source_metadata: dict[str, dict[str, str]] | None = None,
    citation_chunks: list[dict] | None = None,
    trusted_sources: list[dict[str, Any]] | None = None,
    citation_output: CitationOutput = "links",
) -> AsyncGenerator[bytes]:
    """Stream LiteLLM SSE response with KB-source URL sanitization.

    POST to LiteLLM with stream=true, yield sanitized content deltas. Collects
    the streamed assistant text alongside the byte forwarding so the
    ``chat_synthesis_complete`` log event (REQ-07) gets the full
    response text even though we never buffer it for the client.
    """

    augmented_messages = _augment_messages_with_system_prompt(messages, system_prompt)
    user_query = _last_user_message(messages) or ""

    if citation_output == "markers":
        async for chunk in _chat_completion_streaming_with_composed_citations(
            augmented_messages=augmented_messages,
            model=model,
            temperature=temperature,
            settings=settings,
            org_id=org_id,
            user_query=user_query,
            trusted_sources=trusted_sources,
        ):
            yield chunk
        return

    async for chunk in _chat_completion_streaming_sanitized(
        augmented_messages=augmented_messages,
        model=model,
        temperature=temperature,
        settings=settings,
        org_id=org_id,
        user_query=user_query,
        allowed_source_urls=allowed_source_urls,
        citation_source_urls=citation_source_urls,
        citation_source_metadata=citation_source_metadata,
        citation_output=citation_output,
    ):
        yield chunk


async def _chat_completion_streaming_sanitized(
    *,
    augmented_messages: list[dict],
    model: str,
    temperature: float,
    settings: Settings,
    org_id: int | str | None,
    user_query: str,
    allowed_source_urls: set[str] | None,
    citation_source_urls: dict[int, str] | None,
    citation_source_metadata: dict[str, dict[str, str]] | None,
    citation_output: CitationOutput,
) -> AsyncGenerator[bytes]:
    """Legacy partner streaming path with URL sanitization and linked citations."""
    litellm_url = settings.litellm_base_url
    allowed_source_urls = allowed_source_urls or set()
    citation_source_urls = citation_source_urls or {}
    citation_source_metadata = citation_source_metadata or {}
    collected_text_parts: list[str] = []
    pending_text = ""
    emitted_source_keys: set[str] = set()
    emitted_source_key_order: list[str] = []
    sources_sent = False
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
                        emitted_source_keys=emitted_source_keys,
                        emitted_source_key_order=emitted_source_key_order,
                        citation_output=citation_output,
                        final=True,
                    )
                    stripped_links += changed
                    if safe_text:
                        collected_text_parts.append(safe_text)
                        yield _sse_content_delta(safe_text)
                    if source_delta := _maybe_sse_sources_delta(
                        citation_output=citation_output,
                        emitted_source_key_order=emitted_source_key_order,
                        citation_source_metadata=citation_source_metadata,
                    ):
                        yield source_delta
                    sources_sent = True
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
                    emitted_source_keys=emitted_source_keys,
                    emitted_source_key_order=emitted_source_key_order,
                    citation_output=citation_output,
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
            emitted_source_keys=emitted_source_keys,
            emitted_source_key_order=emitted_source_key_order,
            citation_output=citation_output,
            final=True,
        )
        stripped_links += changed
        if safe_text:
            collected_text_parts.append(safe_text)
            yield _sse_content_delta(safe_text)

    source_delta = (
        None
        if sources_sent
        else _maybe_sse_sources_delta(
            citation_output=citation_output,
            emitted_source_key_order=emitted_source_key_order,
            citation_source_metadata=citation_source_metadata,
        )
    )
    if source_delta:
        yield source_delta

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
