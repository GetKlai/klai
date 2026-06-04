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

from unittest.mock import ANY, AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import ClauseElement


def _compile(stmt: ClauseElement) -> str:
    """Compile a SQLAlchemy statement into a literal Postgres SQL string."""
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def _http_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://auth.example.test/v2/users/human")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("Zitadel error", request=request, response=response)


# @MX:ANCHOR REQ-5.1 — must remain coupled to offboard_user's delete shape.
# @MX:REASON: regression guard for finding #5 (cross-tenant IDOR via
# PortalGroupMembership delete keyed only on zitadel_user_id).
from tests.conftest import make_perms  # noqa: E402


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

    target_user = MagicMock()
    target_user.status = "active"
    target_user.org_id = 101
    target_user.zitadel_user_id = "user-U"
    target_user.github_username = None

    mock_db = AsyncMock()
    select_user_result = MagicMock()
    select_user_result.scalar_one_or_none.return_value = target_user
    mock_db.execute.return_value = select_user_result

    perms = make_perms(role="admin", user_id="admin-1", org_id=101)

    with (
        patch("app.api.admin.users.zitadel") as mock_zitadel,
        patch("app.api.admin.users.log_event", new=AsyncMock()),
        patch("app.api.admin.users.remove_github_org_member", new=AsyncMock()),
    ):
        mock_zitadel.deactivate_user = AsyncMock()
        await offboard_user(zitadel_user_id="user-U", perms=perms, db=mock_db)

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
    assert "portal_groups" in sql, f"membership delete is not org-scoped via PortalGroup join (REQ-1.2). Got SQL: {sql}"
    assert "101" in sql, f"membership delete does not bind the caller's org_id literal (REQ-1.1). Got SQL: {sql}"


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
        ("group_manager", None),
        ("kb_manager", None),
        ("company", None),
        ("personal", None),
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
    # The role→Zitadel-grant mapping does not depend on plan; pick the plan
    # that allows every role in the parametrize matrix so the role-mapping
    # assertion is the one under test, not the plan ceiling. REQ-12/REQ-13
    # plan-ceiling behaviour is covered by ``test_admin_users_plan_ceiling.py``.
    org.plan = "knowledge"

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    locked_org_result = MagicMock()
    locked_org_result.scalar_one.return_value = org
    mock_db.execute.return_value = locked_org_result
    mock_db.scalar.return_value = 0  # active_count under seat limit

    body = InviteRequest(
        email=f"{portal_role}@example.com",
        first_name="A",
        last_name="B",
        role=portal_role,  # type: ignore[arg-type]
        preferred_language="nl",
    )

    perms = make_perms(role="admin", user_id="admin-1", org_id=101, plan="knowledge")

    with (
        patch("app.api.admin.users.zitadel") as mock_zitadel,
        patch(
            "app.services.default_knowledge_bases.create_default_personal_kb",
            new=AsyncMock(),
        ),
    ):
        mock_zitadel.invite_user = AsyncMock(return_value={"userId": f"new-user-{portal_role}"})
        mock_zitadel.send_invite_code = AsyncMock()  # SPEC-PORTAL-AUTH-EMAIL-LINKS-001 REQ-2
        mock_zitadel.grant_user_role = AsyncMock()
        await invite_user(body=body, perms=perms, db=mock_db)

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


