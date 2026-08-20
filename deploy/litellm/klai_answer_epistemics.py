"""Deterministic answer provenance and correspondence-contract inspection."""

from __future__ import annotations

import re
from typing import Any

from klai_citations import evidence_chunks_from_chunks, extract_salient_query_tokens
from klai_pasted_correspondence import (
    ANSWER_CONTRACT_MARKERS,
    extract_pasted_correspondence_text,
)

_EVIDENCE_TOKEN_FIELDS = (
    "title",
    "heading_path",
    "source_label",
    "text",
    "content",
)

_STRIPPABLE_EVIDENCE_LABEL_RE = re.compile(
    r"\(\s*(?:evidence\s+)?E\d{1,3}(?:\s*[,;]\s*E\d{1,3})*\s*\)"
    r"|\[\s*(?:evidence\s+)?E\d{1,3}(?:\s*[,;]\s*E\d{1,3})*\s*\]"
    r"|\bevidence\s+E\d{1,3}\b",
    re.IGNORECASE,
)
_EVIDENCE_ID_TOKEN_RE = re.compile(r"E\d{1,3}", re.IGNORECASE)
_ANSWER_CONTRACT_MARKER_RE = re.compile(r"\[\[KLAI_[A-Z_]+\]\]", re.IGNORECASE)
_ANSWER_CONTRACT_PARTIAL_MARKER_RE = re.compile(
    r"(\[\[KLAI_[A-Z_]*(?:\]?)?)$", re.IGNORECASE
)
_ANSWER_CONTRACT_PREFIX = "[[KLAI_"
_ANSWER_CONTRACT_MARKERS_BY_SUFFIX = {
    marker[len("[[KLAI_CORRESPONDENCE_") : -2]: marker
    for marker in ANSWER_CONTRACT_MARKERS
}
_KNOWN_SECTION_MARKER_RE = re.compile(
    r"\[\[KLAI_[A-Z_]*("
    + "|".join(re.escape(suffix) for suffix in _ANSWER_CONTRACT_MARKERS_BY_SUFFIX)
    + r")\]\]",
    re.IGNORECASE,
)


def _normalize_known_section_markers(answer: str) -> str:
    """Canonicalize known sections while leaving unknown markers observable."""
    return _KNOWN_SECTION_MARKER_RE.sub(
        lambda match: _ANSWER_CONTRACT_MARKERS_BY_SUFFIX[match.group(1).upper()],
        answer,
    )


def _evidence_tokens(evidence_chunks: list[dict]) -> set[str]:
    tokens: set[str] = set()
    for chunk in evidence_chunks:
        if not isinstance(chunk, dict):
            continue
        tokens |= extract_salient_query_tokens(
            " ".join(str(chunk.get(field) or "") for field in _EVIDENCE_TOKEN_FIELDS)
        )
    return tokens


def inspect_answer_epistemics(
    answer: str,
    *,
    user_turn: object,
    evidence_chunks: list[dict],
    correspondence_detected: bool,
    telemetry_level: object,
    latest_turn_correspondence_detected: bool | None = None,
) -> dict[str, Any]:
    """Measure answer provenance without changing or enforcing the answer."""
    user_turn_text = user_turn if isinstance(user_turn, str) else ""
    user_turn_tokens = extract_salient_query_tokens(user_turn_text)
    latest_turn_detected = (
        correspondence_detected
        if latest_turn_correspondence_detected is None
        else latest_turn_correspondence_detected
    )
    correspondence_text = (
        extract_pasted_correspondence_text(user_turn_text, assume_detected=True)
        if latest_turn_detected
        else ""
    )
    correspondence_tokens = extract_salient_query_tokens(correspondence_text)
    evidence_tokens = _evidence_tokens(evidence_chunks)
    measured_answer = (
        strip_answer_contract_markers(answer) if correspondence_detected else answer
    )
    answer_tokens = extract_salient_query_tokens(measured_answer)

    sender_only = answer_tokens & (correspondence_tokens - evidence_tokens)
    unsupported = answer_tokens - (evidence_tokens | user_turn_tokens)
    provenance: dict[str, Any] = {
        "sender_only_tokens_in_answer": len(sender_only),
        "answer_tokens_unsupported_by_evidence": len(unsupported),
        "correspondence_detected": bool(correspondence_detected),
    }
    if telemetry_level == "full":
        provenance["sender_only_tokens"] = sorted(sender_only)
        provenance["answer_tokens_unsupported_by_evidence_values"] = sorted(unsupported)

    result: dict[str, Any] = {"claim_provenance": provenance}
    if correspondence_detected:
        result["answer_contract"] = _verify_answer_contract(
            _normalize_known_section_markers(answer), evidence_chunks
        )
    return result


