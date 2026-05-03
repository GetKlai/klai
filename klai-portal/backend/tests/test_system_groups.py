from unittest.mock import AsyncMock, MagicMock

import pytest


class TestSystemGroupRoleMap:
    def test_role_map_has_five_entries(self) -> None:
        from app.core.system_groups import SYSTEM_GROUP_ROLE_MAP

        assert len(SYSTEM_GROUP_ROLE_MAP) == 5

    def test_role_map_covers_all_roles(self) -> None:
        from app.core.system_groups import SYSTEM_GROUP_ROLE_MAP

        expected = {"personal", "company", "kb_manager", "group_manager", "admin"}
        assert set(SYSTEM_GROUP_ROLE_MAP.values()) == expected

    def test_addon_keys_not_in_role_map(self) -> None:
        from app.core.system_groups import SYSTEM_GROUP_ROLE_MAP

        assert "addon_scribe" not in SYSTEM_GROUP_ROLE_MAP
        assert "addon_docs" not in SYSTEM_GROUP_ROLE_MAP

    def test_seven_system_groups_total(self) -> None:
        from app.core.system_groups import SYSTEM_GROUPS

        assert len(SYSTEM_GROUPS) == 7


class TestSyncRoleFromSystemGroup:
    @pytest.mark.asyncio
    async def test_role_bind_group_sets_user_role(self) -> None:
        from app.services.system_groups import sync_role_from_system_group

        group = MagicMock()
        group.id = 10
        group.is_system = True
        group.system_key = "role_kb_manager"
        group_result = MagicMock()
        group_result.scalar_one_or_none.return_value = group
        update_result = MagicMock()
        update_result.fetchone.return_value = (42,)
        db = AsyncMock()
        db.execute.side_effect = [group_result, update_result]
        role = await sync_role_from_system_group("user-abc", group_id=10, db=db)
        assert role == "kb_manager"
        assert db.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_non_system_group_is_noop(self) -> None:
        from app.services.system_groups import sync_role_from_system_group

        group = MagicMock()
        group.id = 20
        group.is_system = False
        group.system_key = None
        group_result = MagicMock()
        group_result.scalar_one_or_none.return_value = group
        db = AsyncMock()
        db.execute.return_value = group_result
        role = await sync_role_from_system_group("user-abc", group_id=20, db=db)
        assert role is None
        assert db.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_group_not_found_returns_none(self) -> None:
        from app.services.system_groups import sync_role_from_system_group

        group_result = MagicMock()
        group_result.scalar_one_or_none.return_value = None
        db = AsyncMock()
        db.execute.return_value = group_result
        role = await sync_role_from_system_group("user-abc", group_id=99, db=db)
        assert role is None

    @pytest.mark.asyncio
    async def test_addon_group_does_not_set_role(self) -> None:
        from app.services.system_groups import sync_role_from_system_group

        group = MagicMock()
        group.id = 30
        group.is_system = True
        group.system_key = "addon_scribe"
        group_result = MagicMock()
        group_result.scalar_one_or_none.return_value = group
        db = AsyncMock()
        db.execute.return_value = group_result
        role = await sync_role_from_system_group("user-abc", group_id=30, db=db)
        assert role is None
        assert db.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_role_admin_group_sets_admin(self) -> None:
        from app.services.system_groups import sync_role_from_system_group

        group = MagicMock()
        group.id = 40
        group.is_system = True
        group.system_key = "role_admin"
        group_result = MagicMock()
        group_result.scalar_one_or_none.return_value = group
        update_result = MagicMock()
        update_result.fetchone.return_value = (99,)
        db = AsyncMock()
        db.execute.side_effect = [group_result, update_result]
        role = await sync_role_from_system_group("user-xyz", group_id=40, db=db)
        assert role == "admin"

    @pytest.mark.asyncio
    async def test_membership_in_role_kb_manager_via_add_member(self) -> None:
        from app.services.system_groups import sync_role_from_system_group

        group = MagicMock()
        group.id = 50
        group.is_system = True
        group.system_key = "role_kb_manager"
        group_result = MagicMock()
        group_result.scalar_one_or_none.return_value = group
        update_result = MagicMock()
        update_result.fetchone.return_value = (77,)
        db = AsyncMock()
        db.execute.side_effect = [group_result, update_result]
        returned = await sync_role_from_system_group("user-77", group_id=50, db=db)
        assert returned == "kb_manager"