@pytest.mark.asyncio
async def test_invite_user_cleans_up_zitadel_user_when_mail_fails() -> None:
    """If invite-code delivery fails, retry must not hit a stuck Zitadel 409."""
    from fastapi import HTTPException

    from app.api.admin.users import InviteRequest, invite_user
    from app.core.config import settings

    org = MagicMock()
    org.id = 101
    org.plan = "knowledge"

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    locked_org_result = MagicMock()
    locked_org_result.scalar_one.return_value = org
    mock_db.execute.return_value = locked_org_result

    body = InviteRequest(
        email="mail-fail@example.com",
        first_name="Mail",
        last_name="Fail",
        role="company",
        preferred_language="nl",
    )
    perms = make_perms(role="admin", user_id="admin-1", org_id=101, plan="knowledge")

    with (
        patch("app.api.admin.users.zitadel") as mock_zitadel,
        patch(
            "app.services.default_knowledge_bases.create_default_personal_kb",
            new=AsyncMock(),
        ),
    ):
        mock_zitadel.invite_user = AsyncMock(return_value={"userId": "new-user-mail-fail"})
        mock_zitadel.send_invite_code = AsyncMock(side_effect=RuntimeError("mailer down"))
        mock_zitadel.remove_user = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await invite_user(body=body, perms=perms, db=mock_db)

    assert exc_info.value.status_code == 502
    mock_zitadel.remove_user.assert_awaited_once_with(
        org_id=settings.zitadel_portal_org_id,
        zitadel_user_id="new-user-mail-fail",
    )
    mock_db.add.assert_called_once()
    mock_db.rollback.assert_awaited_once()
    mock_db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_invite_user_reuses_existing_global_zitadel_user() -> None:
    """Tenant invites must link an existing global identity instead of failing on Zitadel 409."""
    from app.api.admin.users import InviteRequest, invite_user
    from app.models.portal import PortalUser

    org = MagicMock()
    org.id = 101
    org.plan = "knowledge"

    locked_org_result = MagicMock()
    locked_org_result.scalar_one.return_value = org
    membership_result = MagicMock()
    membership_result.scalar_one_or_none.return_value = None

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[locked_org_result, membership_result])

    added_users: list[PortalUser] = []

    def _record_add(row):
        if isinstance(row, PortalUser):
            added_users.append(row)

    mock_db.add = MagicMock(side_effect=_record_add)

    body = InviteRequest(
        email="existing@example.com",
        first_name="Existing",
        last_name="User",
        role="company",
        preferred_language="nl",
    )
    perms = make_perms(role="admin", user_id="admin-1", org_id=101, plan="knowledge")

    with (
        patch("app.api.admin.users.zitadel") as mock_zitadel,
        patch(
            "app.services.default_knowledge_bases.create_default_personal_kb",
            new=AsyncMock(),
        ) as create_personal_kb,
    ):
        mock_zitadel.invite_user = AsyncMock(side_effect=_http_error(409))
        mock_zitadel.find_user_id_by_email = AsyncMock(return_value="existing-user")
        mock_zitadel.send_invite_code = AsyncMock()
        mock_zitadel.remove_user = AsyncMock()
        mock_zitadel.grant_user_role = AsyncMock()

        response = await invite_user(body=body, perms=perms, db=mock_db)

    mock_zitadel.find_user_id_by_email.assert_awaited_once_with("existing@example.com")
    mock_zitadel.send_invite_code.assert_awaited_once_with(
        "existing-user",
        url_template=ANY,
    )
    mock_zitadel.remove_user.assert_not_awaited()
    assert response.user_id == "existing-user"
    assert response.message == "Gebruiker existing@example.com toegevoegd aan deze workspace."
    assert [user.zitadel_user_id for user in added_users] == ["existing-user"]
    assert [user.org_id for user in added_users] == [101]
    create_personal_kb.assert_awaited_once_with("existing-user", 101, mock_db)
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_invite_user_reused_global_user_mail_failure_does_not_delete_identity() -> None:
    """A reused global Zitadel identity must not be removed when invite-mail delivery fails."""
    from fastapi import HTTPException

    from app.api.admin.users import InviteRequest, invite_user

    org = MagicMock()
    org.id = 101
    org.plan = "knowledge"

    locked_org_result = MagicMock()
    locked_org_result.scalar_one.return_value = org
    membership_result = MagicMock()
    membership_result.scalar_one_or_none.return_value = None

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[locked_org_result, membership_result])
    mock_db.add = MagicMock()

    body = InviteRequest(
        email="existing-mail-fail@example.com",
        first_name="Existing",
        last_name="Mailfail",
        role="company",
        preferred_language="nl",
    )
    perms = make_perms(role="admin", user_id="admin-1", org_id=101, plan="knowledge")

    with (
        patch("app.api.admin.users.zitadel") as mock_zitadel,
        patch(
            "app.services.default_knowledge_bases.create_default_personal_kb",
            new=AsyncMock(),
        ),
    ):
        mock_zitadel.invite_user = AsyncMock(side_effect=_http_error(409))
        mock_zitadel.find_user_id_by_email = AsyncMock(return_value="existing-user")
        mock_zitadel.send_invite_code = AsyncMock(side_effect=RuntimeError("mailer down"))
        mock_zitadel.remove_user = AsyncMock()
        mock_zitadel.grant_user_role = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await invite_user(body=body, perms=perms, db=mock_db)

    assert exc_info.value.status_code == 502
    mock_zitadel.remove_user.assert_not_awaited()
    mock_db.rollback.assert_awaited_once()
    mock_db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_invite_user_existing_global_user_already_in_workspace_returns_409() -> None:
    """Existing identities are reused only when they are not already members of the tenant."""
    from fastapi import HTTPException

    from app.api.admin.users import InviteRequest, invite_user

    org = MagicMock()
    org.id = 101
    org.plan = "knowledge"

    locked_org_result = MagicMock()
    locked_org_result.scalar_one.return_value = org
    membership_result = MagicMock()
    membership_result.scalar_one_or_none.return_value = MagicMock()

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[locked_org_result, membership_result])
    mock_db.add = MagicMock()

    body = InviteRequest(
        email="member@example.com",
        first_name="Member",
        last_name="User",
        role="company",
        preferred_language="nl",
    )
    perms = make_perms(role="admin", user_id="admin-1", org_id=101, plan="knowledge")

    with patch("app.api.admin.users.zitadel") as mock_zitadel:
        mock_zitadel.invite_user = AsyncMock(side_effect=_http_error(409))
        mock_zitadel.find_user_id_by_email = AsyncMock(return_value="existing-user")
        mock_zitadel.send_invite_code = AsyncMock()
        mock_zitadel.remove_user = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await invite_user(body=body, perms=perms, db=mock_db)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Deze gebruiker is al lid van deze workspace."
    mock_db.add.assert_not_called()
    mock_db.commit.assert_not_awaited()
    mock_zitadel.send_invite_code.assert_not_awaited()
    mock_zitadel.remove_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_invite_user_reactivates_offboarded_workspace_member() -> None:
    """Offboarded members can be invited again with a fresh invite code."""
    from app.api.admin.users import InviteRequest, invite_user

    org = MagicMock()
    org.id = 101
    org.plan = "knowledge"

    locked_org_result = MagicMock()
    locked_org_result.scalar_one.return_value = org
    offboarded_member = MagicMock()
    offboarded_member.status = "offboarded"
    offboarded_member.role = "admin"
    membership_result = MagicMock()
    membership_result.scalar_one_or_none.return_value = offboarded_member

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[locked_org_result, membership_result])
    mock_db.add = MagicMock()

    body = InviteRequest(
        email="returning@example.com",
        first_name="Returning",
        last_name="User",
        role="company",
        preferred_language="nl",
    )
    perms = make_perms(role="admin", user_id="admin-1", org_id=101, plan="knowledge")
    call_order: list[str] = []

    async def _record_unlock(*_args, **_kwargs):
        call_order.append("unlock")

    async def _record_send_invite(*_args, **_kwargs):
        call_order.append("send_invite")

    async def _record_commit(*_args, **_kwargs):
        call_order.append("commit")

    mock_db.commit = AsyncMock(side_effect=_record_commit)

    with (
        patch("app.api.admin.users.zitadel") as mock_zitadel,
        patch(
            "app.services.default_knowledge_bases.create_default_personal_kb",
            new=AsyncMock(),
        ) as create_personal_kb,
        patch("app.api.admin.users._sync_zitadel_role_grant", new=AsyncMock()) as sync_role,
    ):
        mock_zitadel.invite_user = AsyncMock(side_effect=_http_error(409))
        mock_zitadel.find_user_id_by_email = AsyncMock(return_value="returning-user")
        mock_zitadel.grant_user_role = AsyncMock()
        mock_zitadel.unlock_user = AsyncMock(side_effect=_record_unlock)
        mock_zitadel.send_invite_code = AsyncMock(side_effect=_record_send_invite)
        mock_zitadel.deactivate_user = AsyncMock()
        mock_zitadel.remove_user = AsyncMock()

        response = await invite_user(body=body, perms=perms, db=mock_db)

    assert response.user_id == "returning-user"
    assert response.message == "Gebruiker returning@example.com opnieuw uitgenodigd."
    assert offboarded_member.status == "active"
    assert offboarded_member.role == "company"
    assert offboarded_member.seat_type == "chat"
    assert offboarded_member.preferred_language == "nl"
    assert call_order == ["unlock", "send_invite", "commit"]
    mock_db.add.assert_not_called()
    create_personal_kb.assert_awaited_once_with("returning-user", 101, mock_db)
    mock_zitadel.unlock_user.assert_awaited_once()
    mock_zitadel.send_invite_code.assert_awaited_once_with("returning-user", url_template=ANY)
    mock_zitadel.deactivate_user.assert_not_awaited()
    sync_role.assert_awaited_once_with("returning-user", old_role="admin", new_role="company")


