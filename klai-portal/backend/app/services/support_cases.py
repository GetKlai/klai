"""Support-case evidence store and analysis orchestration (SPEC-RAG-SUPPORT-GAP).

One tenant-scoped table (``portal_support_cases``) holds validated case
payloads as restricted evidence; this module is the only write path into it.
It owns the contract in ``docs/architecture/support-gap-detection.md`` →
"Case payload and storage" / "Existing inbox and transcript input":

- telemetry gate: only ``telemetry_level=full`` orgs may store or analyse
  literal support evidence; every other level is rejected before persistence
  and before any model call;
- stable-identity serialization so a repeat or concurrent import lands on one
  case and one finding per (case, question, diagnosis);
- content-hash change detection: a changed message invalidates the old
  findings and triggers reanalysis; a byte-identical re-import is a no-op;
- evidence is persisted even when analysis fails (visible, retryable), and a
  failed analysis is never reported as a successful sync.

"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any, Literal

import structlog
from fastapi import HTTPException
from sqlalchemy import Row, delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import set_tenant
from app.core.permissions import assert_platform_unlocked
from app.models.portal import PortalOrg
from app.models.retrieval_gaps import PortalRetrievalGap
from app.models.support_cases import PortalSupportCase
from app.schemas_support_cases import SupportCaseMessage, SupportCasePayload

logger = structlog.get_logger()

# Platform unlock that turns on the whole support-gap surface for a tenant. The
# telemetry_level gate and this feature gate are independent AND-conditions: a
# tenant needs telemetry_level=full AND knowledge_gaps unlocked to store or
# analyse support evidence. Fail-closed — an org row without the flag (the
# server default omits it) is rejected.
GAP_FEATURE = "knowledge_gaps"

# Diagnoses that create an inbox finding. The analyzer may return others
# (covered/non_knowledge/uncertain); those stay on the case's ``analysis`` and
# never become a gap row.
INBOX_DIAGNOSES: frozenset[str] = frozenset(
    {"missing", "incomplete", "outdated", "contradictory", "findability", "audience"}
)

# A case larger than the analyzer can handle is rejected rather than silently
# truncated (contract: "Support up to 1,000 messages/segments and 200,000 text
# characters per case; reject larger cases explicitly"). These match the
# analyzer lane's own limits so a case that upserts here can always be analysed.
MAX_CASE_MESSAGES = 1_000
MAX_CASE_TEXT_CHARS = 200_000


class SupportTelemetryError(Exception):
    """Org telemetry level forbids storing or viewing literal support evidence."""


class OversizedCaseError(Exception):
    """Case payload exceeds ``MAX_CASE_BYTES``."""


@dataclass(frozen=True, slots=True)
class UpsertResult:
    """Return shape of :func:`upsert_support_case` — the import response."""

    case_id: int
    status: Literal["incomplete", "pending", "analyzed", "failed"]
    changed: bool
    findings_count: int


def require_full_telemetry(telemetry_level: str | None) -> None:
    """Raise unless the org may hold literal support evidence.

    Rechecked on every read/analyze path, not only at import: a tenant that
    downgrades away from ``full`` loses evidence access immediately, and the
    purge removes the stored rows.
    """
    if telemetry_level != "full":
        raise SupportTelemetryError(f"telemetry_level={telemetry_level!r} forbids support evidence")


def _identity_lock_key(org_id: int, payload: SupportCasePayload) -> int:
    """Stable 63-bit advisory-lock key for a case identity.

    ``pg_advisory_xact_lock`` serialises concurrent upserts of the *same*
    identity (including the first insert, which no row lock can cover) without
    blocking unrelated cases. Collisions only cost a little extra serialisation,
    never correctness — the UNIQUE constraint is the real guard.
    """
    material = f"{org_id}|{payload.kb_key()}".encode()
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big", signed=True)


async def _load_case_for_update(
    db: AsyncSession, *, org_id: int, payload: SupportCasePayload
) -> PortalSupportCase | None:
    result = await db.execute(
        select(PortalSupportCase)
        .where(
            PortalSupportCase.org_id == org_id,
            PortalSupportCase.kb_slug == payload._kb_slug,
            PortalSupportCase.source == payload.source,
            PortalSupportCase.account_id == payload.account_id,
            PortalSupportCase.external_id == payload.external_id,
        )
        .with_for_update()
    )
    return result.scalar_one_or_none()


def _normalize_question(text_value: str) -> str:
    return re.sub(r"\s+", " ", text_value).strip().casefold()


def _question_key(*, question: str, diagnosis: str, language: str | None, kb_slug: str, audience: str | None) -> str:
    """Grouping key for support findings.

    Equivalent questions across cases collapse into one inbox group while
    keeping different diagnoses, KBs, languages and audiences distinct
    (contract: "group normalized equivalent questions"). Semantic merging is a
    later, measured extension — this is deliberately a normalized-text key.
    """
    return "|".join([_normalize_question(question), diagnosis, language or "", kb_slug, audience or ""])


def _finding_gap(
    *,
    org_id: int,
    user_id: str,
    kb_slug: str,
    case_id: int,
    finding: dict,
) -> PortalRetrievalGap:
    return PortalRetrievalGap(
        org_id=org_id,
        user_id=user_id,
        query_text=finding["question"],
        # Every case-backed inbox row is a content gap: a content gap can exist
        # even when retrieval scores are healthy (diagnosis=incomplete with
        # good hits), so the analyzer's nullable hard/soft signal is kept in
        # ``evidence.retrieval_signal`` rather than driving gap_type.
        gap_type="content",
        top_score=finding.get("top_score"),
        nearest_kb_slug=kb_slug,
        language=finding.get("language"),
        support_case_id=case_id,
        diagnosis=finding["diagnosis"],
        audience=finding.get("audience"),
        question_key=_question_key(
            question=finding["question"],
            diagnosis=finding["diagnosis"],
            language=finding.get("language"),
            kb_slug=kb_slug,
            audience=finding.get("audience"),
        ),
        evidence={
            "message_ids": finding.get("message_ids", []),
            "articles": finding.get("articles", []),
            "missing_information": finding.get("missing_information"),
            "rationale": finding.get("rationale"),
            # Analyzer's nullable retrieval signal (hard/soft/None) — preserved
            # so a later view can show whether retrieval also scored poorly.
            "retrieval_signal": finding.get("gap_type"),
        },
    )


def _current_analysis_version() -> str | None:
    """Current analyzer version, or ``None`` when the (separate-lane) module is
    not importable. ``None`` forces a (re)analysis rather than a false no-op."""
    try:
        from app.services.support_case_analysis import ANALYSIS_VERSION

        return ANALYSIS_VERSION
    except Exception:
        return None


async def _lock_identity(db: AsyncSession, org_id: int, payload: SupportCasePayload) -> None:
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _identity_lock_key(org_id, payload)})


async def _delete_case_findings(db: AsyncSession, org_id: int, case_id: int) -> None:
    await db.execute(
        delete(PortalRetrievalGap).where(
            PortalRetrievalGap.org_id == org_id,
            PortalRetrievalGap.support_case_id == case_id,
        )
    )


async def _case_findings_count(db: AsyncSession, org_id: int, case_id: int) -> int:
    result = await db.execute(
        select(func.count(PortalRetrievalGap.id)).where(
            PortalRetrievalGap.org_id == org_id,
            PortalRetrievalGap.support_case_id == case_id,
        )
    )
    return result.scalar_one()


async def _org_policy(db: AsyncSession, org_id: int) -> Row[Any] | None:
    return (
        await db.execute(
            select(PortalOrg.telemetry_level, PortalOrg.platform_unlocked_features)
            .where(PortalOrg.id == org_id)
            .with_for_update(read=True)
        )
    ).one_or_none()


def _assert_support_policy(policy: Row[tuple[str, list[str]]] | None) -> None:
    require_full_telemetry(policy.telemetry_level if policy is not None else None)
    # assert_platform_unlocked reads platform_unlocked_features via getattr, so the
    # locked Row satisfies it exactly like a PortalOrg (the row is non-None here:
    # require_full_telemetry already raised on the absent-org case above).
    assert_platform_unlocked(policy, GAP_FEATURE)  # type: ignore[arg-type]


def _write_evidence(case: PortalSupportCase, *, body: dict, content_hash: str, connector_id: str | None) -> None:
    case.payload = body
    case.content_hash = content_hash
    case.connector_id = connector_id
    case.status = "incomplete" if body.get("complete") is False else "pending"
    case.analysis = None
    case.analysis_version = None


async def _prepare_evidence(
    db: AsyncSession,
    *,
    org_id: int,
    connector_id: str | None,
    created_by: str,
    kb_slug: str,
    payload: SupportCasePayload,
    body: dict,
    content_hash: str,
    current_version: str | None,
) -> tuple[int, bool, UpsertResult | None]:
    """Phase 1: durably checkpoint the evidence under the identity lock.

    Returns ``(case_id, changed, early_result)``. When ``early_result`` is not
    ``None`` the caller returns it directly (a no-op or an incomplete case that
    needs no analysis); the transaction is already committed. Otherwise the case
    is committed as ``pending`` with its stale findings dropped, ready for
    analysis outside any transaction.
    """
    await _lock_identity(db, org_id, payload)
    case = await _load_case_for_update(db, org_id=org_id, payload=payload)

    if case is not None and case.content_hash == content_hash:
        # Identical evidence. Only a case successfully analysed at the CURRENT
        # version is a true no-op; a failed/pending/stale-version case must
        # retry, and an incomplete case stays incomplete.
        if case.status == "analyzed" and current_version is not None and case.analysis_version == current_version:
            count = await _case_findings_count(db, org_id, case.id)
            await db.commit()
            return case.id, False, UpsertResult(case.id, "analyzed", False, count)
        if case.status == "incomplete":
            await db.commit()
            return case.id, False, UpsertResult(case.id, "incomplete", False, 0)
        # retryable: fall through to reset+analyse the same evidence
        await _delete_case_findings(db, org_id, case.id)
        _write_evidence(case, body=body, content_hash=content_hash, connector_id=connector_id)
        case_id, changed = case.id, False
    elif case is None:
        case = PortalSupportCase(
            org_id=org_id,
            kb_slug=kb_slug,
            connector_id=connector_id,
            source=payload.source,
            account_id=payload.account_id,
            external_id=payload.external_id,
            created_by=created_by,
            payload=body,
            content_hash=content_hash,
            status=payload.initial_status(),
        )
        db.add(case)
        await db.flush()
        case_id, changed = case.id, True
    else:
        # Existing case, changed evidence: the old analysis is stale.
        await _delete_case_findings(db, org_id, case.id)
        _write_evidence(case, body=body, content_hash=content_hash, connector_id=connector_id)
        case_id, changed = case.id, True

    if not payload.complete:
        # Incomplete evidence is stored but not analysed (contract: an
        # unavailable-content case is not a knowledge gap yet).
        await db.commit()
        return case_id, changed, UpsertResult(case_id, "incomplete", changed, 0)

    await db.commit()  # <-- evidence durable as 'pending'; stale findings gone
    return case_id, changed, None


async def _analyze_case(
    *, body: dict, kb_slug: str, zitadel_org_id: str, user_id: str | None, case_id: int
) -> tuple[list[dict], str | None, bool]:
    """Run the analyzer OUTSIDE any DB transaction. Returns
    ``(findings, version, failed)``. On import/analysis error, ``failed`` is
    True and no raw exception text or case body is logged (privacy)."""
    try:
        from app.services.support_case_analysis import (
            ANALYSIS_VERSION,
            analyze_support_case,
        )

        findings = await analyze_support_case(
            case=body, kb_slug=kb_slug, zitadel_org_id=zitadel_org_id, user_id=user_id
        )
        return findings, ANALYSIS_VERSION, False
    except Exception as exc:
        # No exc_info / str(exc): a parser error's message can carry customer
        # evidence. Safe type + identifiers only.
        logger.warning(
            "support_case_analysis_failed",
            case_id=case_id,
            error_type=type(exc).__name__,
            exc_info=(RuntimeError, RuntimeError("Support case analysis failed"), exc.__traceback__),
        )
        return [], None, True


def _apply_findings(
    db: AsyncSession, *, case: PortalSupportCase, findings: list[dict], created_by: str, kb_slug: str
) -> int:
    seen: set[str] = set()
    inserted = 0
    for finding in findings:
        diagnosis = finding.get("diagnosis")
        if diagnosis not in INBOX_DIAGNOSES:
            continue
        # Dedupe on the full grouping key so different audience/language within
        # one case are kept as distinct findings.
        key = _question_key(
            question=finding["question"],
            diagnosis=diagnosis,
            language=finding.get("language"),
            kb_slug=kb_slug,
            audience=finding.get("audience"),
        )
        if key in seen:
            continue
        seen.add(key)
        db.add(_finding_gap(org_id=case.org_id, user_id=created_by, kb_slug=kb_slug, case_id=case.id, finding=finding))
        inserted += 1
    case.analysis = list(findings)
    return inserted


async def upsert_support_case(
    db: AsyncSession,
    *,
    org_id: int,
    zitadel_org_id: str,
    telemetry_level: str,
    connector_id: str | None,
    created_by: str,
    kb_slug: str,
    payload: SupportCasePayload,
) -> UpsertResult:
    """Store one validated case and (re)run analysis when needed.

    Evidence is committed durably BEFORE any model/network work: phase 1 writes
    the case ``pending`` (dropping stale findings) under a per-identity advisory
    lock and commits; phase 2 analyses outside any transaction; phase 3 re-locks,
    reloads the current row, and applies results only if the evidence hash still
    matches — a stale run never overwrites newer evidence, revives a deleted
    case, or writes after a telemetry downgrade. A failed analysis leaves the
    case retryable (status ``failed``), and only a case analysed at the current
    version is a no-op.

    Raises ``SupportTelemetryError`` (before persistence or model calls, and
    again at the final write) and ``OversizedCaseError``.
    """
    require_full_telemetry(telemetry_level)
    payload.bind_kb(kb_slug)

    if len(payload.messages) > MAX_CASE_MESSAGES:
        raise OversizedCaseError(f"case has {len(payload.messages)} messages, limit {MAX_CASE_MESSAGES}")
    total_chars = sum(len(m.text) for m in payload.messages)
    if total_chars > MAX_CASE_TEXT_CHARS:
        raise OversizedCaseError(f"case has {total_chars} text chars, limit {MAX_CASE_TEXT_CHARS}")

    await set_tenant(db, org_id)
    _assert_support_policy(await _org_policy(db, org_id))
    body = payload.model_dump()
    content_hash = payload.content_hash()
    current_version = _current_analysis_version()

    case_id, changed, early = await _prepare_evidence(
        db,
        org_id=org_id,
        connector_id=connector_id,
        created_by=created_by,
        kb_slug=kb_slug,
        payload=payload,
        body=body,
        content_hash=content_hash,
        current_version=current_version,
    )
    if early is not None:
        return early

    # Org-owned connector imports run unattended: the retrieval identity must be
    # the tenant, not the connector's creator, so offboarding or suspending that
    # person cannot break future service analysis. ``created_by`` stays as audit
    # attribution on the case row and its findings. A user/transcript import
    # (connector_id is None) keeps the authenticated caller's identity.
    analysis_user_id = None if connector_id is not None else created_by
    findings, analyzed_version, failed = await _analyze_case(
        body=body, kb_slug=kb_slug, zitadel_org_id=zitadel_org_id, user_id=analysis_user_id, case_id=case_id
    )

    # Take the policy lock before the case lock, as in phase 1. Both telemetry and
    # the knowledge_gaps unlock are re-read here (freshly, FOR SHARE) so a revoke
    # committed while the analyzer ran is observed before any result is written.
    policy = await _org_policy(db, org_id)
    await _lock_identity(db, org_id, payload)
    case = await _load_case_for_update(db, org_id=org_id, payload=payload)
    if case is None or case.id != case_id:
        # Deleted (or superseded by a delete) during analysis — do not revive.
        await db.commit()
        return UpsertResult(case_id, "failed", changed, 0)
    if case.content_hash != content_hash:
        # Newer evidence arrived mid-analysis; our result is stale. Report the
        # case's real current state, never a false 'analyzed'.
        count = await _case_findings_count(db, org_id, case.id)
        status = case.status
        await db.commit()
        return UpsertResult(case.id, status, changed, count)  # type: ignore[arg-type]
    try:
        _assert_support_policy(policy)
    except (SupportTelemetryError, HTTPException):
        # Telemetry downgrade or knowledge_gaps revoke raced the analysis. The
        # evidence stays as committed 'pending' (no privacy purge here); commit
        # the findings-free transaction so the locks release, then surface the
        # 403 / telemetry error — model output is never written post-revoke.
        await db.commit()
        raise

    if failed:
        case.status = "failed"
        await db.commit()
        return UpsertResult(case.id, "failed", changed, 0)

    await _delete_case_findings(db, org_id, case.id)  # idempotent under redundant concurrent analysis
    inserted = _apply_findings(db, case=case, findings=findings, created_by=created_by, kb_slug=kb_slug)
    case.analysis_version = analyzed_version
    case.status = "analyzed"
    await db.commit()
    return UpsertResult(case.id, "analyzed", changed, inserted)


async def reconcile_support_cases(
    db: AsyncSession,
    *,
    org_id: int,
    connector_id: str,
    external_ids: list[str],
) -> int:
    """Delete this connector's cases whose external id is no longer in scope.

    Only ever called after a fully successful snapshot (the internal endpoint
    enforces that); a partial read must not reach here, or it would delete live
    cases as phantom deletions. Deleting a case cascades to its derived findings
    via the FK. Returns the number of cases removed.

    Gated on the same locked authoritative policy as the write path: a tenant
    whose telemetry was downgraded or whose ``knowledge_gaps`` unlock was revoked
    may not have a stale/partial connector import drive a destructive delete.
    """
    await set_tenant(db, org_id)
    _assert_support_policy(await _org_policy(db, org_id))
    keep = set(external_ids)
    result = await db.execute(
        select(PortalSupportCase).where(
            PortalSupportCase.org_id == org_id,
            PortalSupportCase.connector_id == connector_id,
        )
    )
    stale = [c for c in result.scalars().all() if c.external_id not in keep]
    for case in stale:
        await db.delete(case)
    if stale:
        await db.commit()
    return len(stale)


async def purge_support_cases_for_org(db: AsyncSession, org_id: int) -> int:
    """Remove all support evidence for an org (telemetry downgrade / privacy).

    The FK cascade drops the derived findings with the cases. Returns the
    number of cases removed.
    """
    await set_tenant(db, org_id)
    result = await db.execute(select(PortalSupportCase.id).where(PortalSupportCase.org_id == org_id))
    ids = [row[0] for row in result.all()]
    if not ids:
        return 0
    await db.execute(delete(PortalSupportCase).where(PortalSupportCase.org_id == org_id))
    await db.commit()
    return len(ids)


# ---------------------------------------------------------------------------
# Transcript normalization (native Whisper verbose JSON -> case payload)
# ---------------------------------------------------------------------------


class TranscriptError(Exception):
    """The transcript body could not be normalized into a case payload."""


_SHA256_HEX = re.compile(r"\A[0-9a-f]{64}\Z")

# Whisper segment end timestamps can overrun the measured audio duration by a
# small amount; permit up to two seconds before treating it as corrupt timing
# (contract: "allow up to two seconds of model timestamp overrun").
_DURATION_OVERRUN_TOLERANCE_S = 2.0


def normalize_whisper_transcript(transcript: dict) -> SupportCasePayload:
    """Turn a native Whisper verbose-JSON result (+ ``_source``) into a case.

    Identity is ``_source.sha256`` (a 64-hex recording hash), never the filename
    — two identical recording copies collapse to one case, and a filename change
    on the same recording does not create a duplicate. The filename and the
    processing timestamp are metadata only: neither is trusted as identity or as
    the call date. Mono audio cannot separate speakers, so every segment is an
    ``unknown``-role message. Segment timing is validated finite/non-negative by
    :class:`SupportCaseMessage`; here we also require monotonic ordering and
    that ends stay within ``audio_duration_seconds`` plus a small model-overrun
    tolerance.
    """
    source = transcript.get("_source")
    if not isinstance(source, dict):
        raise TranscriptError("transcript is missing the '_source' object")
    sha256 = source.get("sha256")
    if not isinstance(sha256, str) or not _SHA256_HEX.match(sha256):
        raise TranscriptError("_source.sha256 must be a 64-character lowercase hex recording hash")

    duration = source.get("audio_duration_seconds")
    max_end = None
    if isinstance(duration, (int, float)) and math.isfinite(duration):
        max_end = float(duration) + _DURATION_OVERRUN_TOLERANCE_S

    segments = transcript.get("segments") or []
    if not isinstance(segments, list):
        raise TranscriptError("segments must be a list")

    messages: list[SupportCaseMessage] = []
    prev_start = -1.0
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            raise TranscriptError(f"segment {i} is not an object")
        seg_text = seg.get("text")
        if not isinstance(seg_text, str):
            raise TranscriptError(f"segment {i} has no text")
        # A supplied diarization label is preserved verbatim; it is never turned
        # into a customer/agent role. Anything non-string (absent, numeric) stays
        # None with the role unknown.
        speaker = seg.get("speaker")
        try:
            message = SupportCaseMessage(
                id=f"seg-{i}",
                kind="transcript",
                role="unknown",
                text=seg_text,
                occurred_at=None,
                visibility="unknown",
                medium="call",
                thread_id=sha256,
                speaker_id=speaker if isinstance(speaker, str) else None,
                start_seconds=seg.get("start"),
                end_seconds=seg.get("end"),
            )
        except ValueError as exc:
            raise TranscriptError(f"segment {i}: {exc}") from exc
        # Monotonic ordering: a later segment may not start before an earlier
        # one, or the evidence timeline is scrambled.
        if message.start_seconds is not None:
            if message.start_seconds < prev_start:
                raise TranscriptError(
                    f"segment {i} starts at {message.start_seconds}s, before the previous segment ({prev_start}s)"
                )
            prev_start = message.start_seconds
        # Duration bound with model-overrun tolerance.
        if max_end is not None and message.end_seconds is not None and message.end_seconds > max_end:
            raise TranscriptError(
                f"segment {i} ends at {message.end_seconds}s, past the recording duration "
                f"({duration}s + {_DURATION_OVERRUN_TOLERANCE_S}s tolerance)"
            )
        messages.append(message)

    metadata = {
        "sha256": sha256,
        "filename": source.get("filename"),
        "audio_duration_seconds": source.get("audio_duration_seconds"),
        "processed_at": source.get("processed_at"),
        "service": source.get("service"),
        "requested_model": source.get("requested_model"),
        "input_normalization": source.get("input_normalization"),
        "speaker_labels": source.get("speaker_labels"),
        "reused_existing_result": source.get("reused_existing_result"),
    }

    return SupportCasePayload(
        source="audio",
        account_id="audio",
        external_id=sha256,
        subject=source.get("filename") or "",
        language=transcript.get("language"),
        source_url=None,
        source_updated_at=None,
        complete=bool(messages),
        incomplete_reasons=[] if messages else ["empty_transcript"],
        messages=messages,
        metadata=metadata,
    )
