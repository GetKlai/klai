"""Deterministic KB citation rendering for LiteLLM chat responses."""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from klai_chat_prompts import no_citable_sources_message
from klai_citations import (
    compose_answer_with_trusted_sources,
    strip_model_citation_artifacts,
)
from klai_kb_answer_policy import strict_kb_unavailable_message
from klai_kb_chat_mode import prompt_mode_is_known, prompt_mode_is_strict
from klai_kb_traceability import dedupe_strings
from klai_kb_urls import normalise_guard_url
from klai_litellm_response import (
    get_choice_finish_reason,
    get_choice_message,
    get_message_content,
    get_response_choices,
    set_message_content,
    set_message_field,
)

_STREAM_LINK_GUARD_TAIL_CHARS = 16


@dataclass
class KbCitationRenderStats:
    mutated_messages: int = 0
    rendered_messages: int = 0
    rendered_sources: int = 0
    no_citable_sources: bool = False
    citation_decisions: list[dict[str, Any]] = field(default_factory=list)

    def merge(self, other: "KbCitationRenderStats") -> None:
        self.mutated_messages += other.mutated_messages
        self.rendered_messages += other.rendered_messages
        self.rendered_sources = max(self.rendered_sources, other.rendered_sources)
        self.no_citable_sources = self.no_citable_sources or other.no_citable_sources
        self.citation_decisions.extend(other.citation_decisions)


def _is_strict_refusal_answer(text: object, *, user_query: object) -> bool:
    if not isinstance(text, str) or not text.strip():
        return False
    answer = re.sub(r"\s+", " ", text).strip().casefold()
    expected = (
        re.sub(
            r"\s+",
            " ",
            no_citable_sources_message(user_query),
        )
        .strip()
        .casefold()
    )
    unavailable = (
        re.sub(
            r"\s+",
            " ",
            strict_kb_unavailable_message(user_query),
        )
        .strip()
        .casefold()
    )
    return answer in {
        expected,
        unavailable,
        "dat staat niet in de kennisbank.",
        "dat staat niet in de kennisbank",
    }


def _citation_render_inputs(
    kb_meta: dict[str, Any],
) -> tuple[set[str], list[dict], list[dict[str, Any]]]:
    allowed_image_urls = {
        url
        for url in (
            normalise_guard_url(url) for url in kb_meta.get("allowed_image_urls") or []
        )
        if url
    }
    citation_chunks = [
        chunk
        for chunk in (kb_meta.get("citation_chunks") or [])
        if isinstance(chunk, dict)
    ]
    trusted_sources = [
        source
        for source in (kb_meta.get("trusted_sources") or [])
        if isinstance(source, dict)
        and (
            normalise_guard_url(source.get("url"))
            or source.get("artifact_id")
            or source.get("source_id")
            or source.get("title")
        )
    ]
    return allowed_image_urls, citation_chunks, trusted_sources


def _kb_meta_is_strict(kb_meta: dict[str, Any]) -> bool:
    prompt_mode = kb_meta.get("chat_retrieval_prompt_mode")
    if prompt_mode_is_known(prompt_mode):
        return prompt_mode_is_strict(prompt_mode)
    return bool(kb_meta.get("kb_narrow", False))