@pytest.mark.asyncio
async def test_resend_invite_reactivates_offboarded_workspace_member() -> None:
    """The users-table resend action must also recover offboarded members."""
    from app.api.admin.users import resend_invite

    offboarded_member = MagicMock()
    offboarded_member.status = "offboarded"
    offboarded_member.email = "returning@example.com"
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = offboarded_member

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=user_result)
    call_order: list[str] = []

    async def _record_unlock(*_args, **_kwargs):
        call_order.append("unlock")

    async def _record_send_invite(*_args, **_kwargs):
        call_order.append("send_invite")

    async def _record_commit(*_args, **_kwargs):
        call_order.append("commit")

    mock_db.commit = AsyncMock(side_effect=_record_commit)

    with (
        patch("app.api.admin.users.zitadel") as mock_zitadel,
        patch(
            "app.services.default_knowledge_bases.create_default_personal_kb",
            new=AsyncMock(),
        ) as create_personal_kb,
    ):
        mock_zitadel.unlock_user = AsyncMock(side_effect=_record_unlock)
        mock_zitadel.send_invite_code = AsyncMock(side_effect=_record_send_invite)
        mock_zitadel.deactivate_user = AsyncMock()

        response = await resend_invite(
            zitadel_user_id="returning-user",
            perms=make_perms(role="admin", user_id="admin-1", org_id=101),
            db=mock_db,
        )

    assert response.message == "Invitation resent."
    assert offboarded_member.status == "active"
    assert call_order == ["unlock", "send_invite", "commit"]
    create_personal_kb.assert_awaited_once_with("returning-user", 101, mock_db)
    mock_zitadel.deactivate_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_invite_user_treats_existing_admin_grant_as_success() -> None:
    """Zitadel may report an existing org:owner grant for reused/admin identities; invite stays idempotent."""
    from app.api.admin.users import InviteRequest, invite_user

    org = MagicMock()
    org.id = 101
    org.plan = "knowledge"

    mock_db = AsyncMock()
    locked_org_result = MagicMock()
    locked_org_result.scalar_one.return_value = org
    mock_db.execute.return_value = locked_org_result
    mock_db.add = MagicMock()

    body = InviteRequest(
        email="admin@example.com",
        first_name="Admin",
        last_name="User",
        role="admin",
        preferred_language="nl",
    )
    perms = make_perms(role="admin", user_id="admin-1", org_id=101, plan="knowledge")

    with (
        patch("app.api.admin.users.zitadel") as mock_zitadel,
        patch(
            "app.services.default_knowledge_bases.create_default_personal_kb",
            new=AsyncMock(),
        ),
    ):
        mock_zitadel.invite_user = AsyncMock(return_value={"userId": "new-admin"})
        mock_zitadel.grant_user_role = AsyncMock(side_effect=_http_error(409))
        mock_zitadel.send_invite_code = AsyncMock()
        mock_zitadel.remove_user = AsyncMock()

        response = await invite_user(body=body, perms=perms, db=mock_db)

    assert response.user_id == "new-admin"
    mock_zitadel.remove_user.assert_not_awaited()
    mock_zitadel.send_invite_code.assert_awaited_once()
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_invite_user_can_add_multiple_users_to_same_tenant() -> None:
    """A tenant admin can invite multiple distinct users into one workspace."""
    from app.api.admin.users import InviteRequest, invite_user
    from app.models.portal import PortalUser

    org = MagicMock()
    org.id = 101
    org.plan = "knowledge"

    mock_db = AsyncMock()
    locked_org_result = MagicMock()
    locked_org_result.scalar_one.return_value = org
    mock_db.execute.return_value = locked_org_result

    added_users: list[PortalUser] = []

    def _record_add(row):
        if isinstance(row, PortalUser):
            added_users.append(row)

    mock_db.add = MagicMock(side_effect=_record_add)

    bodies = [
        InviteRequest(
            email="one@example.com",
            first_name="One",
            last_name="User",
            role="company",
            preferred_language="nl",
        ),
        InviteRequest(
            email="two@example.com",
            first_name="Two",
            last_name="User",
            role="kb_manager",
            preferred_language="en",
        ),
    ]
    perms = make_perms(role="admin", user_id="admin-1", org_id=101, plan="knowledge")

    with (
        patch("app.api.admin.users.zitadel") as mock_zitadel,
        patch(
            "app.services.default_knowledge_bases.create_default_personal_kb",
            new=AsyncMock(),
        ) as create_personal_kb,
    ):
        mock_zitadel.invite_user = AsyncMock(
            side_effect=[
                {"userId": "new-user-one"},
                {"userId": "new-user-two"},
            ]
        )
        mock_zitadel.send_invite_code = AsyncMock()
        mock_zitadel.grant_user_role = AsyncMock()

        responses = [await invite_user(body=body, perms=perms, db=mock_db) for body in bodies]

    assert [response.user_id for response in responses] == ["new-user-one", "new-user-two"]
    assert [user.zitadel_user_id for user in added_users] == ["new-user-one", "new-user-two"]
    assert [user.org_id for user in added_users] == [101, 101]
    assert [user.role for user in added_users] == ["company", "kb_manager"]
    assert mock_zitadel.invite_user.await_count == 2
    assert mock_zitadel.send_invite_code.await_count == 2
    assert create_personal_kb.await_count == 2
    assert mock_db.commit.await_count == 2


