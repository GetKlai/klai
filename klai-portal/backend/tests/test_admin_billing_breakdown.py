"""SPEC-PORTAL-PRICING-PER-USER-001 Phase 1 — admin billing breakdown.

The endpoint is a thin aggregator: ``SELECT seat_type, COUNT(id) FROM
portal_users WHERE org_id = T AND status = 'active' GROUP BY seat_type``,
multiplied by ``SEAT_PRICE_MONTHLY``. Tests cover:

  - Aggregation correctness (mixed-tier org).
  - Zero-row handling (every tier appears in stable order even if count=0).
  - Suspended / offboarded users are EXCLUDED from the count.
  - Tenant scope is enforced via ``perms.org_id`` (the WHERE clause).
  - RBAC: the endpoint depends on ``get_caller_at_least(ProfileRole.ADMIN)``.

The 403-for-non-admin branch lives in ``Depends(get_caller_at_least(ADMIN))``
and is pinned in ``tests/test_permissions.py``; we only need to verify the
endpoint is wired to that dependency.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.admin.billing import (
    SeatBreakdownResponse,
    SeatBreakdownRow,
    billing_breakdown,
)
from app.core.seats import SEAT_PRICE_MONTHLY, SeatType
from tests.conftest import make_perms


def _mock_db_returning(rows: list[tuple[str, int]]) -> AsyncMock:
    """Build an AsyncMock db that returns ``rows`` from the count query."""
    result = MagicMock()
    result.all = MagicMock(return_value=rows)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    return db


class TestBillingBreakdownAggregation:
    @pytest.mark.asyncio
    async def test_mixed_tier_breakdown_sums_correctly(self) -> None:
        # 4 chat + 2 knowledge + 1 viewer = 4*28 + 2*68 + 1*0 = 248
        db = _mock_db_returning([("chat", 4), ("knowledge", 2), ("viewer", 1)])
        perms = make_perms(role="admin", org_id=101)

        result = await billing_breakdown(perms=perms, db=db)

        assert isinstance(result, SeatBreakdownResponse)
        rows_by_type = {row.seat_type: row for row in result.rows}
        assert rows_by_type["chat"].count == 4
        assert rows_by_type["chat"].monthly_eur == 4 * SEAT_PRICE_MONTHLY[SeatType.CHAT]
        assert rows_by_type["knowledge"].count == 2
        assert rows_by_type["knowledge"].monthly_eur == 2 * SEAT_PRICE_MONTHLY[SeatType.KNOWLEDGE]
        assert rows_by_type["viewer"].count == 1
        assert rows_by_type["viewer"].monthly_eur == 0
        assert result.total_users == 7
        assert result.total_monthly_eur == 4 * 28 + 2 * 68

    @pytest.mark.asyncio
    async def test_zero_count_tiers_still_appear_in_response(self) -> None:
        # Only chat users — viewer / knowledge rows must still appear so
        # the FE renders the full ladder without conditional logic.
        db = _mock_db_returning([("chat", 3)])
        perms = make_perms(role="admin", org_id=101)

        result = await billing_breakdown(perms=perms, db=db)

        seat_types_returned = [row.seat_type for row in result.rows]
        assert seat_types_returned == ["viewer", "chat", "knowledge"]
        assert result.total_users == 3
        assert result.total_monthly_eur == 3 * 28

    @pytest.mark.asyncio
    async def test_empty_org_returns_all_zero_rows(self) -> None:
        db = _mock_db_returning([])
        perms = make_perms(role="admin", org_id=101)

        result = await billing_breakdown(perms=perms, db=db)

        assert result.total_users == 0
        assert result.total_monthly_eur == 0
        for row in result.rows:
            assert row.count == 0
            assert row.monthly_eur == 0

    @pytest.mark.asyncio
    async def test_stable_seat_ordering(self) -> None:
        # Even if the DB returns rows in a different order (e.g. asyncpg
        # streaming order), the response always lists viewer, then chat,
        # then knowledge.
        db = _mock_db_returning([("knowledge", 2), ("viewer", 1), ("chat", 4)])
        perms = make_perms(role="admin", org_id=101)

        result = await billing_breakdown(perms=perms, db=db)

        assert [row.seat_type for row in result.rows] == [
            "viewer",
            "chat",
            "knowledge",
        ]


class TestBillingBreakdownQueryShape:
    @pytest.mark.asyncio
    async def test_query_filters_by_caller_org_and_active_status(self) -> None:
        from sqlalchemy.dialects import postgresql

        db = _mock_db_returning([])
        perms = make_perms(role="admin", org_id=4242)
        await billing_breakdown(perms=perms, db=db)

        # Inspect the SELECT that was issued. The handler issues exactly
        # one db.execute() call; pull the compiled SQL out.
        assert db.execute.await_count == 1
        stmt = db.execute.await_args.args[0]
        sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        # Tenant scope is mandatory — the caller's org_id must appear in
        # the WHERE clause. If a future refactor drops the filter, this
        # test catches the cross-tenant leak before it ships.
        assert "4242" in sql, f"caller org_id not in WHERE: {sql}"
        # Status filter — billing should only count billable users.
        assert "'active'" in sql, f"status='active' filter missing from query: {sql}"
        # The aggregation grouping. (case-insensitive because the SQL
        # text varies across SA versions.)
        assert "group by" in sql.lower()


class TestBillingBreakdownRbacWiring:
    """Behavioural RBAC check on the admin-or-above gate.

    The previous shape ("introspect ``__qualname__`` or ``callable``") was
    dead-soft: ``callable(expected)`` is always ``True`` for a closure, so
    the assertion passed regardless of which dependency was wired. The
    replacement runs the dependency directly with a fake ``UserPermissions``
    and asserts the real contract: non-admin -> 403, admin -> pass-through.
    """

    def _endpoint_dependency(self):
        """Extract the closure that ``Depends(...)`` wraps on the endpoint."""
        import inspect

        signature = inspect.signature(billing_breakdown)
        perms_param = signature.parameters.get("perms")
        assert perms_param is not None, "endpoint must take a `perms` parameter"
        dep = perms_param.default
        assert dep is not None and dep.dependency is not None, "endpoint must wire a Depends(...) on `perms`"
        return dep.dependency

    def test_dependency_is_get_caller_at_least_closure(self) -> None:
        """Structural check: the dependency is the closure produced by
        ``get_caller_at_least`` AND it captured the ADMIN rank. Drop the
        gate by mistake — e.g. swap to ``Depends(get_caller)`` directly —
        and this test fails.
        """
        dep = self._endpoint_dependency()

        # The closure produced by ``get_caller_at_least`` lives at
        # ``permissions.py:272`` as a nested ``async def _dep``; Python
        # prefixes its ``__qualname__`` with ``get_caller_at_least.``.
        assert "get_caller_at_least" in dep.__qualname__, (
            f"endpoint dependency is not the get_caller_at_least closure. Got: {dep.__qualname__!r}"
        )

        # Verify the closure captured the ADMIN rank (not some other tier).
        # ``required_rank = PROFILE_RANK[min_role]`` -> the free variable
        # ``required_rank`` is the int rank of the role the factory was
        # called with.
        from app.core.profiles import PROFILE_RANK, ProfileRole

        captured = {
            name: cell.cell_contents
            for name, cell in zip(
                dep.__code__.co_freevars or (),
                dep.__closure__ or (),
                strict=False,
            )
        }
        assert captured.get("required_rank") == PROFILE_RANK[ProfileRole.ADMIN], (
            f"dependency captured rank {captured.get('required_rank')!r}, "
            f"expected ADMIN rank {PROFILE_RANK[ProfileRole.ADMIN]}"
        )

    @pytest.mark.asyncio
    async def test_dependency_403s_on_non_admin_caller(self) -> None:
        """Behavioural check: a personal-role caller MUST be refused 403.

        If a future refactor widens the gate (e.g. ``min_role`` becomes
        ``COMPANY``), this test fires.
        """
        from fastapi import HTTPException

        dep = self._endpoint_dependency()
        non_admin = make_perms(role="personal", org_id=101)

        with pytest.raises(HTTPException) as exc_info:
            await dep(perms=non_admin)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_dependency_passes_admin_caller_through(self) -> None:
        """Sanity case: an admin caller passes the gate without raising."""
        dep = self._endpoint_dependency()
        admin = make_perms(role="admin", org_id=101)

        result = await dep(perms=admin)
        assert result is admin, "dependency must return the same UserPermissions instance it received"


class TestSeatBreakdownRowValidation:
    def test_count_cannot_be_negative(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            SeatBreakdownRow(seat_type="chat", count=-1, monthly_eur=0)

    def test_seat_type_must_be_known(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            SeatBreakdownRow(seat_type="premium", count=1, monthly_eur=10)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Phase 5 (light) — per-seat billing switch stub
# ---------------------------------------------------------------------------


class TestPerSeatBillingStatus:
    """SPEC-PORTAL-PRICING-PER-USER-001 Phase 5 (light): the
    ``GET /api/admin/billing/per-seat-status`` endpoint returns the
    per-tenant feature-flag value plus an ``available`` field that the
    FE uses to render the CTA. ``available`` is hard-coded to False
    during the Phase 5 light window — Phase 5b flips it once the
    Moneybird mutation path is wired.
    """

    @pytest.mark.asyncio
    async def test_returns_enabled_false_for_default_tenant(self) -> None:
        from app.api.admin.billing import per_seat_billing_status

        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = False
        db.execute = AsyncMock(return_value=result)
        perms = make_perms(role="admin", org_id=101)

        response = await per_seat_billing_status(perms=perms, db=db)
        assert response.enabled is False
        # Phase 5 light: available=False until Phase 5b wires Moneybird.
        assert response.available is False

    @pytest.mark.asyncio
    async def test_returns_enabled_true_when_flag_set(self) -> None:
        from app.api.admin.billing import per_seat_billing_status

        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = True
        db.execute = AsyncMock(return_value=result)
        perms = make_perms(role="admin", org_id=101)

        response = await per_seat_billing_status(perms=perms, db=db)
        assert response.enabled is True
        # ``available`` stays False even when ``enabled`` is True — the
        # flag-flip happens in Phase 5b, this gate stays hard-locked
        # until that follow-up SPEC lands.
        assert response.available is False


class TestSwitchToPerSeatStub:
    """SPEC-PORTAL-PRICING-PER-USER-001 Phase 5 (light): the
    ``POST /api/admin/billing/switch-to-per-seat`` endpoint exists so the
    FE CTA hits a real URL, but the body is a 501 stub. Phase 5b
    replaces this with the actual Moneybird mutation behind explicit
    per-tenant consent (the CTA-click).
    """

    @pytest.mark.asyncio
    async def test_endpoint_returns_501_with_structured_detail(self) -> None:
        from fastapi import HTTPException

        from app.api.admin.billing import switch_to_per_seat_billing

        db = AsyncMock()
        perms = make_perms(role="admin", org_id=101)

        with pytest.raises(HTTPException) as exc_info:
            await switch_to_per_seat_billing(perms=perms, db=db)

        assert exc_info.value.status_code == 501
        detail = exc_info.value.detail
        assert isinstance(detail, dict)
        assert detail["error_code"] == "per_seat_billing_not_implemented"
        assert "spec" in detail
        # Verify the stub did NOT touch the DB — no commits, no inserts.
        db.commit.assert_not_awaited()