def _verify_answer_contract(answer: str, evidence_chunks: list[dict]) -> dict[str, Any]:
    positions = [answer.find(marker) for marker in ANSWER_CONTRACT_MARKERS]
    missing_sections = [
        index for index, position in enumerate(positions, start=1) if position < 0
    ]
    present_positions = [position for position in positions if position >= 0]
    emitted_markers = _ANSWER_CONTRACT_MARKER_RE.findall(answer)
    order_violation = (
        (positions[0] >= 0 and bool(answer[: positions[0]].strip()))
        or present_positions != sorted(present_positions)
        or any(
            answer.count(marker) != 1
            for marker, position in zip(ANSWER_CONTRACT_MARKERS, positions, strict=True)
            if position >= 0
        )
        or any(marker not in ANSWER_CONTRACT_MARKERS for marker in emitted_markers)
    )

    section2_uncited = False
    if evidence_chunks and positions[1] >= 0:
        section2_end = min(
            (position for position in positions[2:] if position > positions[1]),
            default=len(answer),
        )
        section2 = answer[positions[1] + len(ANSWER_CONTRACT_MARKERS[1]) : section2_end]
        valid_ids = {
            item.evidence_id.upper()
            for item in evidence_chunks_from_chunks(evidence_chunks)
        }
        cited_ids = {
            token.upper()
            for label in _STRIPPABLE_EVIDENCE_LABEL_RE.finditer(section2)
            for token in _EVIDENCE_ID_TOKEN_RE.findall(label.group(0))
        }
        section2_uncited = not bool(valid_ids & cited_ids)

    satisfied = not missing_sections and not order_violation and not section2_uncited
    return {
        "satisfied": satisfied,
        "missing_sections": missing_sections,
        "order_violation": order_violation,
        "section2_uncited": section2_uncited,
    }


def _strip_trailing_contract_marker_fragment(answer: str) -> str:
    """Remove a provider-truncated marker suffix while preserving whitespace."""
    body = answer.rstrip()
    trailing_whitespace = answer[len(body) :]
    partial_marker = _ANSWER_CONTRACT_PARTIAL_MARKER_RE.search(body)
    if partial_marker:
        return body[: partial_marker.start()] + trailing_whitespace

    folded_body = body.casefold()
    max_prefix = min(len(_ANSWER_CONTRACT_PREFIX) - 1, len(body))
    for prefix_length in range(max_prefix, 0, -1):
        if folded_body.endswith(_ANSWER_CONTRACT_PREFIX[:prefix_length].casefold()):
            return body[:-prefix_length] + trailing_whitespace
    return answer


def strip_answer_contract_markers(answer: str) -> str:
    """Remove internal answer-shape markers from correspondence output."""
    return _strip_trailing_contract_marker_fragment(
        _ANSWER_CONTRACT_MARKER_RE.sub("", answer)
    )


def pop_answer_contract_stream_text(buffer: str, *, final: bool) -> tuple[str, str]:
    """Strip complete markers and retain only a split marker suffix."""
    cleaned = _ANSWER_CONTRACT_MARKER_RE.sub("", buffer)
    if final:
        return _strip_trailing_contract_marker_fragment(cleaned), ""

    hold_length = 0
    max_prefix = min(len(_ANSWER_CONTRACT_PREFIX) - 1, len(cleaned))
    for prefix_length in range(max_prefix, 0, -1):
        if cleaned.casefold().endswith(
            _ANSWER_CONTRACT_PREFIX[:prefix_length].casefold()
        ):
            hold_length = prefix_length
            break
    partial_marker = _ANSWER_CONTRACT_PARTIAL_MARKER_RE.search(cleaned)
    if partial_marker:
        hold_length = max(hold_length, len(partial_marker.group(1)))
    if not hold_length:
        return cleaned, ""
    return cleaned[:-hold_length], cleaned[-hold_length:]


__all__ = [
    "ANSWER_CONTRACT_MARKERS",
    "inspect_answer_epistemics",
    "pop_answer_contract_stream_text",
    "strip_answer_contract_markers",
]