@pytest.mark.asyncio
async def test_remove_user_keeps_global_identity_when_other_memberships_exist() -> None:
    """Tenant admin delete must only remove the local membership for multi-org users."""
    from app.api.admin.users import remove_user
    from app.services.user_memberships import UserMembershipSummary

    target_user = MagicMock()
    target_user.zitadel_user_id = "user-U"
    target_user.org_id = 101

    mock_db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = target_user
    mock_db.execute.return_value = result
    mock_db.delete = AsyncMock()

    perms = make_perms(role="admin", user_id="admin-1", org_id=101)

    with (
        patch(
            "app.api.admin.users.get_user_membership_summary",
            new=AsyncMock(
                return_value=UserMembershipSummary(total_count=2, remaining_count=1, is_platform_admin=False)
            ),
        ),
        patch("app.api.admin.users.zitadel") as mock_zitadel,
        patch("app.api.admin.users.fire_role_change_notification") as notify,
    ):
        mock_zitadel.remove_user = AsyncMock()
        response = await remove_user(zitadel_user_id="user-U", perms=perms, db=mock_db)

    mock_zitadel.remove_user.assert_not_awaited()
    mock_db.delete.assert_awaited_once_with(target_user)
    mock_db.commit.assert_awaited_once()
    notify.assert_called_once_with("user-U")
    assert response.message == "User removed from organization."


