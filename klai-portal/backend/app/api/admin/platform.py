"""Platform-admin console endpoints — SPEC-PLATFORM-ADMIN-001.

Cross-tenant read-only overview of users, organisations, bots
(widgets/agents) and subscriptions for Klai staff. Every endpoint:

- [HARD] is gated on ``require_platform_admin()`` — caller must be an
  ADMIN inside the platform org (``settings.platform_org_slug``).
- [HARD] reads via ``cross_org_session()`` which bypasses RLS, so it
  MUST never be reachable without passing the platform-admin gate.
- [HARD] writes an audit event (``platform_admin.viewed``) on every
  read so cross-tenant access is never silent.

# @MX:ANCHOR fan_in=5 — every endpoint here is a cross-tenant RLS bypass
# @MX:REASON: Security boundary; the platform-admin gate is the only thing
#             standing between a tenant-admin and every other tenant's data.
# @MX:SPEC: SPEC-PLATFORM-ADMIN-001
"""

from __future__ import annotations

from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from app.core.database import cross_org_session
from app.core.permissions import UserPermissions, require_platform_admin
from app.services.audit import log_event

logger = structlog.get_logger()

router = APIRouter(prefix="/platform", tags=["platform-admin"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class PlatformStats(BaseModel):
    total_users: int
    new_users_this_month: int
    total_orgs: int
    active_subscriptions: int
    total_bots: int
    new_bots_today: int
    total_kbs: int
    total_documents: int
    mrr_cents: int
    arr_cents: int


class PlatformUser(BaseModel):
    zitadel_user_id: str
    email: str | None
    display_name: str | None
    role: str
    is_admin: bool
    status: str
    org_id: int
    org_name: str
    org_slug: str
    org_plan: str
    org_onboarded: bool
    created_at: datetime


class PlatformOrg(BaseModel):
    id: int
    name: str
    slug: str
    plan: str
    billing_status: str
    billing_cycle: str
    seats: int
    provisioning_status: str
    user_count: int
    bot_count: int
    kb_count: int
    document_count: int
    created_at: datetime


class PlatformBot(BaseModel):
    id: str
    name: str
    widget_id: str
    org_id: int
    org_name: str
    org_slug: str
    kb_count: int
    created_at: datetime


class PlatformChatError(BaseModel):
    id: int
    org_id: int
    org_name: str | None
    event_type: str
    detail: str | None
    created_at: datetime


class PlatformOrgDetail(BaseModel):
    org: PlatformOrg
    users: list[PlatformUser]
    bots: list[PlatformBot]


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------


async def _audit(perms: UserPermissions, tab: str, search: str | None) -> None:
    """Record a cross-tenant platform read. Never raises."""
    await log_event(
        org_id=perms.org_id,
        actor=perms.user_id,
        action="platform_admin.viewed",
        resource_type="platform_console",
        resource_id=tab,
        details={"search": search} if search else None,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=PlatformStats)
async def platform_stats(
    perms: UserPermissions = Depends(require_platform_admin()),
) -> PlatformStats:
    await _audit(perms, "stats", None)
    async with cross_org_session() as db:
        row = (
            await db.execute(
                text(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM portal_users
                         WHERE status <> 'offboarded') AS total_users,
                      (SELECT COUNT(*) FROM portal_users
                         WHERE status <> 'offboarded'
                           AND created_at >= date_trunc('month', NOW())) AS new_users_month,
                      (SELECT COUNT(*) FROM portal_orgs
                         WHERE deleted_at IS NULL) AS total_orgs,
                      (SELECT COUNT(*) FROM portal_orgs
                         WHERE deleted_at IS NULL
                           AND billing_status IN ('active','trialing')) AS active_subs,
                      (SELECT COUNT(*) FROM widgets) AS total_bots,
                      (SELECT COUNT(*) FROM widgets
                         WHERE created_at >= date_trunc('day', NOW())) AS new_bots_today,
                      (SELECT COUNT(*) FROM portal_knowledge_bases) AS total_kbs,
                      (SELECT COUNT(*) FROM knowledge.artifacts) AS total_docs
                    """
                )
            )
        ).one()

    # MRR is stubbed at 0 until a plan→price table exists (SPEC §7).
    return PlatformStats(
        total_users=row.total_users,
        new_users_this_month=row.new_users_month,
        total_orgs=row.total_orgs,
        active_subscriptions=row.active_subs,
        total_bots=row.total_bots,
        new_bots_today=row.new_bots_today,
        total_kbs=row.total_kbs,
        total_documents=row.total_docs,
        mrr_cents=0,
        arr_cents=0,
    )


@router.get("/users", response_model=list[PlatformUser])
async def platform_users(
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    perms: UserPermissions = Depends(require_platform_admin()),
) -> list[PlatformUser]:
    await _audit(perms, "users", search)
    params: dict[str, object] = {"limit": limit, "offset": offset}
    where = "WHERE u.status <> 'offboarded'"
    if search:
        where += " AND (u.email ILIKE :q OR u.display_name ILIKE :q OR o.name ILIKE :q)"
        params["q"] = f"%{search}%"

    async with cross_org_session() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT u.zitadel_user_id, u.email, u.display_name, u.role, "  # noqa: S608
                    "u.status, u.created_at, o.id AS org_id, o.name AS org_name, "
                    "o.slug AS org_slug, o.plan AS org_plan, "
                    "o.provisioning_status AS prov "
                    "FROM portal_users u "
                    "JOIN portal_orgs o ON o.id = u.org_id "
                    f"{where} "
                    "ORDER BY u.created_at DESC "
                    "LIMIT :limit OFFSET :offset"
                ),
                params,
            )
        ).all()

    return [
        PlatformUser(
            zitadel_user_id=r.zitadel_user_id,
            email=r.email,
            display_name=r.display_name,
            role=r.role,
            is_admin=r.role == "admin",
            status=r.status,
            org_id=r.org_id,
            org_name=r.org_name,
            org_slug=r.org_slug,
            org_plan=r.org_plan,
            org_onboarded=r.prov == "complete",
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/organizations", response_model=list[PlatformOrg])
async def platform_organizations(
    search: str | None = Query(default=None),
    perms: UserPermissions = Depends(require_platform_admin()),
) -> list[PlatformOrg]:
    await _audit(perms, "organizations", search)
    params: dict[str, object] = {}
    where = "WHERE o.deleted_at IS NULL"
    if search:
        where += " AND (o.name ILIKE :q OR o.slug ILIKE :q)"
        params["q"] = f"%{search}%"

    async with cross_org_session() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT o.id, o.name, o.slug, o.plan, o.billing_status, "  # noqa: S608
                    "o.billing_cycle, o.seats, o.provisioning_status, o.created_at, "
                    "o.zitadel_org_id, "
                    "(SELECT COUNT(*) FROM portal_users u "
                    "  WHERE u.org_id = o.id AND u.status <> 'offboarded') AS user_count, "
                    "(SELECT COUNT(*) FROM widgets w WHERE w.org_id = o.id) AS bot_count, "
                    "(SELECT COUNT(*) FROM portal_knowledge_bases kb "
                    "  WHERE kb.org_id = o.id) AS kb_count, "
                    "(SELECT COUNT(*) FROM knowledge.artifacts a "
                    "  WHERE a.org_id = o.zitadel_org_id) AS document_count "
                    "FROM portal_orgs o "
                    f"{where} "
                    "ORDER BY o.created_at DESC"
                ),
                params,
            )
        ).all()

    return [
        PlatformOrg(
            id=r.id,
            name=r.name,
            slug=r.slug,
            plan=r.plan,
            billing_status=r.billing_status,
            billing_cycle=r.billing_cycle,
            seats=r.seats,
            provisioning_status=r.provisioning_status,
            user_count=r.user_count,
            bot_count=r.bot_count,
            kb_count=r.kb_count,
            document_count=r.document_count,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/organizations/{org_id}", response_model=PlatformOrgDetail)
async def platform_org_detail(
    org_id: int,
    perms: UserPermissions = Depends(require_platform_admin()),
) -> PlatformOrgDetail:
    """One org with its users + bots — drill-down from the console."""
    await _audit(perms, f"organization:{org_id}", None)
    async with cross_org_session() as db:
        org_row = (
            await db.execute(
                text(
                    "SELECT o.id, o.name, o.slug, o.plan, o.billing_status, "
                    "o.billing_cycle, o.seats, o.provisioning_status, o.created_at, "
                    "(SELECT COUNT(*) FROM portal_users u "
                    "  WHERE u.org_id = o.id AND u.status <> 'offboarded') AS user_count, "
                    "(SELECT COUNT(*) FROM widgets w WHERE w.org_id = o.id) AS bot_count, "
                    "(SELECT COUNT(*) FROM portal_knowledge_bases kb "
                    "  WHERE kb.org_id = o.id) AS kb_count, "
                    "(SELECT COUNT(*) FROM knowledge.artifacts a "
                    "  WHERE a.org_id = o.zitadel_org_id) AS document_count "
                    "FROM portal_orgs o WHERE o.id = :org_id AND o.deleted_at IS NULL"
                ),
                {"org_id": org_id},
            )
        ).first()
        if org_row is None:
            raise HTTPException(status_code=404, detail="Organisatie niet gevonden")

        user_rows = (
            await db.execute(
                text(
                    "SELECT u.zitadel_user_id, u.email, u.display_name, u.role, "
                    "u.status, u.created_at FROM portal_users u "
                    "WHERE u.org_id = :org_id AND u.status <> 'offboarded' "
                    "ORDER BY u.created_at DESC"
                ),
                {"org_id": org_id},
            )
        ).all()

        bot_rows = (
            await db.execute(
                text(
                    "SELECT w.id, w.name, w.widget_id, w.created_at, "
                    "(SELECT COUNT(*) FROM widget_kb_access k "
                    "  WHERE k.widget_id = w.id) AS kb_count "
                    "FROM widgets w WHERE w.org_id = :org_id "
                    "ORDER BY w.created_at DESC"
                ),
                {"org_id": org_id},
            )
        ).all()

    org = PlatformOrg(
        id=org_row.id,
        name=org_row.name,
        slug=org_row.slug,
        plan=org_row.plan,
        billing_status=org_row.billing_status,
        billing_cycle=org_row.billing_cycle,
        seats=org_row.seats,
        provisioning_status=org_row.provisioning_status,
        user_count=org_row.user_count,
        bot_count=org_row.bot_count,
        kb_count=org_row.kb_count,
        document_count=org_row.document_count,
        created_at=org_row.created_at,
    )
    onboarded = org_row.provisioning_status == "complete"
    users = [
        PlatformUser(
            zitadel_user_id=u.zitadel_user_id,
            email=u.email,
            display_name=u.display_name,
            role=u.role,
            is_admin=u.role == "admin",
            status=u.status,
            org_id=org.id,
            org_name=org.name,
            org_slug=org.slug,
            org_plan=org.plan,
            org_onboarded=onboarded,
            created_at=u.created_at,
        )
        for u in user_rows
    ]
    bots = [
        PlatformBot(
            id=str(b.id),
            name=b.name,
            widget_id=b.widget_id,
            org_id=org.id,
            org_name=org.name,
            org_slug=org.slug,
            kb_count=b.kb_count,
            created_at=b.created_at,
        )
        for b in bot_rows
    ]
    return PlatformOrgDetail(org=org, users=users, bots=bots)


@router.get("/bots", response_model=list[PlatformBot])
async def platform_bots(
    search: str | None = Query(default=None),
    perms: UserPermissions = Depends(require_platform_admin()),
) -> list[PlatformBot]:
    await _audit(perms, "bots", search)
    params: dict[str, object] = {}
    where = ""
    if search:
        where = "WHERE (w.name ILIKE :q OR o.name ILIKE :q)"
        params["q"] = f"%{search}%"

    async with cross_org_session() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT w.id, w.name, w.widget_id, w.created_at, "  # noqa: S608
                    "o.id AS org_id, o.name AS org_name, o.slug AS org_slug, "
                    "(SELECT COUNT(*) FROM widget_kb_access k "
                    "  WHERE k.widget_id = w.id) AS kb_count "
                    "FROM widgets w "
                    "JOIN portal_orgs o ON o.id = w.org_id "
                    f"{where} "
                    "ORDER BY w.created_at DESC"
                ),
                params,
            )
        ).all()

    return [
        PlatformBot(
            id=str(r.id),
            name=r.name,
            widget_id=r.widget_id,
            org_id=r.org_id,
            org_name=r.org_name,
            org_slug=r.org_slug,
            kb_count=r.kb_count,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/chat-errors", response_model=list[PlatformChatError])
async def platform_chat_errors(
    limit: int = Query(default=50, ge=1, le=200),
    perms: UserPermissions = Depends(require_platform_admin()),
) -> list[PlatformChatError]:
    """Recent chat-error product-events across all tenants.

    v1 best-effort: reads ``product_events`` for error-flavoured event
    types. If the table or events don't exist yet, returns []. Full
    error-stream integration is a follow-up (SPEC §7).
    """
    await _audit(perms, "chat-errors", None)
    async with cross_org_session() as db:
        try:
            rows = (
                await db.execute(
                    text(
                        "SELECT e.id, e.org_id, e.event_type, "
                        "e.properties::text AS detail, e.created_at, "
                        "o.name AS org_name "
                        "FROM product_events e "
                        "LEFT JOIN portal_orgs o ON o.id = e.org_id "
                        "WHERE e.event_type LIKE '%error%' "
                        "ORDER BY e.created_at DESC "
                        "LIMIT :limit"
                    ),
                    {"limit": limit},
                )
            ).all()
        except Exception:
            logger.warning("platform_chat_errors_query_failed", exc_info=True)
            return []

    return [
        PlatformChatError(
            id=r.id,
            org_id=r.org_id,
            org_name=r.org_name,
            event_type=r.event_type,
            detail=r.detail,
            created_at=r.created_at,
        )
        for r in rows
    ]


__all__ = ["router"]
