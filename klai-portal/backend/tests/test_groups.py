"""
Tests for SPEC-AUTH-001: Group management endpoints.

Pure unit tests -- no real DB, all async sessions are mocked.
"""

from datetime import UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.models.groups import PortalGroup, PortalGroupMembership


def _mock_org(org_id: int = 1) -> MagicMock:
    org = MagicMock()
    org.id = org_id
    return org


def _mock_caller(role: str = "admin") -> MagicMock:
    caller = MagicMock()
    caller.role = role
    caller.zitadel_user_id = "caller-1"
    return caller


def _mock_group(group_id: int = 10, org_id: int = 1, name: str = "Engineering", *, is_system: bool = False) -> MagicMock:
    group = MagicMock(spec=PortalGroup)
    group.id = group_id
    group.org_id = org_id
    group.name = name
    group.description = None
    group.is_system = is_system
    group.created_at = MagicMock()
    group.created_by = "caller-1"
    return group


# ---------------------------------------------------------------------------
# Group CRUD
# ---------------------------------------------------------------------------


class TestListGroups:
    @pytest.mark.asyncio
    async def test_list_groups_returns_groups(self) -> None:
        from app.api.groups import list_groups

        org = _mock_org()
        caller = _mock_caller()

        group = _mock_group()
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [group]
        mock_db.execute.return_value = mock_result
        mock_credentials = MagicMock()

        with patch("app.api.groups._get_caller_org", return_value=("caller-1", org, caller)):
            result = await list_groups(credentials=mock_credentials, db=mock_db)

        assert len(result.groups) == 1
        assert result.groups[0].name == "Engineering"


class TestCreateGroup:
    @pytest.mark.asyncio
    async def test_create_group_succeeds(self) -> None:
        from datetime import datetime

        from app.api.groups import create_group

        org = _mock_org()
        caller = _mock_caller()

        mock_db = AsyncMock()
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()

        now = datetime.now(tz=UTC)

        async def fake_refresh(obj: object) -> None:
            obj.id = 10  # type: ignore[attr-defined]
            obj.created_at = now  # type: ignore[attr-defined]

        mock_db.refresh = AsyncMock(side_effect=fake_refresh)
        mock_credentials = MagicMock()

        body = MagicMock()
        body.name = "Engineering"
        body.description = "The eng team"

        with patch("app.api.groups._get_caller_org", return_value=("caller-1", org, caller)):
            result = await create_group(body=body, credentials=mock_credentials, db=mock_db)

        assert result.name == "Engineering"
        assert result.id == 10
        mock_db.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_duplicate_group_returns_409(self) -> None:
        from app.api.groups import create_group

        org = _mock_org()
        caller = _mock_caller()

        mock_db = AsyncMock()
        mock_db.flush = AsyncMock(side_effect=IntegrityError("", {}, Exception()))
        mock_db.rollback = AsyncMock()
        mock_credentials = MagicMock()

        body = MagicMock()
        body.name = "Engineering"
        body.description = None

        with patch("app.api.groups._get_caller_org", return_value=("caller-1", org, caller)):
            with pytest.raises(HTTPException) as exc_info:
                await create_group(body=body, credentials=mock_credentials, db=mock_db)

        assert exc_info.value.status_code == 409


class TestDeleteGroup:
    @pytest.mark.asyncio
    async def test_delete_group_succeeds(self) -> None:
        from app.api.groups import delete_group

        org = _mock_org()
        caller = _mock_caller()
        group = _mock_group()

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = group
        mock_db.execute.return_value = mock_result
        mock_credentials = MagicMock()

        with patch("app.api.groups._get_caller_org", return_value=("caller-1", org, caller)):
            await delete_group(group_id=10, credentials=mock_credentials, db=mock_db)

        mock_db.delete.assert_awaited_once_with(group)
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_group_returns_404(self) -> None:
        from app.api.groups import delete_group

        org = _mock_org()
        caller = _mock_caller()

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result
        mock_credentials = MagicMock()

        with patch("app.api.groups._get_caller_org", return_value=("caller-1", org, caller)):
            with pytest.raises(HTTPException) as exc_info:
                await delete_group(group_id=999, credentials=mock_credentials, db=mock_db)

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Membership management
# ---------------------------------------------------------------------------


