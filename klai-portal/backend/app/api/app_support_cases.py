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
from app.models.portal import PortalOrg
from app.models.support_cases import PortalSupportCase
from app.services.access import is_personal_kb
from app.services.support_case_reviews import (
    ReviewDecision,
    compute_analysis_revision,
    review_key,
    reviews_for_current_revision,
)
from app.services.support_cases import (
    GAP_FEATURE,
    OversizedCaseError,
    SupportTelemetryError,
    TranscriptError,
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
    note: str = Field(default="", max_length=2000)


class ReviewValue(BaseModel):
    decision: ReviewDecision
    note: str
    reviewed_by: str
    reviewed_at: str


class FindingReviewResponse(BaseModel):
    analysis_revision: str
    review: ReviewValue


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
    if is_personal_kb(kb):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Support case not found")

    # Org policy FOR SHARE before the case FOR UPDATE (see docstring). Both the
    # telemetry level and the knowledge_gaps unlock are read on the same locked
    # row, so a revoke racing this write cannot commit a verdict after the tenant
    # loses access. assert_platform_unlocked reads the feature off the Row.
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
        note=body.note,
        reviewed_by=perms.user_id,
        reviewed_at=datetime.now(tz=UTC).isoformat(),
    )
    # Copy-on-write so SQLAlchemy sees a new JSONB value and older-revision
    # entries are carried forward untouched (retention/audit).
    case.reviews = {**(case.reviews or {}), review_key(revision, finding_index): review.model_dump()}
    await db.commit()

    return FindingReviewResponse(analysis_revision=revision, review=review)
