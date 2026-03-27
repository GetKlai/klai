"""App-level gap dashboard API (admin-only)."""

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import _get_caller_org, _require_admin, bearer
from app.core.database import get_db
from app.models.retrieval_gaps import PortalRetrievalGap

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/app", tags=["gaps"])


class GapOut(BaseModel):
    query_text: str
    gap_type: str
    top_score: float | None
    nearest_kb_slug: str | None
    occurrence_count: int
    last_occurred: datetime


class GapsResponse(BaseModel):
    gaps: list[GapOut]
    total: int


class GapSummaryResponse(BaseModel):
    total_7d: int
    hard_7d: int
    soft_7d: int


@router.get("/gaps", response_model=GapsResponse)
async def list_gaps(
    days: int = Query(default=30, ge=1, le=90),
    gap_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> GapsResponse:
    """List gap events for the caller's org, grouped by query text."""
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)
    cutoff = datetime.now(tz=UTC) - timedelta(days=days)

    stmt = (
        select(
            PortalRetrievalGap.query_text,
            PortalRetrievalGap.gap_type,
            func.max(PortalRetrievalGap.top_score).label("top_score"),
            func.max(PortalRetrievalGap.nearest_kb_slug).label("nearest_kb_slug"),
            func.count().label("occurrence_count"),
            func.max(PortalRetrievalGap.occurred_at).label("last_occurred"),
        )
        .where(
            PortalRetrievalGap.org_id == org.id,
            PortalRetrievalGap.occurred_at >= cutoff,
        )
        .group_by(PortalRetrievalGap.query_text, PortalRetrievalGap.gap_type)
        .order_by(func.count().desc())
        .limit(limit)
    )
    if gap_type:
        stmt = stmt.where(PortalRetrievalGap.gap_type == gap_type)

    result = await db.execute(stmt)
    rows = result.all()
    gaps = [
        GapOut(
            query_text=r.query_text,
            gap_type=r.gap_type,
            top_score=r.top_score,
            nearest_kb_slug=r.nearest_kb_slug,
            occurrence_count=r.occurrence_count,
            last_occurred=r.last_occurred,
        )
        for r in rows
    ]
    return GapsResponse(gaps=gaps, total=len(gaps))


@router.get("/gaps/summary", response_model=GapSummaryResponse)
async def get_gap_summary(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> GapSummaryResponse:
    """Return gap summary stats for the caller's org (last 7 days)."""
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)
    cutoff = datetime.now(tz=UTC) - timedelta(days=7)

    count_result = await db.execute(
        select(
            PortalRetrievalGap.gap_type,
            func.count().label("cnt"),
        )
        .where(
            PortalRetrievalGap.org_id == org.id,
            PortalRetrievalGap.occurred_at >= cutoff,
        )
        .group_by(PortalRetrievalGap.gap_type)
    )
    counts = {row.gap_type: row.cnt for row in count_result}

    return GapSummaryResponse(
        total_7d=counts.get("hard", 0) + counts.get("soft", 0),
        hard_7d=counts.get("hard", 0),
        soft_7d=counts.get("soft", 0),
    )