def _render_kb_citation_content(
    text: str,
    *,
    allowed_image_urls: set[str],
    user_query: object,
    trusted_sources: list[dict[str, Any]],
    evidence_chunks: list[dict],
    kb_narrow: bool,
    retrieval_confidence_band: object = None,
    allow_uncited_user_content: bool = False,
    suppress_citations_for_user_content: bool = False,
    no_citable_message: object = None,
) -> tuple[str, list[dict[str, str]], bool, dict[str, Any]]:
    """Render the model's answer with deterministic citations.

    When retrieval returned no trusted sources (or the source-selector
    rejected every candidate), behaviour depends on the user's mode:

    - **Narrow / strict** (``kb_narrow=True``): replace the model's
      answer with the canned "I cannot answer reliably from the
      available knowledge sources" string. The user opted into strict-
      KB-only behaviour, so refusing without citable evidence is the
      contract.

    - **Broad / open** (``kb_narrow=False``): pass the model's original
      answer through unchanged. The user opted into general-knowledge
      fallback, so the model's response is valid even without citable
      KB sources — clobbering it with a refusal trains users to ignore
      the open/strict toggle.

    Incident reference: Mijndomein tester 2026-05-27 saw the canned
    refusal in every mode regardless of toggles because his retrieved
    chunks produced no trusted sources (`no_trusted_sources`); under
    broad mode the model's general-knowledge answer is the correct
    fallback and the canned message hid it.
    """
    strict_refusal = (
        no_citable_message.strip()
        if isinstance(no_citable_message, str) and no_citable_message.strip()
        else no_citable_sources_message(user_query)
    )
    if not trusted_sources:
        if kb_narrow:
            return (
                strict_refusal,
                [],
                True,
                {
                    "mode": "document_level_supported_sources",
                    "candidate_count": 0,
                    "selected": [],
                    "rejected": [],
                    "no_citable_reason": "no_trusted_sources",
                },
            )
        if allow_uncited_user_content:
            return (
                text,
                [],
                False,
                {
                    "mode": "document_level_supported_sources",
                    "candidate_count": 0,
                    "selected": [],
                    "rejected": [],
                    "no_citable_reason": "user_provided_content_passthrough",
                },
            )
        # Broad mode: keep model's answer, no citations appended.
        return (
            text,
            [],
            False,
            {
                "mode": "document_level_supported_sources",
                "candidate_count": 0,
                "selected": [],
                "rejected": [],
                "no_citable_reason": "no_trusted_sources_broad_passthrough",
            },
        )
    if kb_narrow and _is_strict_refusal_answer(text, user_query=user_query):
        return (
            text.strip(),
            [],
            True,
            {
                "mode": "document_level_supported_sources",
                "candidate_count": len(trusted_sources),
                "selected": [],
                "rejected": [],
                "no_citable_reason": "strict_refusal_no_supported_sources",
            },
        )
    if suppress_citations_for_user_content:
        return (
            text,
            [],
            False,
            {
                "mode": "document_level_supported_sources",
                "candidate_count": len(trusted_sources),
                "selected": [],
                "rejected": [],
                "no_citable_reason": "user_provided_content_no_kb_citations",
            },
        )
    composed = compose_answer_with_trusted_sources(
        text,
        trusted_sources,
        query_text=user_query if isinstance(user_query, str) else "",
        allowed_image_urls=allowed_image_urls,
        evidence_chunks=evidence_chunks,
        retrieval_confidence_band=retrieval_confidence_band,
    )
    if not composed.content or not composed.sources:
        decision = dict(composed.decision)
        if kb_narrow and _is_strict_refusal_answer(
            composed.content or text,
            user_query=user_query,
        ):
            decision["no_citable_reason"] = "strict_refusal_no_supported_sources"
            return strict_refusal, [], True, decision
        if kb_narrow:
            decision["no_citable_reason"] = "strict_no_sentence_level_support"
            return strict_refusal, [], True, decision
        # Broad mode: pass model's answer through even when no trusted
        # document-level source can be rendered — general knowledge is valid.
        decision["no_citable_reason"] = (
            "selector_rejected_all_sources_broad_passthrough"
        )
        return text, [], False, decision
    return (
        composed.content,
        _prepend_primary_upload_source(
            _merge_source_metadata(composed.sources, trusted_sources),
            trusted_sources,
        ),
        False,
        composed.decision,
    )


