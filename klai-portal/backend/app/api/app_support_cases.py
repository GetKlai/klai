"""Transcript import into the support-case gap workflow (SPEC-RAG-SUPPORT-GAP).

``POST /api/app/knowledge-bases/{kb_slug}/support-cases/transcript`` accepts a
native Whisper verbose-JSON result plus ``_source`` and normalizes it into the
same case contract as the HubSpot connector, then runs the same analysis. It
publishes no knowledge — support-call text is restricted evidence, never KB
content.

Router-level ``get_kb_with_access`` enforces the KB personal-firewall for every
route under the prefix (SPEC-PORTAL-KB-OWNERSHIP-001); this handler additionally
rejects a personal KB outright, since only organization-owned KBs may hold
support evidence.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_kb_with_access, require_capability
from app.core.database import get_db
from app.core.permissions import (
    UserPermissions,
    assert_platform_unlocked,
    get_caller,
    require_platform_unlocked,
)
from app.core.profiles import Capability
from app.models.knowledge_bases import PortalKnowledgeBase
from app.models.portal import PortalOrg, PortalUser
from app.models.support_cases import PortalSupportCase
from app.schemas_support_cases import MessageRole, SupportCasePayload
from app.services.access import is_personal_kb
from app.services.support_case_reviews import (
    REFERENCE_KEY,
    CorrectedDiagnosis,
    ReviewDecision,
    compute_analysis_revision,
    review_key,
    reviews_for_current_revision,
)
from app.services.support_cases import (
    GAP_FEATURE,
    OversizedCaseError,
    StaleCaseError,
    SupportTelemetryError,
    TranscriptError,
    UpsertResult,
    apply_review_visibility,
    normalize_whisper_transcript,
    upsert_support_case,
)

router = APIRouter(
    prefix="/api/app/knowledge-bases/{kb_slug}/support-cases",
    tags=["gaps"],
    dependencies=[
        Depends(require_capability(Capability.KB_GAPS)),
        Depends(require_platform_unlocked("knowledge_gaps")),
        Depends(get_kb_with_access),
    ],
)


class TranscriptImportRequest(BaseModel):
    transcript: dict = Field(description="Native Whisper verbose-JSON result including a `_source` object")


class TranscriptImportResponse(BaseModel):
    case_id: int
    status: str
    changed: bool
    findings_count: int


@router.post("/transcript", response_model=TranscriptImportResponse)
async def import_transcript(
    body: TranscriptImportRequest,
    # kb_slug (the {kb_slug} path param) is resolved + access-checked by the
    # get_kb_with_access dependency, which returns the KB used below.
    kb: PortalKnowledgeBase = Depends(get_kb_with_access),
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> TranscriptImportResponse:
    if is_personal_kb(kb):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Support cases require an organization-owned knowledge base",
        )

    org = await db.get(PortalOrg, perms.org_id)
    if org is None:  # pragma: no cover - perms already resolved the org
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Organisation not found")

    try:
        payload = normalize_whisper_transcript(body.transcript)
    except TranscriptError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    try:
        result = await upsert_support_case(
            db,
            org_id=org.id,
            zitadel_org_id=org.zitadel_org_id,
            telemetry_level=org.telemetry_level,
            connector_id=None,
            created_by=perms.user_id,
            kb_slug=kb.slug,
            payload=payload,
        )
    except SupportTelemetryError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "telemetry_level_forbids_support_evidence"},
        ) from exc
    except OversizedCaseError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"error_code": "support_case_too_large"},
        ) from exc

    return TranscriptImportResponse(
        case_id=result.case_id,
        status=result.status,
        changed=result.changed,
        findings_count=result.findings_count,
    )


# --------------------------------------------------------------------------- #
# Case list — the review inbox, including analyzed no-gap / uncertain cases
# --------------------------------------------------------------------------- #


class SupportCaseListItem(BaseModel):
    id: int
    kb_slug: str
    subject: str
    source: str
    status: str
    imported_at: datetime
    # Distinct message mediums in the case (call/email/chat/unknown).
    mediums: list[str]
    # Analysis outcome counts. All zero when analysis is NULL (pending/failed),
    # so a not-yet-analysed case is distinguishable from an analysed no-gap one.
    question_count: int
    uncertain_count: int
    # Findings that carry a human review for the CURRENT analysis revision.
    reviewed_count: int


class SupportCaseListResponse(BaseModel):
    cases: list[SupportCaseListItem]
    total: int


def _require_full_telemetry(telemetry_level: str | None) -> None:
    if telemetry_level != "full":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "telemetry_level_forbids_support_evidence"},
        )


def _list_item(case: PortalSupportCase) -> SupportCaseListItem:
    analysis = case.analysis or []
    mediums = sorted({m.get("medium") for m in (case.payload.get("messages") or []) if m.get("medium")})
    reviewed = 0
    if analysis:
        revision = compute_analysis_revision(
            content_hash=case.content_hash, analysis_version=case.analysis_version, analysis=analysis
        )
        reviewed = sum(r is not None for r in reviews_for_current_revision(case.reviews, revision, len(analysis)))
    return SupportCaseListItem(
        id=case.id,
        kb_slug=case.kb_slug,
        subject=case.payload.get("subject", ""),
        source=case.source,
        status=case.status,
        imported_at=case.imported_at,
        mediums=mediums,
        question_count=len(analysis),
        uncertain_count=sum(1 for f in analysis if f.get("diagnosis") == "uncertain"),
        reviewed_count=reviewed,
    )


@router.get("", response_model=SupportCaseListResponse)
async def list_support_cases(
    kb: PortalKnowledgeBase = Depends(get_kb_with_access),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> SupportCaseListResponse:
    """List a KB's imported support cases for review (SPEC-RAG-SUPPORT-GAP).

    Includes every status and every analysis outcome — an analysed case with no
    gap and one with only uncertain findings both belong in the reviewer's inbox,
    not just cases that produced a gap row. Telemetry policy is re-checked on read
    and support evidence only ever lives on an organization-owned KB, so a
    personal or inaccessible KB never leaks here (the router firewall plus the
    explicit org predicate bound it). Stable order: newest import first.
    """
    telemetry = (
        await db.execute(select(PortalOrg.telemetry_level).where(PortalOrg.id == perms.org_id))
    ).scalar_one_or_none()
    _require_full_telemetry(telemetry)
    if is_personal_kb(kb):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Support case not found")

    total = (
        await db.execute(
            select(func.count())
            .select_from(PortalSupportCase)
            .where(PortalSupportCase.org_id == perms.org_id, PortalSupportCase.kb_slug == kb.slug)
        )
    ).scalar_one()
    result = await db.execute(
        select(PortalSupportCase)
        .where(PortalSupportCase.org_id == perms.org_id, PortalSupportCase.kb_slug == kb.slug)
        .order_by(PortalSupportCase.imported_at.desc(), PortalSupportCase.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return SupportCaseListResponse(cases=[_list_item(c) for c in result.scalars().all()], total=total)


# --------------------------------------------------------------------------- #
# Finding review — a human verdict on one displayed analysis finding
# --------------------------------------------------------------------------- #


class FindingReviewRequest(BaseModel):
    # Identifies the exact analysis the reviewer saw; a stale value is rejected.
    analysis_revision: str
    decision: ReviewDecision
    # The reviewer's re-diagnosis (any of the nine). Optional: an ``incorrect``
    # verdict without one is an explicit dismiss (the legacy shape), a correction
    # on it re-drives the finding's inbox visibility.
    corrected_diagnosis: CorrectedDiagnosis | None = None
    note: str = Field(default="", max_length=2000)


class ReviewValue(BaseModel):
    decision: ReviewDecision
    corrected_diagnosis: CorrectedDiagnosis | None = None
    note: str
    reviewed_by: str
    reviewed_at: str


class FindingReviewResponse(BaseModel):
    analysis_revision: str
    review: ReviewValue


async def _lock_case_for_human_write(
    db: AsyncSession, *, case_id: int, kb: PortalKnowledgeBase, perms: UserPermissions
) -> PortalSupportCase:
    """Lock the org policy FOR SHARE then the case FOR UPDATE for a human write.

    Shared by the finding review and the case reference: the same lock order as
    the evidence-mutation contract, so a telemetry downgrade or feature revoke
    cannot race the write, and two writers of one case serialise. 404 for a
    personal/cross-tenant/missing case; 403 on the re-checked policy.
    """
    if is_personal_kb(kb):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Support case not found")
    policy = (
        await db.execute(
            select(PortalOrg.telemetry_level, PortalOrg.platform_unlocked_features)
            .where(PortalOrg.id == perms.org_id)
            .with_for_update(read=True)
        )
    ).one_or_none()
    if policy is None or policy.telemetry_level != "full":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "telemetry_level_forbids_support_evidence"},
        )
    assert_platform_unlocked(policy, GAP_FEATURE)  # type: ignore[arg-type]
    case = (
        await db.execute(
            select(PortalSupportCase)
            .where(
                PortalSupportCase.id == case_id,
                PortalSupportCase.org_id == perms.org_id,
                PortalSupportCase.kb_slug == kb.slug,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Support case not found")
    return case


@router.patch("/{case_id}/findings/{finding_index}/review", response_model=FindingReviewResponse)
async def review_finding(
    case_id: int,
    finding_index: int,
    body: FindingReviewRequest,
    kb: PortalKnowledgeBase = Depends(get_kb_with_access),
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> FindingReviewResponse:
    """Record a reviewer's verdict on one analysis finding (SPEC-RAG-SUPPORT-GAP).

    The verdict is stored apart from the machine analysis in
    ``PortalSupportCase.reviews``, keyed by ``analysis_revision:finding_index``,
    so it stays bound to the exact analysis it judged and a later reanalysis never
    silently reattributes it. No analysis, payload or gap-row is mutated.

    Lock order matches the evidence-mutation contract: the tenant is already set
    by the caller dependency; the org policy row is locked ``FOR SHARE`` (so a
    telemetry downgrade cannot race the write) before the case row is locked
    ``FOR UPDATE`` (so two reviewers of the same case serialise). Reviewer identity
    and timestamp are server-derived; the client cannot supply them.

    409 when the case is not analysed or the submitted revision is stale; 404 for
    a missing / cross-tenant case, a non-org KB, or an out-of-range index; 422 for
    invalid input (handled by the request model). Telemetry re-check → 403.
    """
    case = await _lock_case_for_human_write(db, case_id=case_id, kb=kb, perms=perms)

    if case.status != "analyzed" or case.analysis is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "support_case_not_analyzed"},
        )
    revision = compute_analysis_revision(
        content_hash=case.content_hash, analysis_version=case.analysis_version, analysis=case.analysis
    )
    if body.analysis_revision != revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "analysis_revision_stale", "analysis_revision": revision},
        )
    if not 0 <= finding_index < len(case.analysis):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Finding not found")

    review = ReviewValue(
        decision=body.decision,
        corrected_diagnosis=body.corrected_diagnosis,
        note=body.note,
        reviewed_by=perms.user_id,
        reviewed_at=datetime.now(tz=UTC).isoformat(),
    )
    # The human override drives the finding's inbox visibility (dismiss / restore /
    # promote / re-bucket) without touching the machine analysis. The reviewer's
    # portal id is stamped on any row it closes, for the actor/time audit.
    reviewer_id = (
        await db.execute(
            select(PortalUser.id).where(PortalUser.zitadel_user_id == perms.user_id, PortalUser.org_id == perms.org_id)
        )
    ).scalar_one_or_none()
    await apply_review_visibility(
        db,
        case=case,
        finding=case.analysis[finding_index],
        decision=body.decision,
        corrected_diagnosis=body.corrected_diagnosis,
        reviewer_user_id=reviewer_id,
    )
    # Copy-on-write so SQLAlchemy sees a new JSONB value and older-revision
    # entries are carried forward untouched (retention/audit).
    case.reviews = {**(case.reviews or {}), review_key(revision, finding_index): review.model_dump()}
    await db.commit()

    return FindingReviewResponse(analysis_revision=revision, review=review)


# --------------------------------------------------------------------------- #
# Role correction + reanalyse — reviewer fixes speaker roles, shared analysis reruns
# --------------------------------------------------------------------------- #


async def _force_reanalyse(
    db: AsyncSession,
    *,
    org: PortalOrg,
    case: PortalSupportCase,
    payload: SupportCasePayload,
    role_overrides: dict[str, MessageRole] | None = None,
    reviewer: str | None = None,
) -> UpsertResult:
    """Re-run the shared analysis for a stored case via the evidence store.

    Identity attribution (owning connector, creator) is carried from the case, so
    a reviewer's action never re-owns it; ``expected_content_hash`` refuses to
    overwrite a payload that moved since it was read (409). ``role_overrides`` is
    the trusted, server-owned speaker-role correction (the reviewer's, never the
    connector's) that the store persists on ``reviews`` and re-applies on later
    imports. The store takes its own lock + run-token, so no lock is held here
    across the model/network call.
    """
    try:
        return await upsert_support_case(
            db,
            org_id=org.id,
            zitadel_org_id=org.zitadel_org_id,
            telemetry_level=org.telemetry_level,
            connector_id=case.connector_id,
            created_by=case.created_by,
            kb_slug=case.kb_slug,
            payload=payload,
            force_reanalysis=True,
            expected_content_hash=case.content_hash,
            role_overrides=dict(role_overrides) if role_overrides else None,
            role_override_reviewer=reviewer,
        )
    except StaleCaseError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail={"error_code": "support_case_evidence_changed"}
        ) from exc
    except SupportTelemetryError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "telemetry_level_forbids_support_evidence"},
        ) from exc
    except OversizedCaseError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail={"error_code": "support_case_too_large"}
        ) from exc


def _reanalysis_response(result: UpsertResult) -> TranscriptImportResponse:
    """Import response for a reanalyse, surfacing a failed run as a retryable 503.

    A forced reanalysis that failed kept the evidence (and any preserved prior
    analysis), so this is not a lost write — the caller retries. Fail loudly rather
    than report a false success.
    """
    if result.reanalysis_failed or result.status == "failed":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"error_code": "support_case_analysis_unavailable"}
        )
    return TranscriptImportResponse(
        case_id=result.case_id, status=result.status, changed=result.changed, findings_count=result.findings_count
    )


def _assert_call_messages(case: PortalSupportCase, message_roles: dict[str, MessageRole]) -> None:
    """Reject (422) any id that is not one of this case's call-medium messages.

    Role correction only ever relabels call segments; the corrected roles themselves
    are applied and persisted server-side by the evidence store, so nothing here
    mutates the payload — this only guards the ids the reviewer submitted.
    """
    by_id = {m.get("id"): m for m in (case.payload.get("messages") or [])}
    for msg_id in message_roles:
        message = by_id.get(msg_id)
        if message is None or (message.get("medium") != "call" and message.get("kind") != "transcript"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"error_code": "not_a_call_message", "message_id": msg_id},
            )


class RoleCorrectionRequest(BaseModel):
    # Identifies the exact analysis the reviewer saw; a stale value is rejected.
    analysis_revision: str
    # message_id -> corrected business role. Only call-medium messages may be set;
    # an invalid role value is rejected by the enum. Must not be empty.
    message_roles: dict[str, MessageRole] = Field(min_length=1)


async def _load_case_for_reanalysis(
    db: AsyncSession, *, case_id: int, kb: PortalKnowledgeBase, perms: UserPermissions, analysis_revision: str
) -> tuple[PortalOrg, PortalSupportCase]:
    """Gate + load a case whose displayed analysis the caller is acting on.

    Shared by role-correction and manual reanalyse: both require an org-owned KB,
    full telemetry, complete evidence, and the exact current analysis revision — a
    stale revision (409) means the analysis moved under the caller. A failed or
    pending case IS retryable/role-correctable (the store supports the retry), so
    only incomplete evidence is rejected here; the revision is computed even for a
    null analysis (it hashes as an empty list) so the caller always has a handle to
    act on. The store re-checks policy authoritatively under its lock before writing.
    """
    if is_personal_kb(kb):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Support case not found")
    org = await db.get(PortalOrg, perms.org_id)
    if org is None or org.telemetry_level != "full":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "telemetry_level_forbids_support_evidence"},
        )
    case = (
        await db.execute(
            select(PortalSupportCase).where(
                PortalSupportCase.id == case_id,
                PortalSupportCase.org_id == perms.org_id,
                PortalSupportCase.kb_slug == kb.slug,
            )
        )
    ).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Support case not found")
    if case.payload.get("complete") is False:
        # Incomplete evidence is not a knowledge gap yet — it cannot be analysed,
        # so a reanalyse/role correction against it is refused rather than run.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"error_code": "support_case_incomplete"})
    revision = compute_analysis_revision(
        content_hash=case.content_hash, analysis_version=case.analysis_version, analysis=case.analysis
    )
    if analysis_revision != revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "analysis_revision_stale", "analysis_revision": revision},
        )
    return org, case


@router.patch("/{case_id}/roles", response_model=TranscriptImportResponse)
async def correct_message_roles(
    case_id: int,
    body: RoleCorrectionRequest,
    kb: PortalKnowledgeBase = Depends(get_kb_with_access),
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> TranscriptImportResponse:
    """Apply speaker-role corrections to a call case, then rerun analysis (SPEC-
    RAG-SUPPORT-GAP contract 1).

    Only roles change. The correction is persisted server-side on the case's
    ``reviews`` (with the provider's original role, reviewer and time) and overlaid
    onto the evidence by the store, which moves the content hash so the stale
    analysis reruns under the store's lock and run-token. Because the override lives
    in server-owned state, a later connector re-import carrying the original roles
    cannot erase it. A fresher connector payload is never silently overwritten
    (409). 409 when the evidence is incomplete or the revision is stale; 404 for a
    missing/personal/cross-tenant case; 422 for non-call ids.
    """
    org, case = await _load_case_for_reanalysis(
        db, case_id=case_id, kb=kb, perms=perms, analysis_revision=body.analysis_revision
    )
    _assert_call_messages(case, body.message_roles)
    payload = SupportCasePayload.model_validate(case.payload)
    payload.bind_kb(kb.slug)
    return _reanalysis_response(
        await _force_reanalyse(
            db, org=org, case=case, payload=payload, role_overrides=body.message_roles, reviewer=perms.user_id
        )
    )


class ReanalyzeRequest(BaseModel):
    # Identifies the exact analysis the caller is asking to supersede; stale -> 409.
    analysis_revision: str


@router.post("/{case_id}/reanalyze", response_model=TranscriptImportResponse)
async def reanalyze_support_case(
    case_id: int,
    body: ReanalyzeRequest,
    kb: PortalKnowledgeBase = Depends(get_kb_with_access),
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> TranscriptImportResponse:
    """Force a fresh analysis of unchanged evidence (SPEC-RAG-SUPPORT-GAP
    contract 2).

    The evidence never moved but the KB behind it may have, so the same payload is
    re-analysed even at the same analyzer version. The store keeps the prior
    analysis until the new one succeeds and uses a run token so a slower same-hash
    run cannot overwrite this one; a failed run is a retryable 503 with the old
    analysis intact. 409 when not analysed or the revision is stale; 404 for a
    missing/personal/cross-tenant case.
    """
    org, case = await _load_case_for_reanalysis(
        db, case_id=case_id, kb=kb, perms=perms, analysis_revision=body.analysis_revision
    )
    payload = SupportCasePayload.model_validate(case.payload)
    payload.bind_kb(kb.slug)
    return _reanalysis_response(await _force_reanalyse(db, org=org, case=case, payload=payload))


# --------------------------------------------------------------------------- #
# Case reference — a human's gold answer set for the WHOLE case (not model output)
# --------------------------------------------------------------------------- #


class ReferenceQuestion(BaseModel):
    question: str = Field(min_length=1)
    # The human's own diagnosis (any of the nine) — this is a person's judgement,
    # never a model label promoted to gold.
    diagnosis: CorrectedDiagnosis
    # Evidence message ids this question is grounded in; validated against the case.
    message_ids: list[str] = Field(default_factory=list)


class CaseReferenceRequest(BaseModel):
    # The evidence version the reviewer built this reference against; stale -> 409.
    content_hash: str
    questions: list[ReferenceQuestion]
    # True = the reviewer covered the whole case; an empty question set is then
    # valid (nothing reusable to capture).
    complete: bool


class CaseReferenceResponse(BaseModel):
    content_hash: str
    questions: list[ReferenceQuestion]
    complete: bool
    reviewed_by: str
    reviewed_at: str


@router.put("/{case_id}/reference", response_model=CaseReferenceResponse)
async def put_case_reference(
    case_id: int,
    body: CaseReferenceRequest,
    kb: PortalKnowledgeBase = Depends(get_kb_with_access),
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> CaseReferenceResponse:
    """Store a human reference answer set for a whole case (SPEC-RAG-SUPPORT-GAP
    contract 4).

    Independent of the machine analysis: it covers the entire case including
    questions the analyzer missed, and nothing here is model output — the diagnoses
    are the reviewer's own. Kept under ``reviews['_reference']`` bound to the
    evidence it was written against (``content_hash``); a stale hash is 409 and a
    later evidence change makes the detail view stop surfacing it. 422 for a blank
    question or an evidence id that is not in the case; 404 for a
    missing/personal/cross-tenant case; 403 on the re-checked policy.
    """
    case = await _lock_case_for_human_write(db, case_id=case_id, kb=kb, perms=perms)
    if body.content_hash != case.content_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "content_hash_stale", "content_hash": case.content_hash},
        )
    valid_ids = {m.get("id") for m in (case.payload.get("messages") or [])}
    for q in body.questions:
        if not q.question.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"error_code": "blank_reference_question"}
            )
        unknown = [mid for mid in q.message_ids if mid not in valid_ids]
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"error_code": "unknown_evidence_id", "message_ids": unknown},
            )

    reference = CaseReferenceResponse(
        content_hash=case.content_hash,
        questions=body.questions,
        complete=body.complete,
        reviewed_by=perms.user_id,
        reviewed_at=datetime.now(tz=UTC).isoformat(),
    )
    # Copy-on-write, next to the per-finding reviews; older-revision entries stay.
    case.reviews = {**(case.reviews or {}), REFERENCE_KEY: reference.model_dump()}
    await db.commit()
    return reference
