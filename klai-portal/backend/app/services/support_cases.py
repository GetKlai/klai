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

import asyncio
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

import structlog
from fastapi import HTTPException
from sqlalchemy import Row, delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import set_tenant
from app.core.permissions import assert_platform_unlocked
from app.models.portal import PortalOrg, PortalUser
from app.models.retrieval_gaps import PortalRetrievalGap
from app.models.support_cases import PortalSupportCase
from app.schemas_support_cases import MessageRole, SupportCaseMessage, SupportCasePayload
from app.services.support_case_reviews import compute_analysis_revision, reviews_for_current_revision

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

# Reserved key under ``PortalSupportCase.reviews`` for the server-owned speaker-
# role overrides (a human's customer/agent correction on call evidence). It is
# the ONLY trusted source of a role override: the provider payload carries the
# original roles on every re-import, so the correction lives here, apart from the
# provider metadata a connector controls. Never collides with a per-finding key
# (``"{revision}:{index}"``) or the ``_reference`` key.
ROLE_OVERRIDE_KEY = "_role_overrides"


class SupportTelemetryError(Exception):
    """Org telemetry level forbids storing or viewing literal support evidence."""


class OversizedCaseError(Exception):
    """Case payload exceeds ``MAX_CASE_BYTES``."""


class StaleCaseError(Exception):
    """The stored evidence moved (or the case was deleted) since the caller read
    it, so a derived write built on the old version must not land. Used by the
    role-correction and reanalyse paths to refuse silently overwriting a fresher
    connector payload."""


@dataclass(frozen=True, slots=True)
class UpsertResult:
    """Return shape of :func:`upsert_support_case` — the import response."""

    case_id: int
    status: Literal["incomplete", "pending", "analyzed", "failed"]
    changed: bool
    findings_count: int
    # True only when a FORCED reanalysis ran but the analyzer failed and the case's
    # prior analysis/findings were preserved (not dropped to 'failed'), so a caller
    # can surface a retryable failure without losing good data. Always False on the
    # normal import path.
    reanalysis_failed: bool = False


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


