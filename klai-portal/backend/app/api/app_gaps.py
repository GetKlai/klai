"""Support-case detail (the kb.gaps capability gate)."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_kb_with_access, require_capability
from app.core.database import get_db
from app.core.permissions import UserPermissions, get_caller, require_platform_unlocked
from app.core.profiles import Capability
from app.models.portal import PortalOrg
from app.models.support_cases import PortalSupportCase
from app.services.access import is_personal_kb
from app.services.support_case_reviews import REFERENCE_KEY, compute_analysis_revision, reviews_for_current_revision

router = APIRouter(
    prefix="/api/app",
    tags=["gaps"],
    # R-X2 / AC-3: all gap endpoints require the kb.gaps capability, and the
    # per-tenant knowledge_gaps unlock while the screen is unfinished.
    dependencies=[
        Depends(require_capability(Capability.KB_GAPS)),
        Depends(require_platform_unlocked("knowledge_gaps")),
    ],
)


class SupportCaseDetailOut(BaseModel):
    id: int
    kb_slug: str
    payload: dict
    status: str
    # Each finding is the raw analysis dict with a ``review`` key added: the
    # current-revision human verdict (carrying its ``corrected_diagnosis``), or
    # None. NULL analysis stays NULL.
    analysis: list | None = None
    analysis_version: str | None = None
    # Identifies the exact analysis shown, so a review PATCH can be rejected when
    # it was made against a superseded analysis. Present for every case (a null
    # analysis hashes as an empty list) so a failed/pending case stays retryable.
    analysis_revision: str | None = None
    # The evidence version, so a reference PUT can be rejected when it was written
    # against superseded evidence.
    content_hash: str
    # The human case-level reference, surfaced only while it still matches the
    # current evidence (a later change makes it stale and it drops to None).
    reference: dict | None = None
    imported_at: datetime


@router.get("/gaps/support-cases/{case_id}", response_model=SupportCaseDetailOut)
async def get_support_case(
    case_id: int,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> SupportCaseDetailOut:
    """Return one imported case's evidence and analysis (SPEC-RAG-SUPPORT-GAP).

    Access-checked: bounded to the caller's org (explicit predicate plus Cat-D
    RLS). Telemetry policy is re-checked on read — a tenant that downgraded away
    from ``full`` cannot view literal support evidence even for a case imported
    while it still could, and the purge removes those rows shortly after.
    """
    org = await db.get(PortalOrg, perms.org_id)
    if org is None or org.telemetry_level != "full":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "telemetry_level_forbids_support_evidence"},
        )
    result = await db.execute(
        select(PortalSupportCase).where(
            PortalSupportCase.id == case_id,
            PortalSupportCase.org_id == perms.org_id,
        )
    )
    case = result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Support case not found")
    # Shared contract: evidence is gated by tenant/KB access, not org+policy
    # alone. Resolve the case's comparison-scope KB through the same firewall
    # every KB route uses (existence + personal-firewall), and require it to be
    # org-owned, as import does. Raises 404 for a missing / personal / cross-org
    # KB. No new ACL — existing KB_GAPS capability + KB access apply.
    kb = await get_kb_with_access(case.kb_slug, perms, db)
    if is_personal_kb(kb):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Support case not found")

    # A revision is computed for EVERY case, even one with a null analysis (it
    # hashes as an empty list): a failed or pending case is retryable and role-
    # correctable, so the reviewer needs a revision to act on. The finding review
    # itself still requires analyzed findings, gated separately. Lay the human
    # review over each finding without persisting the enriched shape back — the
    # revision hashes the raw analysis, and only the current-revision review shows.
    revision = compute_analysis_revision(
        content_hash=case.content_hash, analysis_version=case.analysis_version, analysis=case.analysis
    )
    analysis_out: list | None = None
    if case.analysis is not None:
        reviews = reviews_for_current_revision(case.reviews, revision, len(case.analysis))
        analysis_out = [{**finding, "review": reviews[i]} for i, finding in enumerate(case.analysis)]

    # Surface the case-level human reference only while it still matches the
    # current evidence; a reference written against superseded evidence is not gold
    # for what is shown now.
    stored_reference = (case.reviews or {}).get(REFERENCE_KEY)
    reference = (
        stored_reference
        if isinstance(stored_reference, dict) and stored_reference.get("content_hash") == case.content_hash
        else None
    )

    return SupportCaseDetailOut(
        id=case.id,
        kb_slug=case.kb_slug,
        payload=case.payload,
        status=case.status,
        analysis=analysis_out,
        analysis_version=case.analysis_version,
        analysis_revision=revision,
        content_hash=case.content_hash,
        reference=reference,
        imported_at=case.imported_at,
    )
