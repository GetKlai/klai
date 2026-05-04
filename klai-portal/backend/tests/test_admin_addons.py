from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _mock_org(enabled_addons=None, plan="core"):
    org = MagicMock()
    org.id = 42
    org.plan = plan
    org.enabled_addons = enabled_addons or []
    return org


def _mock_caller(role="admin"):
    caller = MagicMock()
    caller.role = role
    return caller


def _make_db_mock() -> AsyncMock:
    """AsyncMock DB session with sync ``add()`` to avoid coroutine warnings."""
    db = AsyncMock()
    db.add = MagicMock()
    return db


class TestGetAddons:
    @pytest.mark.asyncio
    async def test_returns_current_state(self) -> None:
        from app.api.admin.settings import get_addons

        org = _mock_org(enabled_addons=["scribe"])
        caller = _mock_caller()
        mock_db = AsyncMock()
        mock_creds = MagicMock()
        with patch("app.api.admin.settings._get_caller_org", return_value=("admin-1", org, caller)):
            result = await get_addons(credentials=mock_creds, db=mock_db)
        assert result.enabled_addons == ["scribe"]

    @pytest.mark.asyncio
    async def test_returns_empty_list_by_default(self) -> None:
        from app.api.admin.settings import get_addons

        org = _mock_org(enabled_addons=[])
        caller = _mock_caller()
        mock_db = AsyncMock()
        mock_creds = MagicMock()
        with patch("app.api.admin.settings._get_caller_org", return_value=("admin-1", org, caller)):
            result = await get_addons(credentials=mock_creds, db=mock_db)
        assert result.enabled_addons == []


class TestUpdateAddons:
    @pytest.mark.asyncio
    async def test_patch_valid_addon_updates_org(self) -> None:
        from app.api.admin.settings import AddonsUpdate, update_addons

        org = _mock_org(enabled_addons=[])
        caller = _mock_caller()
        mock_db = AsyncMock()
        mock_creds = MagicMock()
        body = AddonsUpdate(enabled_addons=["scribe"])
        with (
            patch("app.api.admin.settings._get_caller_org", return_value=("admin-1", org, caller)),
            patch("app.api.admin.settings.log_event", new=AsyncMock()),
            patch("app.api.admin.settings.emit_event") as mock_emit,
        ):
            result = await update_addons(body=body, credentials=mock_creds, db=mock_db)
        assert result.enabled_addons == ["scribe"]
        assert org.enabled_addons == ["scribe"]
        mock_db.commit.assert_called_once()
        assert mock_emit.call_args.args[0] == "tenant.addons_updated"

    @pytest.mark.asyncio
    async def test_patch_unknown_addon_returns_400(self) -> None:
        from app.api.admin.settings import AddonsUpdate, update_addons

        org = _mock_org()
        caller = _mock_caller()
        mock_db = AsyncMock()
        mock_creds = MagicMock()
        body = AddonsUpdate(enabled_addons=["hacker_product"])
        with patch("app.api.admin.settings._get_caller_org", return_value=("admin-1", org, caller)):
            with pytest.raises(HTTPException) as exc_info:
                await update_addons(body=body, credentials=mock_creds, db=mock_db)
        assert exc_info.value.status_code == 400
        assert "hacker_product" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_patch_both_addons(self) -> None:
        from app.api.admin.settings import AddonsUpdate, update_addons

        org = _mock_org()
        caller = _mock_caller()
        mock_db = AsyncMock()
        mock_creds = MagicMock()
        body = AddonsUpdate(enabled_addons=["scribe", "docs"])
        with (
            patch("app.api.admin.settings._get_caller_org", return_value=("admin-1", org, caller)),
            patch("app.api.admin.settings.log_event", new=AsyncMock()),
            patch("app.api.admin.settings.emit_event"),
        ):
            result = await update_addons(body=body, credentials=mock_creds, db=mock_db)
        assert set(result.enabled_addons) == {"scribe", "docs"}

    @pytest.mark.asyncio
    async def test_patch_empty_disables_all(self) -> None:
        from app.api.admin.settings import AddonsUpdate, update_addons

        org = _mock_org(enabled_addons=["scribe"])
        caller = _mock_caller()
        mock_db = AsyncMock()
        mock_creds = MagicMock()
        body = AddonsUpdate(enabled_addons=[])
        with (
            patch("app.api.admin.settings._get_caller_org", return_value=("admin-1", org, caller)),
            patch("app.api.admin.settings.log_event", new=AsyncMock()),
            patch("app.api.admin.settings.emit_event"),
        ):
            result = await update_addons(body=body, credentials=mock_creds, db=mock_db)
        assert result.enabled_addons == []

    @pytest.mark.asyncio
    async def test_non_admin_rejected(self) -> None:
        from app.api.admin.settings import AddonsUpdate, update_addons

        org = _mock_org()
        caller = _mock_caller(role="company")
        mock_db = AsyncMock()
        mock_creds = MagicMock()
        body = AddonsUpdate(enabled_addons=["scribe"])
        with patch("app.api.admin.settings._get_caller_org", return_value=("user-1", org, caller)):
            with pytest.raises(HTTPException) as exc_info:
                await update_addons(body=body, credentials=mock_creds, db=mock_db)
        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# SPEC-PORTAL-PROFILES-001 P2.4 — addon assignment via products / groups
# Closes the gap where enabled_addons unlocked the toggle but no endpoint
# would accept the addon as an assignable product.
# ---------------------------------------------------------------------------


