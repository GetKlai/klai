"""
Tests for SPEC-SEC-TENANT-001 admin user lifecycle regressions.

Coverage:
- REQ-5.1 / A-1: offboard_user must scope the membership delete to the
  caller's org so that a target user's memberships in OTHER tenants
  remain intact (regression for finding #5 — cross-tenant IDOR).

Pure unit tests — no real DB. SQL statements captured via
``mock_db.execute.call_args_list`` and compiled to a Postgres-dialect
string for structural assertions.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import ClauseElement


def _compile(stmt: ClauseElement) -> str:
    """Compile a SQLAlchemy statement into a literal Postgres SQL string."""
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


# @MX:ANCHOR REQ-5.1 — must remain coupled to offboard_user's delete shape.
# @MX:REASON: regression guard for finding #5 (cross-tenant IDOR via
# PortalGroupMembership delete keyed only on zitadel_user_id).
@pytest.mark.asyncio
async def test_offboard_user_does_not_wipe_other_org_memberships() -> None:
    """REQ-1 / REQ-5.1: offboard for org A must scope membership delete to org A.

    The pre-fix code issues
    ``delete(PortalGroupMembership).where(zitadel_user_id == zid)`` — no
    org filter, no PortalGroup join. That deletes the user's memberships
    in EVERY tenant they belong to. This test asserts the compiled SQL
    of the membership-delete statement constrains the rows to the caller's
    org via the parent ``portal_groups`` table.

    The assertion is statement-shape (not row-count) because the test is
    pure-mock; A-1's row-count assertion is the integration-test variant
    that runs against a real Postgres fixture (out of scope for this
    pure-mock suite).
    """
    from app.api.admin.users import offboard_user

    org = MagicMock()
    org.id = 101  # caller is admin of org A

    caller = MagicMock()
    caller.role = "admin"

    target_user = MagicMock()
    target_user.status = "active"
    target_user.org_id = 101
    target_user.zitadel_user_id = "user-U"
    target_user.github_username = None

    mock_db = AsyncMock()
    select_user_result = MagicMock()
    select_user_result.scalar_one_or_none.return_value = target_user
    mock_db.execute.return_value = select_user_result

    mock_credentials = MagicMock()

    with (
        patch("app.api.admin.users._get_caller_org", return_value=("admin-1", org, caller)),
        patch("app.api.admin.users.zitadel") as mock_zitadel,
        patch("app.api.admin.users.log_event", new=AsyncMock()),
        patch("app.api.admin.users.remove_github_org_member", new=AsyncMock()),
    ):
        mock_zitadel.deactivate_user = AsyncMock()
        await offboard_user(zitadel_user_id="user-U", credentials=mock_credentials, db=mock_db)

    # Locate the DELETE on portal_group_memberships among all executed statements.
    membership_delete = None
    for call in mock_db.execute.call_args_list:
        stmt = call.args[0]
        table = getattr(stmt, "table", None)
        if table is not None and getattr(table, "name", None) == "portal_group_memberships":
            membership_delete = stmt
            break

    assert membership_delete is not None, "expected a DELETE on portal_group_memberships"

    sql = _compile(membership_delete).lower()

    # REQ-1.1 / REQ-1.2: the delete must restrict to the caller's org via the
    # PortalGroup join. Pattern A (subselect on portal_groups.org_id) and
    # Pattern B (select ids first, then delete) both produce SQL containing
    # 'portal_groups' AND a literal '101' (the caller's org_id) in the WHERE.
    assert "portal_groups" in sql, (
        "membership delete is not org-scoped via PortalGroup join (REQ-1.2). "
        f"Got SQL: {sql}"
    )
    assert "101" in sql, (
        "membership delete does not bind the caller's org_id literal (REQ-1.1). "
        f"Got SQL: {sql}"
    )