def _source_metadata_keys(source: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    url = normalise_guard_url(source.get("url") or source.get("source_url"))
    if url:
        keys.append(f"url:{url}")
    # source_label is intentionally excluded: for personal KB uploads it is
    # the KB slug, so multiple different documents share the same value.
    for key in ("artifact_id", "source_id", "title"):
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            keys.append(f"{key}:{value.strip()}")
    return keys


def _source_with_metadata(source: dict[str, Any], *, label: str) -> dict[str, Any]:
    title = str(source.get("title") or source.get("source_label") or "Source").strip()
    rendered: dict[str, Any] = {
        "label": label,
        "title": title or "Source",
        "url": normalise_guard_url(source.get("url") or source.get("source_url")),
    }
    for key in (
        "source_id",
        "evidence_ids",
        "evidence",
        "artifact_id",
        "source_label",
        "relevance_score",
    ):
        value = source.get(key)
        if value is not None:
            rendered[key] = value
    return rendered


def _merge_source_metadata(
    rendered_sources: list[dict[str, Any]],
    trusted_sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    trusted_by_key: dict[str, dict[str, Any]] = {}
    for source in trusted_sources:
        if not isinstance(source, dict):
            continue
        for key in _source_metadata_keys(source):
            trusted_by_key.setdefault(key, source)
    enriched: list[dict[str, Any]] = []
    for index, source in enumerate(rendered_sources, 1):
        if not isinstance(source, dict):
            continue
        match = next(
            (
                trusted_by_key[key]
                for key in _source_metadata_keys(source)
                if key in trusted_by_key
            ),
            None,
        )
        merged = dict(source)
        if match is not None:
            for key in (
                "source_id",
                "evidence_ids",
                "evidence",
                "artifact_id",
                "source_label",
                "relevance_score",
            ):
                value = match.get(key)
                if value is not None:
                    merged[key] = value
        merged.setdefault("label", str(index))
        merged.setdefault("title", "Source")
        merged["url"] = normalise_guard_url(merged.get("url"))
        enriched.append(merged)
    return enriched


def _prepend_primary_upload_source(
    rendered_sources: list[dict[str, Any]],
    trusted_sources: list[dict[str, Any]],
    *,
    max_sources: int = 3,
) -> list[dict[str, Any]]:
    """Keep the EvidencePack's primary uploaded document visible.

    The post-call selector validates sources against free-form answer text.
    That is useful for filtering model-authored noise, but OCR-heavy PDFs can
    make token overlap brittle. EvidencePack.sources is already trusted
    retrieval provenance, so a URL-less upload returned as the primary source
    must not disappear from the visible citation footer.
    """
    if not trusted_sources:
        return rendered_sources
    primary = trusted_sources[0]
    if normalise_guard_url(primary.get("url") or primary.get("source_url")):
        return rendered_sources
    if not primary.get("artifact_id"):
        return rendered_sources

    primary_keys = set(_source_metadata_keys(primary))
    rendered_keys = {
        key
        for source in rendered_sources
        if isinstance(source, dict)
        for key in _source_metadata_keys(source)
    }
    if primary_keys & rendered_keys:
        return rendered_sources

    combined = [_source_with_metadata(primary, label="1"), *rendered_sources]
    relabelled: list[dict[str, Any]] = []
    for index, source in enumerate(combined[:max_sources], 1):
        updated = dict(source)
        updated["label"] = str(index)
        relabelled.append(updated)
    return relabelled


def _plural_nl(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


def _format_limited_label_list(values: object, *, max_items: int = 5) -> str:
    if not isinstance(values, list):
        return ""
    labels = dedupe_strings(values)
    if not labels:
        return ""
    visible = labels[:max_items]
    suffix = f", +{len(labels) - max_items} meer" if len(labels) > max_items else ""
    return ", ".join(visible) + suffix


def _format_kb_scope_text(kb_meta: dict[str, Any]) -> str:
    scope_mode = kb_meta.get("kb_scope_mode")
    labels_text = _format_limited_label_list(kb_meta.get("kbs_in_scope"))
    if labels_text:
        if scope_mode == "explicit_org_and_personal":
            return f"{labels_text} + persoonlijke kennisbank"
        return labels_text
    if scope_mode == "all_org_and_personal":
        return "alle organisatiekennisbanken + persoonlijke kennisbank"
    if scope_mode == "all_org":
        return "alle organisatiekennisbanken"
    if scope_mode == "personal":
        return "persoonlijke kennisbank"
    return ""


def _format_visible_sources_markdown(sources: list[dict[str, Any]]) -> str:
    """Render source links or URL-less upload labels for the visible chat footer."""
    lines: list[str] = []
    for source in sources:
        title = str(
            source.get("title") or source.get("source_label") or "Source"
        ).strip()
        url = normalise_guard_url(source.get("url"))
        if url:
            lines.append(f"- [{title or 'Source'}]({url})")
        else:
            lines.append(f"- {title or 'Source'}")
    return "\n".join(lines)


def _append_visible_sources_section(
    content: str,
    sources: list[dict[str, Any]],
    *,
    kb_meta: dict[str, Any] | None = None,
) -> str:
    """Append backend-selected sources for clients that ignore structured metadata."""
    sections: list[str] = []
    if sources:
        sources_markdown = _format_visible_sources_markdown(sources).strip()
        if sources_markdown:
            sections.append(f"**Bronnen**\n{sources_markdown}")
    has_no_citable_reason = (
        kb_meta is not None
        and isinstance(kb_meta.get("no_citable_reason"), str)
        and bool(kb_meta.get("no_citable_reason"))
    )
    if kb_meta is not None and (
        sources or has_no_citable_reason or _has_visible_agent_activity(kb_meta)
    ):
        activity = _format_visible_agent_activity(kb_meta, sources)
        if activity:
            sections.append(f"**Agent activiteit**\n{activity}")
    if sources:
        marker = _format_sources_metadata_marker(sources)
        if marker:
            sections.append(marker)
    if not sections:
        return content
    return f"{content.rstrip()}\n\n" + "\n\n".join(sections)


def _has_visible_agent_activity(kb_meta: dict[str, Any] | None) -> bool:
    if not isinstance(kb_meta, dict) or kb_meta.get("gate_bypassed"):
        return False
    has_kb_trace_labels = bool(
        _format_kb_scope_text(kb_meta)
        or _format_limited_label_list(kb_meta.get("kbs_with_results"))
        or _format_limited_label_list(kb_meta.get("kbs_used_as_sources"))
    )
    if bool(kb_meta.get("allow_uncited_user_content")):
        return bool(_format_limited_label_list(kb_meta.get("kbs_used_as_sources")))
    if has_kb_trace_labels:
        return True
    if isinstance(kb_meta.get("chunks_injected"), int):
        return _kb_meta_is_strict(kb_meta) or bool(kb_meta.get("no_citable_reason"))
    return False


def _format_sources_metadata_marker(sources: list[dict[str, Any]]) -> str:
    try:
        payload = json.dumps(
            sources, ensure_ascii=False, separators=(",", ":")
        ).encode()
    except (TypeError, ValueError):
        return ""
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"<!-- klai_sources={encoded} -->"


def _format_visible_agent_activity(
    kb_meta: dict[str, Any],
    sources: list[dict[str, Any]],
) -> str:
    """Render provenance for LibreChat, which ignores structured source metadata."""
    chunks_injected = kb_meta.get("chunks_injected")
    retrieval_ms = kb_meta.get("retrieval_ms")
    citable_sources_count = kb_meta.get("citable_sources_count")
    no_citable_reason = kb_meta.get("no_citable_reason")
    kb_narrow = _kb_meta_is_strict(kb_meta)
    confidence_band = kb_meta.get("confidence_band")

    lines: list[str] = []
    lines.append(
        "- Modus: "
        + (
            "Strict, alleen kennisbank."
            if kb_narrow
            else "Open, kennisbank met fallback."
        )
    )
    if isinstance(chunks_injected, int):
        chunk_label = _plural_nl(chunks_injected, "fragment", "fragmenten")
        if isinstance(retrieval_ms, int | float):
            lines.append(
                "- Kennisbank geraadpleegd: "
                f"{chunks_injected} {chunk_label} opgehaald in {int(retrieval_ms)} ms."
            )
        else:
            lines.append(
                f"- Kennisbank geraadpleegd: {chunks_injected} {chunk_label} opgehaald."
            )

    kb_scope_text = _format_kb_scope_text(kb_meta)
    if kb_scope_text:
        lines.append(f"- Kennisbanken in scope: {kb_scope_text}.")

    kbs_with_results = _format_limited_label_list(kb_meta.get("kbs_with_results"))
    if kbs_with_results:
        lines.append(f"- Kennisbanken met resultaat: {kbs_with_results}.")

    kbs_used_as_sources = _format_limited_label_list(kb_meta.get("kbs_used_as_sources"))
    if kbs_used_as_sources:
        lines.append(f"- Kennisbanken gebruikt als bron: {kbs_used_as_sources}.")

    if isinstance(citable_sources_count, int):
        candidate_label = _plural_nl(
            citable_sources_count,
            "kandidaatbron",
            "kandidaatbronnen",
        )
        selected_label = _plural_nl(len(sources), "bron", "bronnen")
        lines.append(
            "- Bronselectie: "
            f"{len(sources)} {selected_label} gekoppeld uit "
            f"{citable_sources_count} {candidate_label}."
        )
    elif sources:
        selected_label = _plural_nl(len(sources), "bron", "bronnen")
        lines.append(f"- Bronselectie: {len(sources)} {selected_label} gekoppeld.")

    source_titles = [
        str(source.get("title") or "").strip()
        for source in sources
        if isinstance(source.get("title"), str) and str(source.get("title")).strip()
    ]
    if source_titles:
        lines.append(f"- Gebruikte bronnen: {', '.join(source_titles[:3])}.")

    if isinstance(confidence_band, str) and confidence_band:
        if sources:
            lines.append(
                f"- Retrieval score: {confidence_band}; bronfragmenten gekoppeld."
            )
        else:
            lines.append(f"- Retrieval score: {confidence_band}.")

    if not sources and isinstance(no_citable_reason, str) and no_citable_reason:
        lines.append(
            f"- Citeerbaarheid: geen bruikbare bron geselecteerd ({no_citable_reason})."
        )

    return "\n".join(lines)


def log_kb_citation_render(
    logger: logging.Logger,
    kb_meta: dict[str, Any],
    stats: KbCitationRenderStats,
    *,
    stream: bool,
) -> None:
    if not stats.rendered_messages:
        return

    event = (
        "kb_citations_no_citable_sources"
        if stats.no_citable_sources
        else "kb_citations_rendered_structured"
    )
    logger.warning(
        "%s org_id=%s user_id=%s render_mode=%s stream=%s rendered_messages=%d rendered_sources=%d chunks_injected=%s no_citable_reason=%s citation_decisions=%s",
        event,
        kb_meta.get("org_id"),
        kb_meta.get("user_id"),
        kb_meta.get("render_mode"),
        stream,
        stats.rendered_messages,
        stats.rendered_sources,
        kb_meta.get("chunks_injected"),
        kb_meta.get("no_citable_reason"),
        stats.citation_decisions,
    )


def _remember_citation_decision(
    kb_meta: dict[str, Any],
    decision: dict[str, Any],
    *,
    no_citable_sources: bool,
) -> None:
    reason = decision.get("no_citable_reason")
    if no_citable_sources and isinstance(reason, str) and reason:
        kb_meta["no_citable_reason"] = reason


def _citation_user_content_flags(kb_meta: dict[str, Any]) -> tuple[bool, bool]:
    return (
        bool(kb_meta.get("allow_uncited_user_content")),
        bool(kb_meta.get("suppress_kb_citations")),
    )


def _earliest_stream_guard_start(text: str) -> int:
    starts = [
        idx
        for idx in (
            text.find("!["),
            text.find("["),
            text.find("http://"),
            text.find("https://"),
        )
        if idx >= 0
    ]
    return min(starts) if starts else -1


def _pop_streaming_guard_text(buffer: str, *, final: bool) -> tuple[str, str]:
    start = _earliest_stream_guard_start(buffer)
    if start >= 0:
        if final:
            return buffer, ""
        return buffer[:start], buffer[start:]
    if final:
        return buffer, ""
    if len(buffer) <= _STREAM_LINK_GUARD_TAIL_CHARS:
        return "", buffer
    return buffer[:-_STREAM_LINK_GUARD_TAIL_CHARS], buffer[
        -_STREAM_LINK_GUARD_TAIL_CHARS:
    ]


def _collapse_whitespace_with_index_map(text: str) -> tuple[str, list[int]]:
    collapsed: list[str] = []
    index_map: list[int] = []
    in_whitespace = False
    for index, char in enumerate(text):
        if char.isspace():
            if collapsed and not in_whitespace:
                collapsed.append(" ")
                index_map.append(index)
            in_whitespace = True
            continue
        collapsed.append(char)
        index_map.append(index)
        in_whitespace = False
    if collapsed and collapsed[-1] == " ":
        collapsed.pop()
        index_map.pop()
    return "".join(collapsed), index_map


def remove_already_streamed_prefix(final_text: str, emitted_text: str) -> str | None:
    """Return only the part of final_text that has not already streamed.

    The deterministic citation renderer may normalize whitespace compared to
    the token stream. Use an exact cut when possible, then a whitespace-tolerant
    cut so the final source footer does not replay the full answer in LibreChat.

    Returns ``None`` when the rendered text diverges from the streamed prefix
    in non-whitespace content (e.g. the cleaner renumbered list lines). The
    caller must then compose the flush delta from the raw un-streamed
    remainder — replaying ``final_text`` after already-streamed text duplicates
    the whole answer in the client (Voys feedback #21, 2026-06-11).
    """
    if not emitted_text:
        return final_text
    if final_text.startswith(emitted_text):
        return final_text[len(emitted_text) :]

    collapsed_final, final_map = _collapse_whitespace_with_index_map(final_text)
    collapsed_emitted, _ = _collapse_whitespace_with_index_map(emitted_text)
    if collapsed_emitted and collapsed_final.startswith(collapsed_emitted):
        cut_index = final_map[len(collapsed_emitted) - 1] + 1
        return final_text[cut_index:]

    return None


def compose_non_streaming_kb_response(
    response: object,
    kb_meta: dict[str, Any],
) -> KbCitationRenderStats:
    """Replace non-streaming message content with deterministic citations."""
    stats = KbCitationRenderStats()
    allowed_image_urls, citation_chunks, trusted_sources = _citation_render_inputs(
        kb_meta
    )
    force_no_citable = bool(kb_meta.get("no_citable_sources"))
    has_visible_activity = _has_visible_agent_activity(kb_meta)
    if (
        not citation_chunks
        and not trusted_sources
        and not force_no_citable
        and not has_visible_activity
    ):
        return stats
    allow_uncited_user_content, suppress_user_content_citations = (
        _citation_user_content_flags(kb_meta)
    )

    for choice in get_response_choices(response):
        message = get_choice_message(choice, "message")
        if message is None:
            continue
        content = get_message_content(message)
        if isinstance(content, str):
            rendered_content, sources, no_citable_sources, decision = (
                _render_kb_citation_content(
                    content,
                    allowed_image_urls=allowed_image_urls,
                    user_query=kb_meta.get("user_query"),
                    trusted_sources=trusted_sources,
                    evidence_chunks=citation_chunks,
                    kb_narrow=_kb_meta_is_strict(kb_meta),
                    retrieval_confidence_band=kb_meta.get("confidence_band"),
                    allow_uncited_user_content=allow_uncited_user_content,
                    suppress_citations_for_user_content=suppress_user_content_citations,
                    no_citable_message=kb_meta.get("no_citable_message"),
                )
            )
            if (
                rendered_content != content
                or sources
                or no_citable_sources
                or has_visible_activity
            ):
                _remember_citation_decision(
                    kb_meta,
                    decision,
                    no_citable_sources=no_citable_sources,
                )
                set_message_content(
                    message,
                    _append_visible_sources_section(
                        rendered_content, sources, kb_meta=kb_meta
                    ),
                )
                set_message_field(message, "sources", sources)
                stats.mutated_messages += 1
                stats.rendered_messages += 1
                stats.rendered_sources = max(stats.rendered_sources, len(sources))
                stats.no_citable_sources = (
                    stats.no_citable_sources or no_citable_sources
                )
                stats.citation_decisions.append(decision)
    return stats


def compose_streaming_kb_response(
    response: object,
    kb_meta: dict[str, Any],
    *,
    flush_stream: bool = False,
) -> KbCitationRenderStats:
    """Let answer tokens stream and append deterministic sources at the end."""
    stats = KbCitationRenderStats()
    allowed_image_urls, citation_chunks, trusted_sources = _citation_render_inputs(
        kb_meta
    )
    force_no_citable = bool(kb_meta.get("no_citable_sources"))
    has_visible_activity = _has_visible_agent_activity(kb_meta)
    if (
        not citation_chunks
        and not trusted_sources
        and not force_no_citable
        and not has_visible_activity
    ):
        return stats
    if kb_meta.get("_citation_stream_sources_appended"):
        return stats

    kb_narrow = _kb_meta_is_strict(kb_meta)
    allow_uncited_user_content, suppress_user_content_citations = (
        _citation_user_content_flags(kb_meta)
    )
    strict_no_sources = (
        not trusted_sources
        and (force_no_citable or kb_narrow)
    )
    if not trusted_sources and not strict_no_sources and not has_visible_activity:
        return stats

    should_flush = flush_stream

    for choice in get_response_choices(response):
        delta = get_choice_message(choice, "delta")
        if delta is None:
            continue
        content = get_message_content(delta)
        should_flush = should_flush or bool(get_choice_finish_reason(choice))
        if strict_no_sources:
            if isinstance(content, str) and content:
                buffered = kb_meta.get("_citation_stream_guard_buffer") or ""
                kb_meta["_citation_stream_guard_buffer"] = buffered + content
                set_message_content(delta, "")
                stats.mutated_messages += 1
            if not should_flush:
                continue
            rendered_content, sources, no_citable_sources, decision = (
                _render_kb_citation_content(
                    kb_meta.get("_citation_stream_guard_buffer") or "",
                    allowed_image_urls=allowed_image_urls,
                    user_query=kb_meta.get("user_query"),
                    trusted_sources=trusted_sources,
                    evidence_chunks=citation_chunks,
                    kb_narrow=kb_narrow,
                    retrieval_confidence_band=kb_meta.get("confidence_band"),
                    allow_uncited_user_content=allow_uncited_user_content,
                    suppress_citations_for_user_content=suppress_user_content_citations,
                    no_citable_message=kb_meta.get("no_citable_message"),
                )
            )
            _remember_citation_decision(
                kb_meta,
                decision,
                no_citable_sources=no_citable_sources,
            )
            set_message_content(
                delta,
                _append_visible_sources_section(
                    rendered_content, sources, kb_meta=kb_meta
                ),
            )
            kb_meta["_citation_stream_sources_appended"] = True
            kb_meta["_citation_stream_guard_buffer"] = ""
            stats.mutated_messages += 1
            stats.rendered_messages += 1
            stats.rendered_sources = len(sources)
            stats.no_citable_sources = no_citable_sources
            stats.citation_decisions.append(decision)
            return stats

        stream_buffer = kb_meta.get("_citation_stream_guard_buffer") or ""
        if isinstance(content, str) and content:
            full_parts = kb_meta.setdefault("_citation_stream_full_parts", [])
            full_parts.append(content)
            stream_buffer += content
        safe_text, stream_buffer = _pop_streaming_guard_text(
            stream_buffer, final=should_flush
        )
        if safe_text != content:
            set_message_content(delta, safe_text)
            stats.mutated_messages += 1
        if safe_text and not should_flush:
            emitted_parts = kb_meta.setdefault("_citation_stream_emitted_parts", [])
            emitted_parts.append(safe_text)
        kb_meta["_citation_stream_guard_buffer"] = stream_buffer
        if not should_flush:
            continue

        full_text = "".join(
            part
            for part in kb_meta.get("_citation_stream_full_parts", [])
            if isinstance(part, str)
        )
        emitted_text = "".join(
            part
            for part in kb_meta.get("_citation_stream_emitted_parts", [])
            if isinstance(part, str)
        )
        rendered_content, sources, no_citable_sources, decision = (
            _render_kb_citation_content(
                full_text,
                allowed_image_urls=allowed_image_urls,
                user_query=kb_meta.get("user_query"),
                trusted_sources=trusted_sources,
                evidence_chunks=citation_chunks,
                kb_narrow=kb_narrow,
                retrieval_confidence_band=kb_meta.get("confidence_band"),
                allow_uncited_user_content=allow_uncited_user_content,
                suppress_citations_for_user_content=suppress_user_content_citations,
                no_citable_message=kb_meta.get("no_citable_message"),
            )
        )
        tail = remove_already_streamed_prefix(rendered_content, emitted_text)
        if tail is None and no_citable_sources:
            # Deliberate replacement (strict refusal): the canned message is
            # the contract and is short — append it in full after the stream.
            tail = rendered_content
            decision["stream_flush_alignment"] = "replacement_appended"
        elif tail is None:
            # The cleaner changed non-whitespace content inside the region the
            # user already saw, so the rendered answer cannot be aligned with
            # the stream. Never replay the full answer (Voys feedback #21,
            # 2026-06-11: BT-ticket form restarted at "Hello BT"). Emit the
            # raw un-streamed remainder instead, cleaned standalone so
            # buffered model-authored links/images still cannot leak.
            remainder = (
                full_text[len(emitted_text) :]
                if full_text.startswith(emitted_text)
                else rendered_content
            )
            tail = strip_model_citation_artifacts(
                remainder,
                allowed_image_urls=allowed_image_urls,
                source_titles={
                    source["title"]
                    for source in trusted_sources
                    if isinstance(source.get("title"), str)
                },
            )
            decision["stream_flush_alignment"] = "raw_remainder"
        else:
            decision["stream_flush_alignment"] = "prefix_cut"
        _remember_citation_decision(
            kb_meta,
            decision,
            no_citable_sources=no_citable_sources,
        )
        final_text = _append_visible_sources_section(tail, sources, kb_meta=kb_meta)
        if sources:
            set_message_field(delta, "sources", sources)
        set_message_content(delta, final_text)
        kb_meta["_citation_stream_sources_appended"] = True
        kb_meta["_citation_stream_guard_buffer"] = ""
        kb_meta["_citation_stream_full_parts"] = []
        kb_meta["_citation_stream_emitted_parts"] = []
        stats.mutated_messages += 1
        stats.rendered_messages += 1
        stats.rendered_sources = len(sources)
        stats.no_citable_sources = no_citable_sources
        stats.citation_decisions.append(decision)
        return stats
    return stats