class TestListAvailableProductsAddons:
    @pytest.mark.asyncio
    async def test_addon_appears_when_enabled(self) -> None:
        from app.api.admin.products import list_available_products

        org = _mock_org(plan="core", enabled_addons=["scribe"])
        caller = _mock_caller()
        mock_db = AsyncMock()
        mock_creds = MagicMock()
        with patch("app.api.admin.products._get_caller_org", return_value=("admin-1", org, caller)):
            result = await list_available_products(credentials=mock_creds, db=mock_db)
        assert set(result.products) == {"chat", "knowledge", "scribe"}

    @pytest.mark.asyncio
    async def test_addon_absent_when_disabled(self) -> None:
        from app.api.admin.products import list_available_products

        org = _mock_org(plan="core", enabled_addons=[])
        caller = _mock_caller()
        mock_db = AsyncMock()
        mock_creds = MagicMock()
        with patch("app.api.admin.products._get_caller_org", return_value=("admin-1", org, caller)):
            result = await list_available_products(credentials=mock_creds, db=mock_db)
        assert "scribe" not in result.products
        assert "docs" not in result.products

    @pytest.mark.asyncio
    async def test_both_addons_appear_when_both_enabled(self) -> None:
        from app.api.admin.products import list_available_products

        org = _mock_org(plan="professional", enabled_addons=["scribe", "docs"])
        caller = _mock_caller()
        mock_db = AsyncMock()
        mock_creds = MagicMock()
        with patch("app.api.admin.products._get_caller_org", return_value=("admin-1", org, caller)):
            result = await list_available_products(credentials=mock_creds, db=mock_db)
        assert set(result.products) == {"chat", "knowledge", "scribe", "docs"}


class TestAssignUserProductAddon:
    @pytest.mark.asyncio
    async def test_assign_addon_succeeds_when_enabled(self) -> None:
        from app.api.admin.products import assign_product

        org = _mock_org(plan="core", enabled_addons=["scribe"])
        caller = _mock_caller()
        mock_db = _make_db_mock()
        # User lookup returns a user; duplicate check returns None.
        mock_db.scalar.side_effect = [MagicMock(), None]
        mock_creds = MagicMock()

        body = MagicMock()
        body.product = "scribe"
        with (
            patch("app.api.admin.products._get_caller_org", return_value=("admin-1", org, caller)),
            patch("app.api.admin.products.log_event", new=AsyncMock()),
        ):
            result = await assign_product(
                zitadel_user_id="user-1",
                body=body,
                credentials=mock_creds,
                db=mock_db,
            )
        assert result.message == "Product assigned"
        mock_db.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_assign_addon_rejected_when_not_enabled(self) -> None:
        from app.api.admin.products import assign_product

        org = _mock_org(plan="core", enabled_addons=[])
        caller = _mock_caller()
        mock_db = AsyncMock()
        mock_creds = MagicMock()

        body = MagicMock()
        body.product = "scribe"
        with patch("app.api.admin.products._get_caller_org", return_value=("admin-1", org, caller)):
            with pytest.raises(HTTPException) as exc_info:
                await assign_product(
                    zitadel_user_id="user-1",
                    body=body,
                    credentials=mock_creds,
                    db=mock_db,
                )
        assert exc_info.value.status_code == 403
        assert "scribe" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_assign_unknown_product_still_rejected(self) -> None:
        from app.api.admin.products import assign_product

        org = _mock_org(plan="professional", enabled_addons=["scribe", "docs"])
        caller = _mock_caller()
        mock_db = AsyncMock()
        mock_creds = MagicMock()

        body = MagicMock()
        body.product = "hacker_product"
        with patch("app.api.admin.products._get_caller_org", return_value=("admin-1", org, caller)):
            with pytest.raises(HTTPException) as exc_info:
                await assign_product(
                    zitadel_user_id="user-1",
                    body=body,
                    credentials=mock_creds,
                    db=mock_db,
                )
        assert exc_info.value.status_code == 403


class TestAssignGroupProductAddon:
    @pytest.mark.asyncio
    async def test_assign_addon_succeeds_when_enabled(self) -> None:
        from datetime import UTC, datetime

        from app.api.groups import assign_group_product

        org = _mock_org(plan="core", enabled_addons=["docs"])
        caller = _mock_caller()
        mock_db = _make_db_mock()

        # Real flow loads enabled_at via db.refresh() from a server_default;
        # simulate that so the GroupProductOut construction succeeds.
        async def _refresh_side_effect(record):
            record.enabled_at = datetime.now(UTC)

        mock_db.refresh = AsyncMock(side_effect=_refresh_side_effect)
        mock_creds = MagicMock()

        body = MagicMock()
        body.product = "docs"

        with (
            patch("app.api.groups._get_caller_org", return_value=("admin-1", org, caller)),
            patch("app.api.groups._get_group_or_404", new=AsyncMock(return_value=MagicMock())),
            patch("app.api.groups.log_event", new=AsyncMock()),
        ):
            result = await assign_group_product(
                group_id=7,
                body=body,
                credentials=mock_creds,
                db=mock_db,
            )

        assert result.product == "docs"
        mock_db.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_assign_addon_rejected_when_not_enabled(self) -> None:
        from app.api.groups import assign_group_product

        org = _mock_org(plan="core", enabled_addons=[])
        caller = _mock_caller()
        mock_db = AsyncMock()
        mock_creds = MagicMock()

        body = MagicMock()
        body.product = "docs"

        with (
            patch("app.api.groups._get_caller_org", return_value=("admin-1", org, caller)),
            patch("app.api.groups._get_group_or_404", new=AsyncMock(return_value=MagicMock())),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await assign_group_product(
                    group_id=7,
                    body=body,
                    credentials=mock_creds,
                    db=mock_db,
                )
        assert exc_info.value.status_code == 403