def _question_key(*, question: str, language: str | None, kb_slug: str | None, audience: str | None) -> str:
    """Grouping key for a customer NEED, shared by every gap producer (support
    case findings and chat/widget/MCP telemetry — SPEC-RAG-GAP-GROUPING).

    Equivalent questions collapse into one inbox group, split only on KB,
    language and audience: the axes that describe a genuinely different
    editorial job. Diagnosis (missing/incomplete/...) and gap_type (hard/soft)
    are dropped on purpose — they describe HOW a gap was detected, not WHAT the
    customer needs, so a "missing" and an "incomplete" about the same question
    belong in one group. A differently-worded paraphrase does not share this
    literal key; ``support_gap_grouping.group_findings`` is what folds a
    paraphrase (or a different producer's row) into an existing key.
    """
    return "|".join([_normalize_question(question), language or "", kb_slug or "", audience or ""])


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
        taxonomy_node_ids=finding.get("taxonomy_node_ids"),
        support_case_id=case_id,
        diagnosis=finding["diagnosis"],
        audience=finding.get("audience"),
        # A verified merge from the grouping helper folds this finding into an
        # existing open group by carrying that group's key; the row still stores
        # the case's own question verbatim. Absent an override the key is computed
        # from this finding as before. Only the trusted helper ever sets it.
        question_key=finding.get("group_question_key")
        or _question_key(
            question=finding["question"],
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


async def _read_run_token(db: AsyncSession, *, org_id: int, case_id: int) -> str | None:
    """The case row's Postgres ``xmin`` as an optimistic run token.

    A forced reanalysis of unchanged evidence cannot use the content hash to tell
    a stale run from a fresh one — both hash the same. ``xmin`` changes on every
    row write, so a newer run's phase-1 checkpoint bumps it; an older run then
    reads a different token in phase 3 and discards its result instead of
    clobbering the fresher one. Read in-transaction on the just-written row, so it
    equals the value the row carries once committed.
    """
    row = (
        await db.execute(
            text("SELECT xmin::text AS token FROM portal_support_cases WHERE id = :id AND org_id = :org"),
            {"id": case_id, "org": org_id},
        )
    ).one_or_none()
    return row.token if row is not None else None


async def _bump_run_token(db: AsyncSession, org_id: int, case_id: int) -> None:
    """Give this run a distinct token without touching the preserved analysis.

    An explicit UPDATE writes a new row version (new ``xmin``) even when the value
    is unchanged, so a forced reanalysis that keeps the prior findings still gets
    its own optimistic token for the phase-3 supersession check.
    """
    await db.execute(
        text("UPDATE portal_support_cases SET updated_at = now() WHERE id = :id AND org_id = :org"),
        {"id": case_id, "org": org_id},
    )


async def _delete_case_findings(db: AsyncSession, org_id: int, case_id: int) -> None:
    await db.execute(
        delete(PortalRetrievalGap).where(
            PortalRetrievalGap.org_id == org_id,
            PortalRetrievalGap.support_case_id == case_id,
        )
    )


async def _snapshot_closures(db: AsyncSession, org_id: int, case_id: int) -> dict[str, tuple]:
    """Closed derived rows of a case, keyed by the finding's verbatim question.

    A forced reanalysis of unchanged evidence rebuilds the case's findings, which
    would reopen a manually-closed or review-dismissed finding the analyzer still
    returns. Capturing the closures first lets them be re-applied to the matching
    new rows, so a close survives until the content genuinely covers the question.
    Keyed on ``query_text`` (the finding's own question, preserved verbatim), not
    ``question_key``: grouping can fold a finding under a different group key on the
    new run, and that must not silently drop the close.
    """
    rows = (
        (
            await db.execute(
                select(PortalRetrievalGap).where(
                    PortalRetrievalGap.org_id == org_id,
                    PortalRetrievalGap.support_case_id == case_id,
                    PortalRetrievalGap.resolved_at.isnot(None),
                )
            )
        )
        .scalars()
        .all()
    )
    return {row.query_text: (row.resolved_at, row.resolved_by, row.resolved_by_user_id) for row in rows}


async def _reapply_closures(db: AsyncSession, org_id: int, case_id: int, closures: dict[str, tuple]) -> None:
    await db.flush()  # the freshly added rows must be queryable to match by question
    rows = (
        (
            await db.execute(
                select(PortalRetrievalGap).where(
                    PortalRetrievalGap.org_id == org_id,
                    PortalRetrievalGap.support_case_id == case_id,
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        closure = closures.get(row.query_text)
        if closure is not None:
            row.resolved_at, row.resolved_by, row.resolved_by_user_id = closure


async def _case_findings_count(db: AsyncSession, org_id: int, case_id: int) -> int:
    result = await db.execute(
        select(func.count(PortalRetrievalGap.id)).where(
            PortalRetrievalGap.org_id == org_id,
            PortalRetrievalGap.support_case_id == case_id,
        )
    )
    return result.scalar_one()


# The grouping helper rejects more than this many candidate groups; the caller
# bounds the query to match (contract: "<= 100 existing open support groups").
GROUPING_CANDIDATE_LIMIT = 100


async def _open_group_candidates(
    db: AsyncSession,
    *,
    org_id: int,
    kb_slug: str | None,
    exclude_case_id: int | None,
    exclude_gap_id: int | None = None,
) -> list[dict]:
    """Existing OPEN groups for this org+KB, across every producer (support
    case findings and chat/widget/MCP telemetry alike — SPEC-RAG-GAP-GROUPING),
    minus the case being (re)analysed, if any.

    One row per persisted ``question_key`` with the fields the grouping judge
    compares. Bounded so a large inbox cannot build an unbounded prompt.
    """
    conditions = [
        PortalRetrievalGap.org_id == org_id,
        PortalRetrievalGap.nearest_kb_slug == kb_slug,
        PortalRetrievalGap.resolved_at.is_(None),
        PortalRetrievalGap.question_key.isnot(None),
    ]
    if exclude_gap_id is not None:
        # The row asking the question is already in the table: without this it
        # occupies one of the bounded candidate slots and can push a genuinely
        # matching group out of the prompt.
        conditions.append(PortalRetrievalGap.id != exclude_gap_id)
    if exclude_case_id is not None:
        # NULL-safe: a chat/telemetry row has no support_case_id, so a plain
        # ``!=`` (which is NULL, i.e. excluded, against NULL) would silently
        # drop every non-support candidate.
        conditions.append(
            or_(
                PortalRetrievalGap.support_case_id.is_(None),
                PortalRetrievalGap.support_case_id != exclude_case_id,
            )
        )
    stmt = (
        select(
            PortalRetrievalGap.question_key,
            func.max(PortalRetrievalGap.query_text).label("question"),
            func.max(PortalRetrievalGap.language).label("language"),
            func.max(PortalRetrievalGap.audience).label("audience"),
        )
        .where(*conditions)
        .group_by(PortalRetrievalGap.question_key)
        .limit(GROUPING_CANDIDATE_LIMIT)
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "question_key": r.question_key,
            "question": r.question,
            "language": r.language,
            "audience": r.audience,
        }
        for r in rows
    ]


async def _grouped_findings(
    db: AsyncSession, *, org_id: int, kb_slug: str, exclude_case_id: int, findings: list[dict]
) -> list[dict]:
    """Findings with verified merges into existing open groups stamped on.

    Reads the candidate groups then releases the snapshot BEFORE the model call,
    so no lock is held across it; phase 3 re-takes the lock and validates the run
    afterwards. Grouping is additive and conservative — if the module is absent or
    the judge fails, the findings simply stand on their own (logged, never lost).
    """
    candidates = await _open_group_candidates(db, org_id=org_id, kb_slug=kb_slug, exclude_case_id=exclude_case_id)
    had_existing_candidates = bool(candidates)
    candidate_keys = {candidate["question_key"] for candidate in candidates}
    for index, finding in enumerate(findings):
        if finding.get("diagnosis") not in INBOX_DIAGNOSES:
            continue
        key = _question_key(
            question=finding["question"],
            language=finding.get("language"),
            kb_slug=kb_slug,
            audience=finding.get("audience"),
        )
        if key in candidate_keys:
            continue
        candidates.append(
            {
                "question_key": key,
                "question": finding["question"],
                "language": finding.get("language"),
                "audience": finding.get("audience"),
                "finding_index": index,
            }
        )
        candidate_keys.add(key)
    # Close the read-only snapshot (commit, not rollback: rollback would expire a
    # caller's still-in-use rows, e.g. the rescore loop's case list) so no lock or
    # transaction is held across the model call.
    await db.commit()
    if not had_existing_candidates and len(candidates) < 2:
        return findings
    try:
        from app.services.support_gap_grouping import group_findings

        return await group_findings(findings, candidates)
    except Exception as exc:
        # Model-controlled keys can contain customer text; retain stack frames
        # with a constant exception message, as for analysis failures below.
        logger.warning(
            "support_gap_grouping_failed",
            error_type=type(exc).__name__,
            exc_info=(RuntimeError, RuntimeError("Support case grouping failed"), exc.__traceback__),
        )
        return findings


async def _classify_findings(*, zitadel_org_id: str, kb_slug: str, findings: list[dict]) -> list[dict]:
    actionable = [f for f in findings if f.get("diagnosis") in INBOX_DIAGNOSES]
    if not actionable:
        return findings
    from app.services.knowledge_ingest_client import classify_gap_taxonomy

    node_id_lists = await asyncio.gather(
        *(classify_gap_taxonomy(zitadel_org_id, kb_slug, f["question"]) for f in actionable)
    )
    try:
        async with asyncio.timeout(10.0):
            for index, finding in enumerate(actionable):
                if node_id_lists[index] is None:
                    node_id_lists[index] = await classify_gap_taxonomy(zitadel_org_id, kb_slug, finding["question"])
    except TimeoutError:
        pass  # Unfinished classifications retain None and receive a visible limitation below.
    for finding, node_ids in zip(actionable, node_id_lists, strict=True):
        if node_ids is None:
            limitation = (
                "Taxonomy classification was unavailable within the retry budget, "
                "so this finding may be missing topic labels."
            )
            limitations = finding.setdefault("comparison_limitations", [])
            if limitation not in limitations:
                limitations.append(limitation)
        elif node_ids:
            finding["taxonomy_node_ids"] = node_ids
    return findings


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


def _segment_fingerprint(message: SupportCaseMessage) -> str:
    """Role-independent identity of a message segment.

    Covers everything a role override must stay pinned to (the source text, kind,
    medium, timing and speaker) and deliberately excludes ``role`` — role is the
    field the override changes, so the fingerprint of the provider-original message
    and the corrected one are identical, which is how a re-import matches.
    """
    material = json.dumps(
        {
            "text": message.text,
            "kind": message.kind,
            "medium": message.medium,
            "start_seconds": message.start_seconds,
            "end_seconds": message.end_seconds,
            "speaker_id": message.speaker_id,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _overlay_role_overrides(payload: SupportCasePayload, overrides: dict) -> None:
    """Apply the server-owned role overrides onto ``payload``'s messages in place.

    Only a message whose id AND role-independent fingerprint still match an override
    is relabelled, so a changed, deleted or reused-id segment keeps the provider's
    role — a stale human judgement never rides onto different evidence. Role is the
    only field touched.
    """
    if not overrides:
        return
    for message in payload.messages:
        override = overrides.get(message.id)
        if override is not None and override.get("fingerprint") == _segment_fingerprint(message):
            message.role = cast(MessageRole, override["role"])


def _merge_role_overrides(
    stored: dict, payload: SupportCasePayload, new_roles: dict[str, str], *, reviewer: str | None
) -> dict:
    """Fold a human's trusted role corrections into the server-owned override map.

    ``provider_role`` keeps the true origin across repeated corrections, and the
    fingerprint pins each override to the exact segment identity so a later content
    change drops it. Reviewer and time are server-derived here, never read from the
    provider payload — a connector cannot forge the audit.
    """
    by_id = {m.id: m for m in payload.messages}
    now = datetime.now(tz=UTC).isoformat()
    merged = dict(stored)
    for msg_id, role in new_roles.items():
        message = by_id.get(msg_id)
        if message is None:
            continue
        prior = stored.get(msg_id)
        merged[msg_id] = {
            "role": role,
            "provider_role": prior["provider_role"] if prior else message.role,
            "fingerprint": _segment_fingerprint(message),
            "reviewed_by": reviewer,
            "reviewed_at": now,
        }
    return merged


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
    current_version: str | None,
    force_reanalysis: bool,
    expected_content_hash: str | None,
    role_overrides: dict[str, str] | None,
    role_override_reviewer: str | None,
) -> tuple[int, bool, UpsertResult | None, str | None, bool, dict, str]:
    """Phase 1: durably checkpoint the evidence under the identity lock.

    Returns ``(case_id, changed, early_result, run_token, preserve, body, hash)``.
    ``body``/``hash`` are the EFFECTIVE evidence: the server-owned speaker-role
    overrides are overlaid onto the payload here, under the identity lock and before
    hashing, so the stored hash and the analysed body describe the same effective
    evidence. When ``early_result`` is not ``None`` the caller returns it directly (a
    no-op or an incomplete case that needs no analysis); the transaction is already
    committed. Otherwise the case is committed ready for analysis outside any
    transaction and ``run_token`` is its optimistic supersession token.

    ``preserve`` is True only for a forced reanalysis of a case that already has a
    good analysis: the prior analysis/findings/reviews are kept and only the run
    token is bumped, so a failed reanalysis does not blank good data. Every other
    path drops stale findings and commits the case ``pending`` as before.
    """
    await _lock_identity(db, org_id, payload)
    case = await _load_case_for_update(db, org_id=org_id, payload=payload)

    if expected_content_hash is not None and (case is None or case.content_hash != expected_content_hash):
        # A caller building on a specific version (a role correction, a reanalyse)
        # required the stored evidence to still be that version. It moved or the
        # case was deleted, so refuse rather than clobber the fresher payload.
        await db.rollback()  # releases the identity advisory xact lock
        raise StaleCaseError("stored evidence changed since it was read")

    # Overlay the trusted, server-owned role overrides before hashing. A connector
    # re-import carries the ORIGINAL roles, so the human's correction is re-applied
    # from ``reviews`` (not the provider metadata) and only onto an unchanged segment
    # — the effective evidence then hashes identically to the corrected case, so a
    # repeat import is an idempotent no-op instead of erasing the attribution.
    overrides = dict((case.reviews or {}).get(ROLE_OVERRIDE_KEY, {})) if case is not None else {}
    if role_overrides and case is not None:
        overrides = _merge_role_overrides(overrides, payload, role_overrides, reviewer=role_override_reviewer)
        case.reviews = {**(case.reviews or {}), ROLE_OVERRIDE_KEY: overrides}
    _overlay_role_overrides(payload, overrides)
    body = payload.model_dump()
    content_hash = payload.content_hash()

    preserve = False
    if case is not None and case.content_hash == content_hash:
        # Identical evidence. Without force: only a case successfully analysed at
        # the CURRENT version is a true no-op; a failed/pending/stale-version case
        # retries and an incomplete case stays incomplete. With force: always
        # (re)analyse, keeping a good prior analysis until the new one lands.
        if not force_reanalysis:
            if case.status == "analyzed" and current_version is not None and case.analysis_version == current_version:
                count = await _case_findings_count(db, org_id, case.id)
                await db.commit()
                return case.id, False, UpsertResult(case.id, "analyzed", False, count), None, False, body, content_hash
            if case.status == "incomplete":
                await db.commit()
                return case.id, False, UpsertResult(case.id, "incomplete", False, 0), None, False, body, content_hash
            await _delete_case_findings(db, org_id, case.id)
            _write_evidence(case, body=body, content_hash=content_hash, connector_id=connector_id)
            case_id, changed = case.id, False
        elif case.analysis is not None:
            # Forced reanalysis with something worth protecting: keep it, only
            # bump the token so a concurrent forced run is distinguishable.
            await _bump_run_token(db, org_id, case.id)
            case_id, changed, preserve = case.id, False, True
        else:
            # Forced, but nothing to preserve (pending/failed): reset + analyse.
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
        return case_id, changed, UpsertResult(case_id, "incomplete", changed, 0), None, False, body, content_hash

    await db.flush()
    run_token = await _read_run_token(db, org_id=org_id, case_id=case_id)
    await db.commit()  # <-- evidence durable; token pinned for the phase-3 check
    return case_id, changed, None, run_token, preserve, body, content_hash


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
        # Dedupe on the grouping key so different audience/language within one
        # case are kept as distinct findings; diagnosis no longer separates
        # them here either — a "missing" and an "incomplete" about the same
        # question in one case are the same need, same as across cases.
        key = _question_key(
            question=finding["question"],
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


def _effective_diagnosis(machine: str | None, decision: str, corrected: str | None) -> str | None:
    """The diagnosis that drives a reviewed finding's inbox visibility.

    The machine analysis is never edited; this is the human override that decides
    whether the finding is actionable. ``incorrect`` without a correction is an
    explicit dismiss (legacy shape), ``uncertain`` keeps it out of the inbox, and
    ``correct`` restores the machine's own diagnosis. A correction supplied on any
    verdict wins — that is how an uncertain finding is promoted into a candidate.
    """
    if decision == "incorrect":
        return corrected
    if decision == "uncertain":
        return None
    return corrected if corrected is not None else machine


async def apply_review_visibility(
    db: AsyncSession,
    *,
    case: PortalSupportCase,
    finding: dict,
    decision: str,
    corrected_diagnosis: str | None,
    reviewer_user_id: int | None,
) -> None:
    """Reconcile one finding's derived gap row(s) to the reviewer's verdict.

    Runs inside the review's locked transaction. The reviewer's override — not the
    machine label — decides whether the finding sits in the actionable inbox: an
    actionable effective diagnosis keeps/creates an open row, a non-actionable one
    dismisses it (``resolved_by='review'``, distinct from a content closure). A
    genuine promotion (the machine produced no row) creates one; a content closure
    by manual/rescorer is left intact, since content closure is separate from
    dismissal. The finding is matched by its verbatim question, which a correction
    never rewrites.
    """
    effective = _effective_diagnosis(finding.get("diagnosis"), decision, corrected_diagnosis)
    visible = effective in INBOX_DIAGNOSES
    question = finding["question"]
    rows = (
        (
            await db.execute(
                select(PortalRetrievalGap).where(
                    PortalRetrievalGap.org_id == case.org_id,
                    PortalRetrievalGap.support_case_id == case.id,
                    PortalRetrievalGap.query_text == question,
                )
            )
        )
        .scalars()
        .all()
    )

    if not visible:
        for row in rows:
            if row.resolved_at is None:  # only close still-open rows; keep a content closure as it is
                row.resolved_at = datetime.now(tz=UTC)
                row.resolved_by = "review"
                row.resolved_by_user_id = reviewer_user_id
        return

    # Visible: the finding belongs in the inbox under the effective diagnosis.
    review_managed = [row for row in rows if row.resolved_at is None or row.resolved_by == "review"]
    if not review_managed:
        if finding.get("diagnosis") not in INBOX_DIAGNOSES:
            # Promotion: the machine produced no actionable row, so create one now.
            db.add(
                _finding_gap(
                    org_id=case.org_id,
                    user_id=case.created_by,
                    kb_slug=case.kb_slug,
                    case_id=case.id,
                    finding={**finding, "diagnosis": effective},
                )
            )
        # else: the machine row exists but was content-closed — leave that closure.
        return

    for row in review_managed:
        row.resolved_at = None  # restore/reopen a review-dismissed finding
        row.resolved_by = None
        row.resolved_by_user_id = None
        # A correction changes which diagnosis is shown, but diagnosis is not
        # part of ``question_key`` any more (SPEC-RAG-GAP-GROUPING) — the row
        # stays in the same group regardless of which diagnosis it now carries.
        row.diagnosis = effective


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
    force_reanalysis: bool = False,
    expected_content_hash: str | None = None,
    role_overrides: dict[str, str] | None = None,
    role_override_reviewer: str | None = None,
) -> UpsertResult:
    """Store one validated case and (re)run analysis when needed.

    ``role_overrides`` (message_id -> business role) is the ONLY trusted way to set
    a server-owned speaker-role override; it is passed exclusively by the role-
    correction route, never by a connector import. The override is merged into the
    case's ``reviews`` under the identity lock and re-applied to the payload on every
    subsequent import, so a connector re-import carrying the original roles cannot
    erase the human's correction. ``role_override_reviewer`` stamps the audit.

    ``force_reanalysis`` re-runs the analyzer even when the evidence hash and
    analyzer version are unchanged — the KB behind a case can move without the
    case moving, and a role correction or a manual reanalyse must reflect that. A
    forced run keeps a good prior analysis until the new one succeeds, and uses a
    per-run token so a slower same-hash run cannot overwrite a fresher one.

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
    current_version = _current_analysis_version()

    # Phase 1 overlays the trusted role overrides and returns the EFFECTIVE body +
    # content hash, so analysis and the phase-3 supersession check see the same
    # evidence the row now stores.
    case_id, changed, early, run_token, preserve, body, content_hash = await _prepare_evidence(
        db,
        org_id=org_id,
        connector_id=connector_id,
        created_by=created_by,
        kb_slug=kb_slug,
        payload=payload,
        current_version=current_version,
        force_reanalysis=force_reanalysis,
        expected_content_hash=expected_content_hash,
        role_overrides=role_overrides,
        role_override_reviewer=role_override_reviewer,
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

    # Fold verified matches into existing open groups — a read-then-release model
    # call with no lock held — before re-taking the lock for the run-token check.
    if not failed and findings:
        findings = await _grouped_findings(
            db, org_id=org_id, kb_slug=kb_slug, exclude_case_id=case_id, findings=findings
        )
        findings = await _classify_findings(zitadel_org_id=zitadel_org_id, kb_slug=kb_slug, findings=findings)

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
    current_token = await _read_run_token(db, org_id=org_id, case_id=case.id)
    if run_token is not None and current_token != run_token:
        # A newer (forced) run superseded this run's phase-1 checkpoint while it was
        # analysing. Same hash, so the content-hash guard above cannot catch it —
        # the token does. Discard our result and report the case's current state.
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
        if preserve:
            # A forced reanalysis failed; the prior analysis/findings/reviews were
            # never dropped. Keep them and flag the retryable failure instead of
            # blanking good data (changed-KB/failed-analysis semantics).
            count = await _case_findings_count(db, org_id, case.id)
            await db.commit()
            return UpsertResult(case.id, case.status, changed, count, reanalysis_failed=True)  # type: ignore[arg-type]
        case.status = "failed"
        await db.commit()
        return UpsertResult(case.id, "failed", changed, 0)

    # Preserve manual/review closures across a reanalysis of UNCHANGED evidence:
    # a still-returned finding keeps its close. Changed evidence never preserves —
    # the closures no longer describe the new findings.
    closures = await _snapshot_closures(db, org_id, case.id) if preserve else {}
    await _delete_case_findings(db, org_id, case.id)  # idempotent under redundant concurrent analysis
    inserted = _apply_findings(db, case=case, findings=findings, created_by=created_by, kb_slug=kb_slug)
    case.analysis_version = analyzed_version
    case.status = "analyzed"
    revision = compute_analysis_revision(
        content_hash=case.content_hash, analysis_version=case.analysis_version, analysis=case.analysis
    )
    current_reviews = reviews_for_current_revision(case.reviews, revision, len(findings))
    for finding, review in zip(findings, current_reviews, strict=True):
        if review is None:
            continue
        reviewer_id = (
            await db.execute(
                select(PortalUser.id).where(
                    PortalUser.org_id == org_id, PortalUser.zitadel_user_id == review["reviewed_by"]
                )
            )
        ).scalar_one_or_none()
        await apply_review_visibility(
            db,
            case=case,
            finding=finding,
            decision=review["decision"],
            corrected_diagnosis=review.get("corrected_diagnosis"),
            reviewer_user_id=reviewer_id,
        )
    if closures:
        await _reapply_closures(db, org_id, case.id, closures)
    if any(current_reviews):
        await db.flush()
        inserted = await _case_findings_count(db, org_id, case.id)
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

# Business roles a transcript segment may carry. Diarization only separates
# voices; who is the customer and who the agent is knowledge the authorized
# importer supplies, never something guessed from the speaker index.
_TRANSCRIPT_ROLES: frozenset[str] = frozenset({"customer", "agent", "unknown"})


def _parse_speaker_roles(source: dict) -> dict[str, str]:
    """Validated ``_source.speaker_roles`` (speaker_id -> role), or ``{}``.

    Trust boundary: only an authorized import caller reaches here, but a
    malformed or out-of-enum mapping is still rejected rather than silently
    dropped — a wrong role would mislabel evidence.
    """
    raw = source.get("speaker_roles")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise TranscriptError("_source.speaker_roles must be an object mapping speaker_id to role")
    roles: dict[str, str] = {}
    for speaker_id, role in raw.items():
        if not isinstance(speaker_id, str) or role not in _TRANSCRIPT_ROLES:
            raise TranscriptError(f"_source.speaker_roles[{speaker_id!r}] is not a valid speaker->role entry")
        roles[speaker_id] = role
    return roles


def _segment_role(
    *, index: int, seg: dict, speaker_id: str | None, speaker_roles: dict[str, str], role_by_speaker: dict[str, str]
) -> MessageRole:
    """Resolve one segment's business role. Explicit segment role wins over the
    speaker mapping; the two must agree; a speaker must not carry two roles.

    Never inferred from position — an unmapped, unlabelled segment stays unknown.
    """
    explicit = seg.get("role")
    if explicit is not None and explicit not in _TRANSCRIPT_ROLES:
        raise TranscriptError(f"segment {index} role {explicit!r} is not customer/agent/unknown")
    mapped = speaker_roles.get(speaker_id) if speaker_id is not None else None
    if explicit is not None and mapped is not None and explicit != mapped:
        raise TranscriptError(
            f"segment {index} role {explicit!r} conflicts with speaker_roles[{speaker_id!r}]={mapped!r}"
        )
    role = explicit if explicit is not None else (mapped if mapped is not None else "unknown")
    # A single voice cannot be both customer and agent across the call.
    if speaker_id is not None and role != "unknown":
        prior = role_by_speaker.get(speaker_id)
        if prior is not None and prior != role:
            raise TranscriptError(f"speaker {speaker_id!r} is assigned both {prior!r} and {role!r}")
        role_by_speaker[speaker_id] = role
    return cast(MessageRole, role)


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

    speaker_roles = _parse_speaker_roles(source)

    segments = transcript.get("segments") or []
    if not isinstance(segments, list):
        raise TranscriptError("segments must be a list")

    messages: list[SupportCaseMessage] = []
    prev_start = -1.0
    role_by_speaker: dict[str, str] = {}
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
        speaker_id = speaker if isinstance(speaker, str) else None
        role = _segment_role(
            index=i, seg=seg, speaker_id=speaker_id, speaker_roles=speaker_roles, role_by_speaker=role_by_speaker
        )
        try:
            message = SupportCaseMessage(
                id=f"seg-{i}",
                kind="transcript",
                role=role,
                text=seg_text,
                occurred_at=None,
                visibility="unknown",
                medium="call",
                thread_id=sha256,
                speaker_id=speaker_id,
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
        # The validated speaker_id->role mapping the importer supplied, kept for
        # audit alongside the per-message roles it produced.
        "speaker_roles": speaker_roles or None,
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
