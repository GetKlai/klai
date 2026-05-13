"""Admin billing endpoints — SPEC-PORTAL-PRICING-PER-USER-001 Phase 1.

Read-only "per-seat breakdown" view that the ``/admin/billing`` UI
panel renders. Counts active portal_users per ``seat_type`` for the
caller's org and reports the corresponding monthly cost (using the
canonical SEAT_PRICE_MONTHLY table in ``app.core.seats``).

This endpoint does NOT mutate anything and does NOT touch Moneybird —
Phase 5 introduces the actual per-seat-type subscription line-items
behind a per-tenant ``BILLING_PER_SEAT_ENABLED`` feature flag.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import ProfileRole, UserPermissions, get_caller_at_least
from app.core.seats import (
    SeatType,
    breakdown_to_monthly_bill,
    monthly_seat_cost,
)
from app.models.portal import PortalOrg, PortalUser

router = APIRouter()


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class SeatBreakdownRow(BaseModel):
    """Per-account-type aggregation row for one billing tier."""

    seat_type: Literal["chat", "knowledge"]
    count: int = Field(..., ge=0, description="Active users on this account tier")
    monthly_eur: int = Field(..., ge=0, description="count * SEAT_PRICE_MONTHLY")


class SeatBreakdownResponse(BaseModel):
    """Snapshot of the org's per-account-type billing breakdown.

    Always returns one row per ``SeatType`` member in stable order
    (chat, knowledge). Rows with zero users are still returned so the
    FE renders the full ladder without conditional logic. v0.5.0 drops
    the viewer tier — see SPEC v0.5.0 HISTORY.
    """

    rows: list[SeatBreakdownRow]
    total_users: int = Field(..., ge=0)
    total_monthly_eur: int = Field(..., ge=0)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


# Stable ordering — ascending price. Keeps the panel layout consistent
# across orgs regardless of which tiers are populated.
_SEAT_ORDER: tuple[SeatType, ...] = (
    SeatType.CHAT,
    SeatType.KNOWLEDGE,
)


@router.get("/billing/breakdown", response_model=SeatBreakdownResponse)
async def billing_breakdown(
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> SeatBreakdownResponse:
    """Return active-user counts and monthly cost per seat tier for the caller's org.

    RBAC: admin-or-above. Tenant scope: caller's org only.

    Only ``status = 'active'`` users are counted. Suspended / offboarded
    users do not bill, so they should not show up in the breakdown panel
    either. Phase 5 prorate-billing reads ``portal_user_seat_history``
    instead, where ``valid_to IS NULL`` + ``status='active'`` carries
    the same intent.
    """
    # Pre-seed with both known tiers at zero so the response always
    # contains a row per ``_SEAT_ORDER`` member, even when the org has no
    # users on a given tier. An unknown seat_type value cannot reach this
    # dict — the ``ck_portal_users_seat_type`` CHECK constraint enforces
    # the two-value domain at the DB layer (v0.5.0 dropped 'viewer').
    rows_by_seat: dict[str, int] = {seat.value: 0 for seat in _SEAT_ORDER}

    result = await db.execute(
        select(PortalUser.seat_type, func.count(PortalUser.id))
        .where(
            PortalUser.org_id == perms.org_id,
            PortalUser.status == "active",
        )
        .group_by(PortalUser.seat_type)
    )
    for seat_type_value, count in result.all():
        if seat_type_value in rows_by_seat:
            rows_by_seat[seat_type_value] = int(count)
        # else: ignored. Reaching this branch means the DB CHECK constraint
        # has been weakened OR a future migration introduced a new tier
        # without updating SEAT_PRICE_MONTHLY / _SEAT_ORDER. Either way it
        # is a code-deploy mismatch; the missing pricing data makes the
        # row uncomputable. Dropping silently is safer than surfacing a
        # row with a fabricated 0 EUR cost.

    # Compose the per-tier rows and aggregate totals. ``monthly_seat_cost``
    # and ``breakdown_to_monthly_bill`` are the canonical pricing helpers in
    # ``app.core.seats`` — keeping the cost arithmetic threaded through them
    # means Phase 5 (Moneybird prorate) can swap the monthly snapshot for a
    # daily-prorated computation in ONE place without touching this handler.
    breakdown_rows: list[SeatBreakdownRow] = [
        SeatBreakdownRow(
            seat_type=seat.value,
            count=rows_by_seat[seat.value],
            monthly_eur=monthly_seat_cost(seat) * rows_by_seat[seat.value],
        )
        for seat in _SEAT_ORDER
    ]
    total_users = sum(rows_by_seat.values())
    total_monthly_eur = breakdown_to_monthly_bill({seat: rows_by_seat[seat.value] for seat in _SEAT_ORDER})

    return SeatBreakdownResponse(
        rows=breakdown_rows,
        total_users=total_users,
        total_monthly_eur=total_monthly_eur,
    )


# ---------------------------------------------------------------------------
# Phase 5 (light) — per-seat billing switch stub
# ---------------------------------------------------------------------------


class PerSeatStatusResponse(BaseModel):
    """SPEC-PORTAL-PRICING-PER-USER-001 Phase 5 (light) — per-tenant
    opt-in status for the future Moneybird per-seat-type billing path.

    ``enabled`` reflects ``portal_orgs.billing_per_seat_enabled``. The
    FE breakdown panel uses this to render the "switch to per-user
    billing" CTA. Phase 5b (a follow-up SPEC) wires the actual
    Moneybird mutation behind this flag; today the switch endpoint
    is a 501 stub.
    """

    enabled: bool = Field(..., description="Whether per-seat-type billing is active for this org")
    available: bool = Field(
        ...,
        description=(
            "Whether the FE may surface the switch CTA at all. False during "
            "the Phase 5 light window (Moneybird mutation not yet implemented)."
        ),
    )


@router.get("/billing/per-seat-status", response_model=PerSeatStatusResponse)
async def per_seat_billing_status(
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> PerSeatStatusResponse:
    """Return the per-tenant per-seat-billing opt-in status.

    Always tenant-scoped (caller's org only). ``available`` is hard-coded
    to ``False`` during the Phase 5 light window — Phase 5b flips it to
    ``True`` once the Moneybird mutation path is wired and tested.
    """
    result = await db.execute(select(PortalOrg.billing_per_seat_enabled).where(PortalOrg.id == perms.org_id))
    enabled_val = result.scalar_one_or_none()
    enabled = bool(enabled_val) if enabled_val is not None else False
    return PerSeatStatusResponse(
        enabled=enabled,
        # SPEC-PORTAL-PRICING-PER-USER-001 Phase 5 (light): the switch
        # endpoint is a 501 stub until Phase 5b lands the Moneybird
        # mutation. Setting ``available=False`` here keeps the FE CTA
        # disabled across every tenant.
        available=False,
    )


@router.post("/billing/switch-to-per-seat", response_model=None)
async def switch_to_per_seat_billing(
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Phase 5 light stub for the per-seat-type Moneybird migration.

    Returns HTTP 501 with a structured detail explaining the staged
    rollout. Phase 5b (a follow-up SPEC) replaces this body with the
    real mutation: cancel the current single-tier subscription, create
    per-seat-type line-items (chat / knowledge), flip
    ``portal_orgs.billing_per_seat_enabled`` to true.

    The endpoint exists in Phase 5 light so the FE CTA can hit a real
    URL and surface a deterministic error rather than a 404 — admins
    see "coming soon, no billing change" instead of a broken-link page.

    ``perms`` and ``db`` are taken even though the body never reads
    them: FastAPI evaluates the ``Depends(get_caller_at_least(ADMIN))``
    chain on every request, which is exactly the auth gate we want
    the 501 to sit behind. A non-admin caller gets 403 from the
    dependency, not 501 from this stub.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "error_code": "per_seat_billing_not_implemented",
            "message": (
                "Per-user billing migration is staged. Phase 5 (light) ships "
                "the breakdown view; Phase 5b (follow-up SPEC) wires the "
                "Moneybird mutation. No billing change has been made."
            ),
            "spec": "SPEC-PORTAL-PRICING-PER-USER-001 Phase 5",
        },
    )
