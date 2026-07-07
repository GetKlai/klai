"""Platform-admin usage analytics endpoints — SPEC-PLATFORM-STATS-001."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from app.core.database import cross_org_session
from app.core.litellm_analytics import execute_litellm_analytics, litellm_analytics_configured
from app.core.permissions import UserPermissions, require_platform_admin
from app.services.audit import log_event

logger = structlog.get_logger()

router = APIRouter(prefix="/platform/usage", tags=["platform-admin"])

UsageRange = Literal["7d", "30d", "90d"]
_RANGE_DAYS: dict[UsageRange, int] = {"7d": 7, "30d": 30, "90d": 90}
_LITELLM_TIMEOUT_S = 5.0


class PlatformUsageOverview(BaseModel):
    range: UsageRange
    start: datetime
    end: datetime
    litellm_available: bool
    litellm_configured: bool
    total_events: int
    knowledge_queries: int
    knowledge_uploads: int
    meetings_started: int
    problem_reports: int
    active_users: int
    active_tenants: int
    api_requests: int | None
    successful_requests: int | None
    failed_requests: int | None
    total_tokens: int | None
    spend_usd: float | None


class PlatformUsageTenantRow(BaseModel):
    org_id: int
    name: str
    slug: str
    plan: str
    billing_status: str
    litellm_team_id: str | None
    knowledge_queries: int
    active_users: int
    total_events: int
    last_activity_at: datetime | None
    api_requests: int | None
    successful_requests: int | None
    failed_requests: int | None
    total_tokens: int | None
    spend_usd: float | None


class DailyUsagePoint(BaseModel):
    date: date
    events: int
    knowledge_queries: int
    api_requests: int | None
    failed_requests: int | None
    tokens: int | None
    spend_usd: float | None


class EventTypeCount(BaseModel):
    event_type: str
    count: int


class ModelUsageRow(BaseModel):
    model: str
    api_requests: int
    successful_requests: int
    failed_requests: int
    tokens: int
    spend_usd: float


class PlatformUsageTenantDetail(BaseModel):
    org_id: int
    name: str
    slug: str
    range: UsageRange
    start: datetime
    end: datetime
    litellm_configured: bool
    litellm_mapped: bool
    litellm_available: bool
    active_users: int
    last_activity_at: datetime | None
    daily: list[DailyUsagePoint]
    event_type_breakdown: list[EventTypeCount]
    model_breakdown: list[ModelUsageRow] | None


async def _audit_usage(perms: UserPermissions, *, org_id: int | None, range_name: UsageRange) -> None:
    details: dict[str, Any] = {"tab": "usage", "range": range_name}
    if org_id is not None:
        details["org_id"] = org_id
    await log_event(
        org_id=perms.org_id,
        actor=perms.user_id,
        action="platform_admin.viewed",
        resource_type="platform_console",
        resource_id="usage",
        details=details,
    )


def _window(range_name: UsageRange) -> tuple[datetime, datetime]:
    days = _RANGE_DAYS[range_name]
    today = datetime.now(UTC).date()
    start_day = today - timedelta(days=days - 1)
    start = datetime.combine(start_day, time.min, tzinfo=UTC)
    end = datetime.combine(today + timedelta(days=1), time.min, tzinfo=UTC)
    return start, end


def _to_int(value: Any) -> int:
    return int(value or 0)


def _to_float(value: Any) -> float:
    return float(value or 0.0)


async def _portal_overview(start: datetime, end: datetime) -> dict[str, int]:
    async with cross_org_session() as db:
        row = (
            await db.execute(
                text(
                    """
                    SELECT
                      COUNT(*) AS total_events,
                      COUNT(*) FILTER (WHERE event_type = 'knowledge.queried') AS knowledge_queries,
                      COUNT(*) FILTER (WHERE event_type = 'knowledge.uploaded') AS knowledge_uploads,
                      COUNT(*) FILTER (WHERE event_type = 'meeting.started') AS meetings_started,
                      COUNT(*) FILTER (WHERE event_type = 'klai_assistant.problem_report') AS problem_reports,
                      COUNT(DISTINCT user_id) FILTER (WHERE user_id IS NOT NULL) AS active_users,
                      COUNT(DISTINCT org_id) FILTER (WHERE org_id IS NOT NULL) AS active_tenants
                    FROM product_events
                    WHERE created_at >= :start AND created_at < :end
                    """
                ),
                {"start": start, "end": end},
            )
        ).one()
    return {
        "total_events": _to_int(row.total_events),
        "knowledge_queries": _to_int(row.knowledge_queries),
        "knowledge_uploads": _to_int(row.knowledge_uploads),
        "meetings_started": _to_int(row.meetings_started),
        "problem_reports": _to_int(row.problem_reports),
        "active_users": _to_int(row.active_users),
        "active_tenants": _to_int(row.active_tenants),
    }


async def _portal_tenants(start: datetime, end: datetime) -> list[dict[str, Any]]:
    async with cross_org_session() as db:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT
                      o.id AS org_id,
                      o.name,
                      o.slug,
                      o.plan,
                      o.billing_status,
                      o.litellm_team_id,
                      COUNT(e.id) AS total_events,
                      COUNT(e.id) FILTER (WHERE e.event_type = 'knowledge.queried') AS knowledge_queries,
                      COUNT(DISTINCT e.user_id) FILTER (WHERE e.user_id IS NOT NULL) AS active_users,
                      MAX(e.created_at) AS last_activity_at
                    FROM portal_orgs o
                    LEFT JOIN product_events e
                      ON e.org_id = o.id
                     AND e.created_at >= :start
                     AND e.created_at < :end
                    WHERE o.deleted_at IS NULL
                    GROUP BY o.id
                    ORDER BY knowledge_queries DESC, o.name ASC
                    """
                ),
                {"start": start, "end": end},
            )
        ).all()
    return [dict(r._mapping) for r in rows]