@pytest.mark.asyncio
async def test_remove_user_deletes_global_identity_for_last_membership() -> None:
    """Global Zitadel delete is reserved for the final portal membership."""
    from app.api.admin.users import remove_user
    from app.services.user_memberships import UserMembershipSummary

    target_user = MagicMock()
    target_user.zitadel_user_id = "user-U"
    target_user.org_id = 101

    mock_db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = target_user
    mock_db.execute.return_value = result
    mock_db.delete = AsyncMock()

    perms = make_perms(role="admin", user_id="admin-1", org_id=101)

    with (
        patch(
            "app.api.admin.users.get_user_membership_summary",
            new=AsyncMock(
                return_value=UserMembershipSummary(total_count=1, remaining_count=0, is_platform_admin=False)
            ),
        ),
        patch("app.api.admin.users.zitadel") as mock_zitadel,
        patch("app.api.admin.users.fire_role_change_notification"),
    ):
        mock_zitadel.remove_user = AsyncMock()
        response = await remove_user(zitadel_user_id="user-U", perms=perms, db=mock_db)

    mock_zitadel.remove_user.assert_awaited_once()
    assert response.message == "User deleted."


# @MX:ANCHOR — must remain coupled to invite_user's commit shape.
# @MX:REASON: regression guard for the 2026-05-07 incident where the personal
# KB was created AFTER `db.commit()`. The first commit cleared the
# transaction-scoped `app.current_org_id` GUC, then `create_default_personal_kb`
# tripped the Category-D RLS policy on `portal_knowledge_bases` with 42501.
# Symptom: the Zitadel invite + portal_users INSERT succeeded, the email went
# out, but the admin saw a 500 and the user was left without a personal KB.
# Same shape as the "Post-commit db.refresh on RLS tables" pitfall in
# .claude/rules/klai/projects/portal-backend.md, but with a service-call
# instead of a `db.refresh()`.
@pytest.mark.asyncio
async def test_invite_user_creates_personal_kb_before_commit() -> None:
    """invite_user must create the personal KB inside the same transaction as
    the portal_users INSERT — i.e. BEFORE any `db.commit()`. Splitting the
    commit clears the tenant GUC and trips Category-D RLS on
    portal_knowledge_bases at the next INSERT.

    The test records the order of (commit, create_personal_kb, send_invite)
    calls and
    asserts:
    1. create_default_personal_kb is awaited at least once
    2. send_invite_code happens AFTER create_default_personal_kb
    3. db.commit happens AFTER send_invite_code (single tx)
    4. There is exactly ONE commit (not two — two commits = the regressed pattern)
    """
    from app.api.admin.users import InviteRequest, invite_user

    org = MagicMock()
    org.id = 8  # arbitrary
    org.seats = 100
    # Plan must allow ``kb_manager`` for the role-mapping branch to be the
    # one under test; REQ-12/REQ-13 plan ceiling is covered separately.
    org.plan = "knowledge"

    call_order: list[str] = []

    async def _record_commit(*_args, **_kwargs):
        call_order.append("commit")

    async def _record_create_personal_kb(*_args, **_kwargs):
        call_order.append("create_personal_kb")

    async def _record_send_invite(*_args, **_kwargs):
        call_order.append("send_invite")

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock(side_effect=_record_commit)
    locked_org_result = MagicMock()
    locked_org_result.scalar_one.return_value = org
    mock_db.execute.return_value = locked_org_result
    mock_db.scalar.return_value = 0

    body = InviteRequest(
        email="alpha@example.com",
        first_name="A",
        last_name="L",
        role="kb_manager",
        preferred_language="nl",
    )

    perms = make_perms(role="admin", user_id="admin-1", org_id=8, plan="knowledge")

    with (
        patch("app.api.admin.users.zitadel") as mock_zitadel,
        patch(
            "app.services.default_knowledge_bases.create_default_personal_kb",
            new=AsyncMock(side_effect=_record_create_personal_kb),
        ),
    ):
        mock_zitadel.invite_user = AsyncMock(return_value={"userId": "new-user-id"})
        mock_zitadel.send_invite_code = AsyncMock(
            side_effect=_record_send_invite
        )  # SPEC-PORTAL-AUTH-EMAIL-LINKS-001 REQ-2
        mock_zitadel.grant_user_role = AsyncMock()
        await invite_user(body=body, perms=perms, db=mock_db)

    assert "create_personal_kb" in call_order, (
        f"invite_user MUST call create_default_personal_kb. Observed call order: {call_order}"
    )

    commit_indices = [i for i, e in enumerate(call_order) if e == "commit"]
    kb_indices = [i for i, e in enumerate(call_order) if e == "create_personal_kb"]
    invite_indices = [i for i, e in enumerate(call_order) if e == "send_invite"]
    assert kb_indices and invite_indices and commit_indices, f"missing events; got {call_order}"
    assert kb_indices[0] < invite_indices[0], (
        "send_invite_code MUST run only after the personal KB has flushed. "
        "Otherwise a mail can go out for a user whose portal membership/KB "
        f"cannot be saved. Got call order: {call_order}"
    )
    assert kb_indices[0] < commit_indices[0], (
        "create_default_personal_kb MUST run BEFORE the commit. The 2026-05-07 "
        "regression had `db.commit()` between the portal_users INSERT and the "
        "KB creation, which cleared the tenant-scoped GUC and tripped Cat-D "
        f"RLS at 42501 on portal_knowledge_bases. Got call order: {call_order}"
    )
    assert invite_indices[0] < commit_indices[0], (
        "send_invite_code should run before the single commit in this flow so "
        f"mail failures can still roll back the portal rows. Got call order: {call_order}"
    )
    assert len(commit_indices) == 1, (
        "invite_user MUST commit exactly once after the KB is created. Two or "
        "more commits indicate the personal-KB INSERT is in a separate "
        "transaction, which loses tenant context. The pre-fix shape committed "
        f"before AND after the KB call. Got call order: {call_order}"
    )