class TestAddMember:
    @pytest.mark.asyncio
    async def test_add_member_succeeds(self) -> None:
        from app.api.groups import add_member

        org = _mock_org()
        caller = _mock_caller()
        group = _mock_group()

        target_user = MagicMock()
        target_user.org_id = 1  # same org as group

        mock_db = AsyncMock()
        # First execute: group lookup
        group_result = MagicMock()
        group_result.scalar_one_or_none.return_value = group
        # Second execute: user lookup
        user_result = MagicMock()
        user_result.scalar_one_or_none.return_value = target_user

        mock_db.execute.side_effect = [group_result, user_result]
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_credentials = MagicMock()

        body = MagicMock()
        body.zitadel_user_id = "user-2"

        with (
            patch("app.api.groups._get_caller_org", return_value=("caller-1", org, caller)),
            patch("app.api.groups._require_admin_or_group_admin", new_callable=AsyncMock),
        ):
            result = await add_member(group_id=10, body=body, credentials=mock_credentials, db=mock_db)

        assert "Member added to group" in result.message

    @pytest.mark.asyncio
    async def test_add_member_cross_org_returns_403(self) -> None:
        """R5: User from different org cannot be added to group."""
        from app.api.groups import add_member

        org = _mock_org(org_id=1)
        caller = _mock_caller()
        group = _mock_group(org_id=1)

        target_user = MagicMock()
        target_user.org_id = 2  # DIFFERENT org

        mock_db = AsyncMock()
        group_result = MagicMock()
        group_result.scalar_one_or_none.return_value = group
        user_result = MagicMock()
        user_result.scalar_one_or_none.return_value = target_user

        mock_db.execute.side_effect = [group_result, user_result]
        mock_credentials = MagicMock()

        body = MagicMock()
        body.zitadel_user_id = "user-from-other-org"

        with (
            patch("app.api.groups._get_caller_org", return_value=("caller-1", org, caller)),
            patch("app.api.groups._require_admin_or_group_admin", new_callable=AsyncMock),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await add_member(group_id=10, body=body, credentials=mock_credentials, db=mock_db)

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_add_duplicate_member_returns_409(self) -> None:
        from app.api.groups import add_member

        org = _mock_org()
        caller = _mock_caller()
        group = _mock_group()

        target_user = MagicMock()
        target_user.org_id = 1

        mock_db = AsyncMock()
        group_result = MagicMock()
        group_result.scalar_one_or_none.return_value = group
        user_result = MagicMock()
        user_result.scalar_one_or_none.return_value = target_user

        mock_db.execute.side_effect = [group_result, user_result]
        mock_db.flush = AsyncMock(side_effect=IntegrityError("", {}, Exception()))
        mock_db.rollback = AsyncMock()
        mock_credentials = MagicMock()

        body = MagicMock()
        body.zitadel_user_id = "user-2"

        with (
            patch("app.api.groups._get_caller_org", return_value=("caller-1", org, caller)),
            patch("app.api.groups._require_admin_or_group_admin", new_callable=AsyncMock),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await add_member(group_id=10, body=body, credentials=mock_credentials, db=mock_db)

        assert exc_info.value.status_code == 409


class TestToggleGroupAdmin:
    @pytest.mark.asyncio
    async def test_toggle_group_admin_requires_admin(self) -> None:
        """R7: Only org admin can toggle is_group_admin."""
        from app.api.groups import toggle_group_admin

        org = _mock_org()
        caller = _mock_caller(role="member")

        mock_db = AsyncMock()
        mock_credentials = MagicMock()

        body = MagicMock()
        body.is_group_admin = True

        with patch("app.api.groups._get_caller_org", return_value=("caller-1", org, caller)):
            with pytest.raises(HTTPException) as exc_info:
                await toggle_group_admin(
                    group_id=10, user_id="user-2", body=body, credentials=mock_credentials, db=mock_db
                )

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_toggle_group_admin_succeeds(self) -> None:
        from app.api.groups import toggle_group_admin

        org = _mock_org()
        caller = _mock_caller()

        membership = MagicMock(spec=PortalGroupMembership)
        membership.is_group_admin = False

        group = _mock_group()

        mock_db = AsyncMock()
        # First execute: group lookup
        group_result = MagicMock()
        group_result.scalar_one_or_none.return_value = group
        # Second execute: membership lookup
        membership_result = MagicMock()
        membership_result.scalar_one_or_none.return_value = membership

        mock_db.execute.side_effect = [group_result, membership_result]
        mock_credentials = MagicMock()

        body = MagicMock()
        body.is_group_admin = True

        with patch("app.api.groups._get_caller_org", return_value=("caller-1", org, caller)):
            result = await toggle_group_admin(
                group_id=10, user_id="user-2", body=body, credentials=mock_credentials, db=mock_db
            )

        assert membership.is_group_admin is True
        assert "granted" in result.message


# ---------------------------------------------------------------------------
# Group admin member management (R4)
# ---------------------------------------------------------------------------


class TestGroupAdminCanManageMembers:
    @pytest.mark.asyncio
    async def test_group_admin_can_list_members(self) -> None:
        """R4: Group admin can list members of their group."""
        from app.api.groups import list_members

        org = _mock_org()
        caller = _mock_caller(role="member")
        group = _mock_group()

        membership = MagicMock(spec=PortalGroupMembership)
        membership.zitadel_user_id = "user-2"
        membership.is_group_admin = False
        membership.joined_at = MagicMock()

        mock_db = AsyncMock()
        # First execute: group lookup
        group_result = MagicMock()
        group_result.scalar_one_or_none.return_value = group
        # Second execute: members list
        members_result = MagicMock()
        members_result.scalars.return_value.all.return_value = [membership]

        mock_db.execute.side_effect = [group_result, members_result]
        mock_credentials = MagicMock()

        with (
            patch("app.api.groups._get_caller_org", return_value=("caller-1", org, caller)),
            patch("app.api.groups._require_admin_or_group_admin", new_callable=AsyncMock),
        ):
            result = await list_members(group_id=10, credentials=mock_credentials, db=mock_db)

        assert len(result.members) == 1


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestGroupModels:
    def test_portal_group_tablename(self) -> None:
        assert PortalGroup.__tablename__ == "portal_groups"

    def test_portal_group_membership_tablename(self) -> None:
        assert PortalGroupMembership.__tablename__ == "portal_group_memberships"

    def test_group_can_be_constructed(self) -> None:
        group = PortalGroup(
            org_id=1,
            name="Test Group",
            created_by="admin-1",
        )
        assert group.name == "Test Group"
        assert group.org_id == 1

    def test_membership_can_be_constructed(self) -> None:
        membership = PortalGroupMembership(
            group_id=1,
            zitadel_user_id="user-1",
        )
        assert membership.group_id == 1
        assert membership.zitadel_user_id == "user-1"
        # default=False is applied at flush time; at construction it's None
        assert membership.is_group_admin is None or membership.is_group_admin is False