async def _portal_tenant_detail(
    org_id: int,
    start: datetime,
    end: datetime,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    async with cross_org_session() as db:
        org_row = (
            await db.execute(
                text(
                    """
                    SELECT
                      o.id AS org_id,
                      o.name,
                      o.slug,
                      o.litellm_team_id,
                      COUNT(e.id) AS total_events,
                      COUNT(DISTINCT e.user_id) FILTER (WHERE e.user_id IS NOT NULL) AS active_users,
                      MAX(e.created_at) AS last_activity_at
                    FROM portal_orgs o
                    LEFT JOIN product_events e
                      ON e.org_id = o.id
                     AND e.created_at >= :start
                     AND e.created_at < :end
                    WHERE o.id = :org_id AND o.deleted_at IS NULL
                    GROUP BY o.id
                    """
                ),
                {"org_id": org_id, "start": start, "end": end},
            )
        ).first()
        if org_row is None:
            raise HTTPException(status_code=404, detail="Organisatie niet gevonden")

        daily_rows = (
            await db.execute(
                text(
                    """
                    SELECT
                      date_trunc('day', created_at AT TIME ZONE 'UTC')::date AS day,
                      COUNT(*) AS events,
                      COUNT(*) FILTER (WHERE event_type = 'knowledge.queried') AS knowledge_queries
                    FROM product_events
                    WHERE org_id = :org_id
                      AND created_at >= :start
                      AND created_at < :end
                    GROUP BY day
                    ORDER BY day
                    """
                ),
                {"org_id": org_id, "start": start, "end": end},
            )
        ).all()
        event_rows = (
            await db.execute(
                text(
                    """
                    SELECT event_type, COUNT(*) AS count
                    FROM product_events
                    WHERE org_id = :org_id
                      AND created_at >= :start
                      AND created_at < :end
                    GROUP BY event_type
                    ORDER BY count DESC, event_type ASC
                    """
                ),
                {"org_id": org_id, "start": start, "end": end},
            )
        ).all()
    return dict(org_row._mapping), [dict(r._mapping) for r in daily_rows], [dict(r._mapping) for r in event_rows]


async def _litellm_rows(sql: str, params: dict[str, Any]) -> tuple[bool, bool, list[dict[str, Any]]]:
    configured = litellm_analytics_configured()
    if not configured:
        return False, False, []
    try:
        rows = await asyncio.wait_for(
            execute_litellm_analytics(sql, params),
            timeout=_LITELLM_TIMEOUT_S,
        )
    except Exception as exc:
        logger.exception("platform_usage_litellm_query_failed", error=str(exc))
        return True, False, []
    return True, True, [dict(r) for r in rows]


async def _litellm_team_totals(start: datetime, end: datetime) -> tuple[bool, bool, dict[str, dict[str, Any]]]:
    configured, available, rows = await _litellm_rows(
        """
        SELECT
          team_id,
          SUM(api_requests) AS api_requests,
          SUM(successful_requests) AS successful_requests,
          SUM(failed_requests) AS failed_requests,
          SUM(prompt_tokens + completion_tokens) AS total_tokens,
          SUM(spend) AS spend_usd
        FROM "LiteLLM_DailyTeamSpend"
        WHERE date::date >= :start_date AND date::date < :end_date
        GROUP BY team_id
        """,
        {"start_date": start.date(), "end_date": end.date()},
    )
    rows_by_team: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        team_id = row.get("team_id")
        key = str(team_id) if team_id else f"__unmapped_litellm_team_{index}"
        rows_by_team[key] = row
    return configured, available, rows_by_team


def _sum_litellm(rows_by_team: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "api_requests": sum(_to_int(r.get("api_requests")) for r in rows_by_team.values()),
        "successful_requests": sum(_to_int(r.get("successful_requests")) for r in rows_by_team.values()),
        "failed_requests": sum(_to_int(r.get("failed_requests")) for r in rows_by_team.values()),
        "total_tokens": sum(_to_int(r.get("total_tokens")) for r in rows_by_team.values()),
        "spend_usd": sum(_to_float(r.get("spend_usd")) for r in rows_by_team.values()),
    }


def _tenant_row(row: dict[str, Any], litellm_available: bool, litellm: dict[str, Any] | None) -> PlatformUsageTenantRow:
    has_mapping = bool(row.get("litellm_team_id"))
    show_litellm = litellm_available and has_mapping
    return PlatformUsageTenantRow(
        org_id=_to_int(row["org_id"]),
        name=str(row["name"]),
        slug=str(row["slug"]),
        plan=str(row["plan"]),
        billing_status=str(row["billing_status"]),
        litellm_team_id=row.get("litellm_team_id"),
        knowledge_queries=_to_int(row.get("knowledge_queries")),
        active_users=_to_int(row.get("active_users")),
        total_events=_to_int(row.get("total_events")),
        last_activity_at=row.get("last_activity_at"),
        api_requests=_to_int(litellm.get("api_requests"))
        if show_litellm and litellm
        else (0 if show_litellm else None),
        successful_requests=_to_int(litellm.get("successful_requests"))
        if show_litellm and litellm
        else (0 if show_litellm else None),
        failed_requests=_to_int(litellm.get("failed_requests"))
        if show_litellm and litellm
        else (0 if show_litellm else None),
        total_tokens=_to_int(litellm.get("total_tokens"))
        if show_litellm and litellm
        else (0 if show_litellm else None),
        spend_usd=_to_float(litellm.get("spend_usd")) if show_litellm and litellm else (0.0 if show_litellm else None),
    )


async def _litellm_daily(team_id: str, start: datetime, end: datetime) -> tuple[bool, bool, dict[date, dict[str, Any]]]:
    configured, available, rows = await _litellm_rows(
        """
        SELECT
          date::date AS day,
          SUM(api_requests) AS api_requests,
          SUM(failed_requests) AS failed_requests,
          SUM(prompt_tokens + completion_tokens) AS tokens,
          SUM(spend) AS spend_usd
        FROM "LiteLLM_DailyTeamSpend"
        WHERE team_id = :team_id
          AND date::date >= :start_date
          AND date::date < :end_date
        GROUP BY day
        ORDER BY day
        """,
        {"team_id": team_id, "start_date": start.date(), "end_date": end.date()},
    )
    return configured, available, {r["day"]: r for r in rows}


async def _litellm_models(team_id: str, start: datetime, end: datetime) -> tuple[bool, bool, list[ModelUsageRow]]:
    configured, available, rows = await _litellm_rows(
        """
        SELECT
          COALESCE(model, 'unknown') AS model,
          SUM(api_requests) AS api_requests,
          SUM(successful_requests) AS successful_requests,
          SUM(failed_requests) AS failed_requests,
          SUM(prompt_tokens + completion_tokens) AS tokens,
          SUM(spend) AS spend_usd
        FROM "LiteLLM_DailyTeamSpend"
        WHERE team_id = :team_id
          AND date::date >= :start_date
          AND date::date < :end_date
        GROUP BY model
        ORDER BY spend_usd DESC NULLS LAST, api_requests DESC
        """,
        {"team_id": team_id, "start_date": start.date(), "end_date": end.date()},
    )
    return (
        configured,
        available,
        [
            ModelUsageRow(
                model=str(r["model"]),
                api_requests=_to_int(r.get("api_requests")),
                successful_requests=_to_int(r.get("successful_requests")),
                failed_requests=_to_int(r.get("failed_requests")),
                tokens=_to_int(r.get("tokens")),
                spend_usd=_to_float(r.get("spend_usd")),
            )
            for r in rows
        ],
    )


def _daily_points(
    start: datetime,
    end: datetime,
    product_rows: list[dict[str, Any]],
    litellm_rows: dict[date, dict[str, Any]],
    *,
    include_litellm: bool,
) -> list[DailyUsagePoint]:
    product_by_day = {r["day"]: r for r in product_rows}
    points: list[DailyUsagePoint] = []
    day = start.date()
    last_day = end.date() - timedelta(days=1)
    while day <= last_day:
        product = product_by_day.get(day, {})
        litellm = litellm_rows.get(day, {})
        points.append(
            DailyUsagePoint(
                date=day,
                events=_to_int(product.get("events")),
                knowledge_queries=_to_int(product.get("knowledge_queries")),
                api_requests=_to_int(litellm.get("api_requests")) if include_litellm else None,
                failed_requests=_to_int(litellm.get("failed_requests")) if include_litellm else None,
                tokens=_to_int(litellm.get("tokens")) if include_litellm else None,
                spend_usd=_to_float(litellm.get("spend_usd")) if include_litellm else None,
            )
        )
        day += timedelta(days=1)
    return points


@router.get("/overview", response_model=PlatformUsageOverview)
async def platform_usage_overview(
    range: UsageRange = Query(default="30d"),
    perms: UserPermissions = Depends(require_platform_admin()),
) -> PlatformUsageOverview:
    await _audit_usage(perms, org_id=None, range_name=range)
    start, end = _window(range)
    portal_task = _portal_overview(start, end)
    litellm_task = _litellm_team_totals(start, end)
    portal, (litellm_configured, litellm_available, litellm_by_team) = await asyncio.gather(portal_task, litellm_task)
    litellm = _sum_litellm(litellm_by_team) if litellm_available else None
    return PlatformUsageOverview(
        range=range,
        start=start,
        end=end,
        litellm_available=litellm_available,
        litellm_configured=litellm_configured,
        **portal,
        api_requests=_to_int(litellm["api_requests"]) if litellm else None,
        successful_requests=_to_int(litellm["successful_requests"]) if litellm else None,
        failed_requests=_to_int(litellm["failed_requests"]) if litellm else None,
        total_tokens=_to_int(litellm["total_tokens"]) if litellm else None,
        spend_usd=_to_float(litellm["spend_usd"]) if litellm else None,
    )


@router.get("/tenants", response_model=list[PlatformUsageTenantRow])
async def platform_usage_tenants(
    range: UsageRange = Query(default="30d"),
    perms: UserPermissions = Depends(require_platform_admin()),
) -> list[PlatformUsageTenantRow]:
    await _audit_usage(perms, org_id=None, range_name=range)
    start, end = _window(range)
    portal_task = _portal_tenants(start, end)
    litellm_task = _litellm_team_totals(start, end)
    portal_rows, (_, litellm_available, litellm_by_team) = await asyncio.gather(portal_task, litellm_task)
    mapped_team_ids = {str(r["litellm_team_id"]) for r in portal_rows if r.get("litellm_team_id")}
    unmapped_count = len(set(litellm_by_team) - mapped_team_ids)
    if unmapped_count:
        logger.debug("platform_usage_unmapped_litellm_teams", count=unmapped_count)
    return [
        _tenant_row(row, litellm_available, litellm_by_team.get(str(row.get("litellm_team_id")))) for row in portal_rows
    ]


@router.get("/tenants/{org_id}", response_model=PlatformUsageTenantDetail)
async def platform_usage_tenant_detail(
    org_id: int,
    range: UsageRange = Query(default="30d"),
    perms: UserPermissions = Depends(require_platform_admin()),
) -> PlatformUsageTenantDetail:
    await _audit_usage(perms, org_id=org_id, range_name=range)
    start, end = _window(range)
    org, product_daily, event_breakdown = await _portal_tenant_detail(org_id, start, end)
    team_id = org.get("litellm_team_id")
    litellm_configured = litellm_analytics_configured()
    litellm_mapped = bool(team_id)
    litellm_available = False
    litellm_daily: dict[date, dict[str, Any]] = {}
    model_breakdown: list[ModelUsageRow] | None = None
    include_litellm = False
    if team_id:
        daily_result, model_result = await asyncio.gather(
            _litellm_daily(str(team_id), start, end),
            _litellm_models(str(team_id), start, end),
        )
        daily_configured, daily_available, litellm_daily = daily_result
        model_configured, model_available, models = model_result
        litellm_configured = daily_configured or model_configured
        litellm_available = daily_available and model_available
        include_litellm = litellm_available
        model_breakdown = models if litellm_available else None

    return PlatformUsageTenantDetail(
        org_id=_to_int(org["org_id"]),
        name=str(org["name"]),
        slug=str(org["slug"]),
        range=range,
        start=start,
        end=end,
        litellm_configured=litellm_configured,
        litellm_mapped=litellm_mapped,
        litellm_available=litellm_available,
        active_users=_to_int(org.get("active_users")),
        last_activity_at=org.get("last_activity_at"),
        daily=_daily_points(
            start,
            end,
            product_daily,
            litellm_daily,
            include_litellm=include_litellm,
        ),
        event_type_breakdown=[
            EventTypeCount(event_type=str(r["event_type"]), count=_to_int(r.get("count"))) for r in event_breakdown
        ],
        model_breakdown=model_breakdown,
    )
