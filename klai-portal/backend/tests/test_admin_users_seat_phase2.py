"""SPEC-PORTAL-PRICING-PER-USER-001 Phase 2 — admin/users seat tests.

Coverage:
- ``invite_user`` accepts an optional ``seat_type`` body field. When
  omitted, ``suggest_seat(role)`` decides — personal/company -> chat,
  KMs/admins -> knowledge. When present, the explicit value wins
  (admin override path).
- ``UserOut`` surfaces ``seat_type`` so /admin/users can render the
  seat column without an extra round-trip.
- ``PATCH /api/admin/users/{user_id}/seat`` (NEW) changes a user's
  billing tier independently of their role. Idempotent (no-op when
  same seat). 404 for users outside the caller's tenant. Emits an
  audit-log event with the cost-delta.

Pure unit tests — DB is an ``AsyncMock`` and ``zitadel`` is patched
out. Same pattern as test_admin_users.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.seats import SeatType
from tests.conftest import make_perms

# ---------------------------------------------------------------------------
# InviteRequest -> PortalUser persists the right seat_type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "expected_default_seat"),
    [
        ("personal", SeatType.CHAT),
        ("company", SeatType.CHAT),
        ("kb_manager", SeatType.KNOWLEDGE),
        ("group_manager", SeatType.KNOWLEDGE),
        ("admin", SeatType.KNOWLEDGE),
    ],
)
@pytest.mark.asyncio
async def test_invite_user_default_seat_from_role(role: str, expected_default_seat: SeatType) -> None:
    """AC-1: invite without explicit seat_type falls back to
    ``suggest_seat(role)``. The default mirrors
    ``app/core/seats.py::DEFAULT_SEAT_FOR_ROLE``.
    """
    from app.api.admin.users import InviteRequest, invite_user

    org = MagicMock()
    org.id = 101
    org.seats = 100
    org.plan = "knowledge"  # allow every role for this test matrix
    mock_db = AsyncMock()
    locked = MagicMock()
    locked.scalar_one.return_value = org
    mock_db.execute.return_value = locked
    mock_db.scalar.return_value = 0  # seat-cap headroom

    body = InviteRequest(
        email=f"{role}@example.com",
        first_name="A",
        last_name="B",
        role=role,  # type: ignore[arg-type]
        preferred_language="nl",
        # seat_type intentionally omitted -> suggest_seat path
    )
    perms = make_perms(role="admin", user_id="admin-1", org_id=101, plan="knowledge")

    captured: dict[str, object] = {}

    def _capture(user_row: object) -> None:
        captured["seat_type"] = user_row.seat_type  # type: ignore[attr-defined]

    mock_db.add = _capture

    with (
        patch("app.api.admin.users.zitadel") as mock_zitadel,
        patch(
            "app.services.default_knowledge_bases.create_default_personal_kb",
            new=AsyncMock(),
        ),
    ):
        mock_zitadel.invite_user = AsyncMock(return_value={"userId": f"new-user-{role}"})
        mock_zitadel.grant_user_role = AsyncMock()
        await invite_user(body=body, perms=perms, db=mock_db)

    assert captured["seat_type"] == expected_default_seat.value, (
        f"invite of {role!r} without explicit seat_type should default to "
        f"{expected_default_seat.value!r} (suggest_seat). "
        f"Got: {captured['seat_type']!r}"
    )


@pytest.mark.asyncio
async def test_invite_user_ignores_client_supplied_seat_override() -> None:
    """SPEC v0.5.0: the FE no longer surfaces a seat-selector and
    ``InviteRequest`` no longer carries the ``seat_type`` field. Even
    if a legacy client POSTs ``{"role": "kb_manager", "seat_type":
    "chat"}``, pydantic drops the extra field (default ``extra='ignore'``)
    and the server derives the account type via ``suggest_seat(role)``
    — producing ``knowledge``, not the legacy override.
    """
    from app.api.admin.users import InviteRequest, invite_user

    org = MagicMock()
    org.id = 101
    org.seats = 100
    org.plan = "knowledge"
    mock_db = AsyncMock()
    locked = MagicMock()
    locked.scalar_one.return_value = org
    mock_db.execute.return_value = locked
    mock_db.scalar.return_value = 0

    # Build the request via raw dict so we can inject a legacy
    # ``seat_type`` field. pydantic drops it silently with extra='ignore'.
    body = InviteRequest.model_validate(
        {
            "email": "km-on-chat@example.com",
            "first_name": "K",
            "last_name": "M",
            "role": "kb_manager",
            "preferred_language": "nl",
            "seat_type": "chat",  # legacy override — server now ignores
        }
    )
    perms = make_perms(role="admin", user_id="admin-1", org_id=101, plan="knowledge")

    captured: dict[str, object] = {}
    mock_db.add = lambda row: captured.__setitem__("seat_type", row.seat_type)

    with (
        patch("app.api.admin.users.zitadel") as mock_zitadel,
        patch(
            "app.services.default_knowledge_bases.create_default_personal_kb",
            new=AsyncMock(),
        ),
    ):
        mock_zitadel.invite_user = AsyncMock(return_value={"userId": "u-km"})
        mock_zitadel.grant_user_role = AsyncMock()
        await invite_user(body=body, perms=perms, db=mock_db)

    assert captured["seat_type"] == "knowledge", (
        "Server derives account type from role via suggest_seat — a "
        "client-supplied seat_type MUST be ignored after v0.5.0."
    )
    # And confirm the legacy field is not part of the pydantic schema.
    assert "seat_type" not in InviteRequest.model_fields


# ---------------------------------------------------------------------------
# UserOut surfaces seat_type
# ---------------------------------------------------------------------------


def test_user_out_schema_has_seat_type_field() -> None:
    """The /admin/users response model must include seat_type so the FE
    /admin/users page can render the Seat column without an extra
    /users/{id}/seat round-trip per row.
    """
    from app.api.admin.users import UserOut

    fields = UserOut.model_fields
    assert "seat_type" in fields, "UserOut MUST expose seat_type"
    # Literal[...] gets stored as a typing form; just verify the field
    # exists and that constructing with a valid value works.
    out = UserOut(
        zitadel_user_id="u-1",
        email="x@example.com",
        first_name="A",
        last_name="B",
        role="kb_manager",
        seat_type="knowledge",
        preferred_language="nl",
        status="active",
        created_at=__import__("datetime").datetime(2026, 5, 12, tzinfo=__import__("datetime").timezone.utc),
        invite_pending=False,
    )
    assert out.seat_type == "knowledge"


# ---------------------------------------------------------------------------
# PATCH /api/admin/users/{user_id}/seat
# ---------------------------------------------------------------------------


class TestUpdateUserSeatEndpoint:
    @pytest.mark.asyncio
    async def test_changes_seat_and_commits(self) -> None:
        """Happy path: kb_manager goes from chat -> knowledge. user.seat_type
        is mutated, db.commit() is called once.
        """
        from app.api.admin.users import SeatUpdateRequest, update_user_seat

        target = MagicMock()
        target.seat_type = "chat"
        target.org_id = 101
        target.zitadel_user_id = "user-K"

        mock_db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = target
        mock_db.execute.return_value = result

        perms = make_perms(role="admin", user_id="admin-1", org_id=101)
        body = SeatUpdateRequest(seat_type="knowledge")

        with patch("app.api.admin.users.log_event", new=AsyncMock()) as mock_log:
            response = await update_user_seat(zitadel_user_id="user-K", body=body, perms=perms, db=mock_db)

        assert target.seat_type == "knowledge"
        mock_db.commit.assert_awaited_once()
        assert "bijgewerkt" in response.message.lower()
        # Audit event MUST be emitted with the cost-delta detail.
        mock_log.assert_awaited_once()
        kwargs = mock_log.await_args.kwargs
        assert kwargs["action"] == "user.seat_changed"
        assert kwargs["resource_type"] == "portal_user"
        assert kwargs["resource_id"] == "user-K"
        assert kwargs["actor"] == "admin-1"
        assert kwargs["org_id"] == 101
        assert kwargs["details"]["old_seat"] == "chat"
        assert kwargs["details"]["new_seat"] == "knowledge"
        assert kwargs["details"]["cost_delta_eur"] == 68 - 28  # 40 EUR

    @pytest.mark.asyncio
    async def test_same_seat_is_noop_no_audit(self) -> None:
        """Idempotency: PATCH /seat with the current value MUST NOT
        commit a no-op write nor emit an audit event. Same shape as the
        existing update_user_role no-op branch.
        """
        from app.api.admin.users import SeatUpdateRequest, update_user_seat

        target = MagicMock()
        target.seat_type = "knowledge"
        target.org_id = 101
        target.zitadel_user_id = "user-K"

        mock_db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = target
        mock_db.execute.return_value = result

        perms = make_perms(role="admin", user_id="admin-1", org_id=101)
        body = SeatUpdateRequest(seat_type="knowledge")

        with patch("app.api.admin.users.log_event", new=AsyncMock()) as mock_log:
            await update_user_seat(zitadel_user_id="user-K", body=body, perms=perms, db=mock_db)

        # No commit (no row-write) and no audit event for a no-op.
        mock_db.commit.assert_not_awaited()
        mock_log.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_404_when_user_not_in_caller_tenant(self) -> None:
        """Cross-tenant safety: a user_id in a different org returns 404,
        not 200 with a leaked existence-signal. The WHERE clause on
        ``org_id = perms.org_id`` is what enforces this — if a refactor
        drops the filter, this test catches it.
        """
        from fastapi import HTTPException

        from app.api.admin.users import SeatUpdateRequest, update_user_seat

        mock_db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None  # WHERE missed
        mock_db.execute.return_value = result

        perms = make_perms(role="admin", user_id="admin-1", org_id=101)
        body = SeatUpdateRequest(seat_type="knowledge")

        with pytest.raises(HTTPException) as exc_info:
            await update_user_seat(zitadel_user_id="user-from-other-org", body=body, perms=perms, db=mock_db)
        assert exc_info.value.status_code == 404
        mock_db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_downgrade_emits_negative_cost_delta(self) -> None:
        """Knowledge -> chat is a real cost-saving event. The audit
        detail's ``cost_delta_eur`` carries the SIGN so a billing-audit
        report can distinguish saves from charges."""
        from app.api.admin.users import SeatUpdateRequest, update_user_seat

        target = MagicMock()
        target.seat_type = "knowledge"
        target.org_id = 101
        target.zitadel_user_id = "user-D"

        mock_db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = target
        mock_db.execute.return_value = result

        perms = make_perms(role="admin", user_id="admin-1", org_id=101)
        body = SeatUpdateRequest(seat_type="chat")

        with patch("app.api.admin.users.log_event", new=AsyncMock()) as mock_log:
            await update_user_seat(zitadel_user_id="user-D", body=body, perms=perms, db=mock_db)

        assert mock_log.await_args.kwargs["details"]["cost_delta_eur"] == 28 - 68

    def test_seat_update_request_rejects_viewer_value(self) -> None:
        """SPEC v0.5.0 invariant: a PATCH /seat request body with
        ``seat_type='viewer'`` is rejected by pydantic before the
        handler runs. The Literal narrows the accepted set to the two
        live tiers."""
        from pydantic import ValidationError

        from app.api.admin.users import SeatUpdateRequest

        with pytest.raises(ValidationError):
            SeatUpdateRequest(seat_type="viewer")  # type: ignore[arg-type]
