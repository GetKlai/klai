"""
Tests for SPEC-SEC-TENANT-001 admin user lifecycle regressions.

Coverage:
- REQ-5.1 / A-1: offboard_user must scope the membership delete to the
  caller's org so that a target user's memberships in OTHER tenants
  remain intact (regression for finding #5 — cross-tenant IDOR).
- REQ-5.2 / A-2: invite_user must pass the Zitadel role string mapped
  from body.role, not the hardcoded "org:owner" (regression for finding
  #10 — Zitadel role grant hardcode).

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


# @MX:ANCHOR REQ-5.2 — must remain coupled to invite_user's grant_user_role call.
# @MX:REASON: regression guard for finding #10 (Zitadel role hardcoded to
# "org:owner" regardless of the admin's body.role choice).
@pytest.mark.parametrize(
    ("portal_role", "expected_zitadel_role"),
    [
        # Admin: a single grant of org:owner. This is the one Zitadel role
        # the Klai Platform project actually has configured and the only
        # downstream signal retrieval-api currently honours.
        ("admin", "org:owner"),
        # Non-admins: NO Zitadel grant. portal_users.role is the canonical
        # authority; the JWT roles claim stays empty so retrieval-api's
        # _extract_role returns None and the cross-org check fires normally.
        ("group-admin", None),
        ("member", None),
    ],
)
@pytest.mark.asyncio
async def test_invite_user_grants_portal_role_to_zitadel(
    portal_role: str,
    expected_zitadel_role: str | None,
) -> None:
    """REQ-2 / REQ-5.2 (v0.5.0 / β): invite_user respects the role mapping.

    Pre-fix (v0.1): every invite (admin / group-admin / member) called
    ``grant_user_role(role="org:owner")``. The portal stored the chosen
    portal role on PortalUser.role correctly, but every Zitadel grant was
    org:owner — a "config-dep CRITICAL" time-bomb because retrieval-api's
    `_extract_role` is one operator-edit away from treating org:owner as
    admin (finding #10).

    Post-fix (v0.5.0 / beta architecture): only `portal_role="admin"`
    produces a Zitadel grant (`org:owner`). Non-admin invites skip
    `grant_user_role` entirely — portal_users.role is the canonical
    authority, and Zitadel is reserved for identity. See
    SPEC-SEC-TENANT-001 v0.5.0 HISTORY for the rationale and
    SPEC-SEC-IDENTITY-ASSERT-001 for the eventual gamma migration that
    replaces JWT-claim admin-bypass with a portal-signed assertion.
    """
    from app.api.admin.users import InviteRequest, invite_user

    org = MagicMock()
    org.id = 101
    org.seats = 100  # plenty of headroom; do not trip seat limit
    org.plan = "free"

    caller = MagicMock()
    caller.role = "admin"

    mock_db = AsyncMock()
    locked_org_result = MagicMock()
    locked_org_result.scalar_one.return_value = org
    mock_db.execute.return_value = locked_org_result
    mock_db.scalar.return_value = 0  # active_count under seat limit

    mock_credentials = MagicMock()

    body = InviteRequest(
        email=f"{portal_role}@example.com",
        first_name="A",
        last_name="B",
        role=portal_role,  # type: ignore[arg-type]
        preferred_language="nl",
    )

    with (
        patch("app.api.admin.users._get_caller_org", return_value=("admin-1", org, caller)),
        patch("app.api.admin.users.zitadel") as mock_zitadel,
        patch("app.api.admin.users.get_plan_products", return_value=[]),
        patch(
            "app.services.default_knowledge_bases.create_default_personal_kb",
            new=AsyncMock(),
        ),
    ):
        mock_zitadel.invite_user = AsyncMock(
            return_value={"userId": f"new-user-{portal_role}"}
        )
        mock_zitadel.grant_user_role = AsyncMock()
        await invite_user(body=body, credentials=mock_credentials, db=mock_db)

    if expected_zitadel_role is None:
        # v0.5.0 invariant for non-admins: no Zitadel grant call at all.
        assert mock_zitadel.grant_user_role.await_count == 0, (
            f"REQ-2 (v0.5.0 / β): invite_user(role={portal_role!r}) MUST NOT "
            "call zitadel.grant_user_role. portal_users.role is the canonical "
            "authority for non-admin roles. The pre-v0.5.0 behaviour granted "
            "org:owner to every invite — exactly the finding #10 time-bomb."
        )
    else:
        mock_zitadel.grant_user_role.assert_awaited_once()
        await_args = mock_zitadel.grant_user_role.await_args
        assert await_args is not None  # narrowed for pyright; also asserted above
        grant_kwargs = await_args.kwargs
        assert grant_kwargs["role"] == expected_zitadel_role, (
            f"REQ-2: invite_user(role={portal_role!r}) granted Zitadel role "
            f"{grant_kwargs['role']!r}; expected {expected_zitadel_role!r}."
        )
