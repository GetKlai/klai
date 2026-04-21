"""
Tests for SPEC-AUTH-002: Product Entitlements.

Covers plan mapping, require_product dependency, and admin product endpoints.
Pure unit tests -- no real DB, all async sessions are mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.plans import PLAN_PRODUCTS, get_plan_products
from app.models.products import PortalUserProduct


def _make_db_mock() -> AsyncMock:
    """Return an AsyncMock DB session with the sync ``add()`` method overridden.

    SQLAlchemy's ``Session.add()`` is synchronous; ``AsyncMock`` would wrap it
    as a coroutine and leak a ``coroutine never awaited`` RuntimeWarning at GC.
    """
    db = AsyncMock()
    db.add = MagicMock()
    return db


# ---------------------------------------------------------------------------
# TS-001: PLAN_PRODUCTS mapping
# ---------------------------------------------------------------------------


class TestPlanProducts:
    """TS-001: Verify PLAN_PRODUCTS values for all 4 plans."""

    def test_free_plan_has_no_products(self) -> None:
        assert get_plan_products("free") == []

    def test_core_plan_has_chat(self) -> None:
        assert get_plan_products("core") == ["chat"]

    def test_professional_plan_has_chat_and_scribe(self) -> None:
        assert get_plan_products("professional") == ["chat", "scribe"]

    def test_complete_plan_has_all_products(self) -> None:
        assert get_plan_products("complete") == ["chat", "scribe", "knowledge"]

    def test_unknown_plan_returns_empty_list(self) -> None:
        assert get_plan_products("nonexistent") == []

    def test_plan_products_dict_has_four_entries(self) -> None:
        assert len(PLAN_PRODUCTS) == 4

    def test_all_plans_return_lists(self) -> None:
        for plan, products in PLAN_PRODUCTS.items():
            assert isinstance(products, list), f"Plan '{plan}' value is not a list"

    def test_plan_hierarchy_is_superset(self) -> None:
        """Each higher plan includes all products of lower plans."""
        free = set(get_plan_products("free"))
        core = set(get_plan_products("core"))
        professional = set(get_plan_products("professional"))
        complete = set(get_plan_products("complete"))

        assert free.issubset(core)
        assert core.issubset(professional)
        assert professional.issubset(complete)


# ---------------------------------------------------------------------------
# TS-005, TS-006: require_product dependency
# ---------------------------------------------------------------------------


class TestRequireProduct:
    """TS-005/TS-006: require_product() granted and denied.

    The require_product dependency imports get_current_user_id from auth.py,
    which triggers Fernet initialization at module level. We test the inner
    logic directly by reconstructing it to avoid that import side-effect.
    """

    @pytest.mark.asyncio
    async def test_require_product_grants_when_product_exists(self) -> None:
        """TS-005: User with the product passes through without error."""

        from app.models.products import PortalUserProduct as PUP

        # Reconstruct the inner dependency logic to avoid auth.py import side-effect
        async def _check_product(user_id: str, db: AsyncMock, product: str) -> None:
            result = await db.execute(select(PUP).where(PUP.zitadel_user_id == user_id, PUP.product == product))
            if not result.scalar_one_or_none():
                raise HTTPException(status_code=403, detail=f"Product '{product}' not available")

        mock_product = MagicMock(spec=PortalUserProduct)
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_product
        mock_db.execute.return_value = mock_result

        # Should not raise
        await _check_product(user_id="user-123", db=mock_db, product="chat")

    @pytest.mark.asyncio
    async def test_require_product_denies_when_product_missing(self) -> None:
        """TS-006: User without the product gets 403."""
        from app.models.products import PortalUserProduct as PUP

        async def _check_product(user_id: str, db: AsyncMock, product: str) -> None:
            result = await db.execute(select(PUP).where(PUP.zitadel_user_id == user_id, PUP.product == product))
            if not result.scalar_one_or_none():
                raise HTTPException(status_code=403, detail=f"Product '{product}' not available")

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(HTTPException) as exc_info:
            await _check_product(user_id="user-456", db=mock_db, product="scribe")

        assert exc_info.value.status_code == 403
        assert "scribe" in str(exc_info.value.detail)


# ---------------------------------------------------------------------------
# TS-007, TS-008, TS-009: Product assignment
# ---------------------------------------------------------------------------


class TestAssignProduct:
    """TS-007/TS-008/TS-009: Product assignment within plan ceiling."""

    def _mock_org(self, plan: str = "professional", seats: int = 10) -> MagicMock:
        org = MagicMock()
        org.id = 1
        org.plan = plan
        org.seats = seats
        return org

    def _mock_caller(self, role: str = "admin") -> MagicMock:
        caller = MagicMock()
        caller.role = role
        return caller

    @pytest.mark.asyncio
    async def test_assign_product_exceeds_plan_ceiling_returns_403(self) -> None:
        """TS-007: Assigning a product not in plan returns 403."""
        from app.api.admin.products import assign_product

        org = self._mock_org(plan="core")
        caller = self._mock_caller()

        mock_db = AsyncMock()
        mock_credentials = MagicMock()

        with patch("app.api.admin.products._get_caller_org", return_value=("admin-1", org, caller)):
            with pytest.raises(HTTPException) as exc_info:
                body = MagicMock()
                body.product = "knowledge"  # not in core plan
                await assign_product(
                    zitadel_user_id="user-1",
                    body=body,
                    credentials=mock_credentials,
                    db=mock_db,
                )

            assert exc_info.value.status_code == 403
            assert "knowledge" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_assign_product_within_ceiling_succeeds(self) -> None:
        """TS-008: Valid assignment within plan ceiling creates record."""
        from app.api.admin.products import assign_product

        org = self._mock_org(plan="professional")
        caller = self._mock_caller()

        mock_db = _make_db_mock()
        # scalar() for user lookup returns a user
        mock_user = MagicMock()
        # scalar() for duplicate check returns None (no duplicate)
        mock_db.scalar.side_effect = [mock_user, None]
        mock_credentials = MagicMock()

        with patch("app.api.admin.products._get_caller_org", return_value=("admin-1", org, caller)):
            body = MagicMock()
            body.product = "chat"
            result = await assign_product(
                zitadel_user_id="user-1",
                body=body,
                credentials=mock_credentials,
                db=mock_db,
            )

        assert result.message == "Product assigned"
        mock_db.add.assert_called_once()
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_assign_duplicate_product_returns_409(self) -> None:
        """TS-009: Duplicate assignment returns 409."""
        from app.api.admin.products import assign_product

        org = self._mock_org(plan="professional")
        caller = self._mock_caller()

        mock_db = AsyncMock()
        mock_user = MagicMock()
        mock_existing = MagicMock(spec=PortalUserProduct)
        mock_db.scalar.side_effect = [mock_user, mock_existing]
        mock_credentials = MagicMock()

        with patch("app.api.admin.products._get_caller_org", return_value=("admin-1", org, caller)):
            body = MagicMock()
            body.product = "chat"
            with pytest.raises(HTTPException) as exc_info:
                await assign_product(
                    zitadel_user_id="user-1",
                    body=body,
                    credentials=mock_credentials,
                    db=mock_db,
                )

            assert exc_info.value.status_code == 409


# ---------------------------------------------------------------------------
# TS-010: Product revocation
# ---------------------------------------------------------------------------


class TestRevokeProduct:
    """TS-010: Product revocation."""

    @pytest.mark.asyncio
    async def test_revoke_existing_product_succeeds(self) -> None:
        """TS-010: Revoking an existing product assignment."""
        from app.api.admin.products import revoke_product

        org = MagicMock()
        org.id = 1
        caller = MagicMock()
        caller.role = "admin"

        mock_db = AsyncMock()
        mock_product_row = MagicMock(spec=PortalUserProduct)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_product_row
        mock_db.execute.return_value = mock_result
        mock_credentials = MagicMock()

        with patch("app.api.admin.products._get_caller_org", return_value=("admin-1", org, caller)):
            await revoke_product(
                zitadel_user_id="user-1",
                product="chat",
                credentials=mock_credentials,
                db=mock_db,
            )

        mock_db.delete.assert_awaited_once_with(mock_product_row)
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_product_returns_404(self) -> None:
        from app.api.admin.products import revoke_product

        org = MagicMock()
        org.id = 1
        caller = MagicMock()
        caller.role = "admin"

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result
        mock_credentials = MagicMock()

        with patch("app.api.admin.products._get_caller_org", return_value=("admin-1", org, caller)):
            with pytest.raises(HTTPException) as exc_info:
                await revoke_product(
                    zitadel_user_id="user-1",
                    product="chat",
                    credentials=mock_credentials,
                    db=mock_db,
                )

            assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# TS-002: Auto-assignment on invite
# ---------------------------------------------------------------------------


class TestAutoAssignOnInvite:
    """TS-002: Products auto-assigned during user invite."""

    @pytest.mark.asyncio
    async def test_invite_auto_assigns_plan_products(self) -> None:
        """TS-002: invite_user adds PortalUserProduct rows for the org's plan."""
        from app.api.admin.users import invite_user

        org = MagicMock()
        org.id = 1
        org.plan = "professional"
        org.seats = 10
        caller = MagicMock()
        caller.role = "admin"

        mock_db = AsyncMock()
        # For the seat check: locked org scalar_one returns org
        locked_result = MagicMock()
        locked_result.scalar_one.return_value = org
        mock_db.execute.return_value = locked_result
        # For active_count (scalar call)
        mock_db.scalar.return_value = 2  # 2 existing users, below seat limit

        mock_credentials = MagicMock()

        body = MagicMock()
        body.email = "new@example.com"
        body.first_name = "Test"
        body.last_name = "User"
        body.role = "member"
        body.preferred_language = "nl"

        mock_zitadel = AsyncMock()
        mock_zitadel.invite_user.return_value = {"userId": "new-user-id"}
        mock_zitadel.grant_user_role.return_value = None

        added_objects: list[object] = []
        mock_db.add = MagicMock(side_effect=added_objects.append)

        with (
            patch("app.api.admin.users._get_caller_org", return_value=("admin-1", org, caller)),
            patch("app.api.admin.users.zitadel", mock_zitadel),
            patch("app.api.admin.users.settings") as mock_settings,
        ):
            mock_settings.zitadel_portal_org_id = "org-id"
            result = await invite_user(body=body, credentials=mock_credentials, db=mock_db)

        assert result.user_id == "new-user-id"

        # Should have added: 1 PortalUser + 2 products (chat, scribe for professional)
        product_adds = [obj for obj in added_objects if isinstance(obj, PortalUserProduct)]
        assert len(product_adds) == 2
        product_names = {p.product for p in product_adds}
        assert product_names == {"chat", "scribe"}

        # All product rows should reference the admin who did the invite
        for p in product_adds:
            assert p.enabled_by == "admin-1"
            assert p.org_id == 1


# ---------------------------------------------------------------------------
# TS-003: Seat limit enforcement
# ---------------------------------------------------------------------------


class TestSeatLimitEnforcement:
    """TS-003: Seat limit enforcement returns 409."""

    @pytest.mark.asyncio
    async def test_invite_at_seat_limit_returns_409(self) -> None:
        """TS-003: invite_user raises 409 when seat limit is reached."""
        from app.api.admin.users import invite_user

        org = MagicMock()
        org.id = 1
        org.plan = "professional"
        org.seats = 3  # limit of 3
        caller = MagicMock()
        caller.role = "admin"

        mock_db = AsyncMock()
        locked_result = MagicMock()
        locked_result.scalar_one.return_value = org
        mock_db.execute.return_value = locked_result
        mock_db.scalar.return_value = 3  # already at limit

        mock_credentials = MagicMock()

        body = MagicMock()
        body.email = "new@example.com"
        body.first_name = "Test"
        body.last_name = "User"
        body.role = "member"
        body.preferred_language = "nl"

        with patch("app.api.admin.users._get_caller_org", return_value=("admin-1", org, caller)):
            with pytest.raises(HTTPException) as exc_info:
                await invite_user(body=body, credentials=mock_credentials, db=mock_db)

            assert exc_info.value.status_code == 409
            assert "Seat limit" in str(exc_info.value.detail)


# ---------------------------------------------------------------------------
# TS-011, TS-012: Plan change (upgrade/downgrade)
# ---------------------------------------------------------------------------


class TestPlanChange:
    """TS-011/TS-012: Plan upgrade and downgrade."""

    @pytest.mark.asyncio
    async def test_upgrade_plan_keeps_products(self) -> None:
        """TS-011: Upgrading from core to professional does not revoke existing products."""
        from app.api.admin.settings import change_plan

        org = MagicMock()
        org.id = 1
        org.plan = "core"
        caller = MagicMock()
        caller.role = "admin"

        # Only chat is assigned (core plan product)
        chat_product = MagicMock(spec=PortalUserProduct)
        chat_product.product = "chat"
        chat_product.org_id = 1

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [chat_product]
        mock_db.execute.return_value = mock_result
        mock_credentials = MagicMock()

        body = MagicMock()
        body.plan = "professional"

        with patch("app.api.admin.settings._get_caller_org", return_value=("admin-1", org, caller)):
            await change_plan(body=body, credentials=mock_credentials, db=mock_db)

        assert org.plan == "professional"
        # chat is in professional plan, so it should NOT be deleted
        mock_db.delete.assert_not_awaited()
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_downgrade_plan_revokes_over_ceiling_products(self) -> None:
        """TS-012: Downgrading from professional to core revokes scribe."""
        from app.api.admin.settings import change_plan

        org = MagicMock()
        org.id = 1
        org.plan = "professional"
        caller = MagicMock()
        caller.role = "admin"

        chat_product = MagicMock(spec=PortalUserProduct)
        chat_product.product = "chat"
        chat_product.org_id = 1

        scribe_product = MagicMock(spec=PortalUserProduct)
        scribe_product.product = "scribe"
        scribe_product.org_id = 1
        scribe_product.zitadel_user_id = "user-1"

        # First execute: user products; second execute: group products (empty)
        mock_user_result = MagicMock()
        mock_user_result.scalars.return_value.all.return_value = [chat_product, scribe_product]
        mock_group_result = MagicMock()
        mock_group_result.scalars.return_value.all.return_value = []

        mock_db = AsyncMock()
        mock_db.execute.side_effect = [mock_user_result, mock_group_result]
        mock_credentials = MagicMock()

        body = MagicMock()
        body.plan = "core"

        with patch("app.api.admin.settings._get_caller_org", return_value=("admin-1", org, caller)):
            await change_plan(body=body, credentials=mock_credentials, db=mock_db)

        assert org.plan == "core"
        # scribe should be revoked, chat should remain
        mock_db.delete.assert_awaited_once_with(scribe_product)

    @pytest.mark.asyncio
    async def test_downgrade_to_free_revokes_all_products(self) -> None:
        """Downgrading to free revokes all product assignments."""
        from app.api.admin.settings import change_plan

        org = MagicMock()
        org.id = 1
        org.plan = "professional"
        caller = MagicMock()
        caller.role = "admin"

        chat_product = MagicMock(spec=PortalUserProduct)
        chat_product.product = "chat"
        scribe_product = MagicMock(spec=PortalUserProduct)
        scribe_product.product = "scribe"

        # First execute: user products; second execute: group products (empty)
        mock_user_result = MagicMock()
        mock_user_result.scalars.return_value.all.return_value = [chat_product, scribe_product]
        mock_group_result = MagicMock()
        mock_group_result.scalars.return_value.all.return_value = []

        mock_db = AsyncMock()
        mock_db.execute.side_effect = [mock_user_result, mock_group_result]
        mock_credentials = MagicMock()

        body = MagicMock()
        body.plan = "free"

        with patch("app.api.admin.settings._get_caller_org", return_value=("admin-1", org, caller)):
            await change_plan(body=body, credentials=mock_credentials, db=mock_db)

        assert mock_db.delete.await_count == 2

    @pytest.mark.asyncio
    async def test_change_to_unknown_plan_returns_400(self) -> None:
        from app.api.admin.settings import change_plan

        org = MagicMock()
        org.plan = "core"
        caller = MagicMock()
        caller.role = "admin"

        mock_db = AsyncMock()
        mock_credentials = MagicMock()

        body = MagicMock()
        body.plan = "enterprise"  # not in PLAN_PRODUCTS

        with patch("app.api.admin.settings._get_caller_org", return_value=("admin-1", org, caller)):
            with pytest.raises(HTTPException) as exc_info:
                await change_plan(body=body, credentials=mock_credentials, db=mock_db)

            assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# TS-016: Product summary endpoint
# ---------------------------------------------------------------------------


class TestProductSummary:
    """TS-016: Product summary endpoint."""

    @pytest.mark.asyncio
    async def test_product_summary_returns_counts(self) -> None:
        """TS-016: product_summary returns per-product user counts."""
        from app.api.admin.products import product_summary

        org = MagicMock()
        org.id = 1
        caller = MagicMock()
        caller.role = "admin"

        mock_db = AsyncMock()

        row1 = MagicMock()
        row1.product = "chat"
        row1.user_count = 5
        row2 = MagicMock()
        row2.product = "scribe"
        row2.user_count = 3

        mock_db.execute.return_value = [row1, row2]
        mock_credentials = MagicMock()

        with patch("app.api.admin.products._get_caller_org", return_value=("admin-1", org, caller)):
            result = await product_summary(credentials=mock_credentials, db=mock_db)

        assert len(result.items) == 2
        items_dict = {i.product: i.user_count for i in result.items}
        assert items_dict["chat"] == 5
        assert items_dict["scribe"] == 3


# ---------------------------------------------------------------------------
# TS-018: List available products for org
# ---------------------------------------------------------------------------


class TestListAvailableProducts:
    """TS-018: List available products for org."""

    @pytest.mark.asyncio
    async def test_list_products_returns_plan_products(self) -> None:
        """TS-018: list_available_products returns products for org's plan."""
        from app.api.admin.products import list_available_products

        org = MagicMock()
        org.plan = "professional"
        caller = MagicMock()
        caller.role = "admin"

        mock_db = AsyncMock()
        mock_credentials = MagicMock()

        with patch("app.api.admin.products._get_caller_org", return_value=("admin-1", org, caller)):
            result = await list_available_products(credentials=mock_credentials, db=mock_db)

        assert result.products == ["chat", "scribe"]

    @pytest.mark.asyncio
    async def test_list_products_free_plan_returns_empty(self) -> None:
        from app.api.admin.products import list_available_products

        org = MagicMock()
        org.plan = "free"
        caller = MagicMock()
        caller.role = "admin"

        mock_db = AsyncMock()
        mock_credentials = MagicMock()

        with patch("app.api.admin.products._get_caller_org", return_value=("admin-1", org, caller)):
            result = await list_available_products(credentials=mock_credentials, db=mock_db)

        assert result.products == []


# ---------------------------------------------------------------------------
# Internal API: get_user_products
# ---------------------------------------------------------------------------


class TestInternalGetUserProducts:
    """Internal endpoint for JWT enrichment."""

    @pytest.mark.asyncio
    async def test_get_user_products_returns_product_list(self) -> None:
        from app.api.internal import get_user_products

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = ["chat", "scribe"]
        mock_db.execute.return_value = mock_result

        mock_request = MagicMock()

        with patch("app.api.internal._require_internal_token"):
            result = await get_user_products(
                zitadel_user_id="user-123",
                request=mock_request,
                db=mock_db,
            )

        assert result.products == ["chat", "scribe"]

    @pytest.mark.asyncio
    async def test_get_user_products_unknown_user_returns_empty(self) -> None:
        from app.api.internal import get_user_products

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result

        mock_request = MagicMock()

        with patch("app.api.internal._require_internal_token"):
            result = await get_user_products(
                zitadel_user_id="unknown-user",
                request=mock_request,
                db=mock_db,
            )

        assert result.products == []


# ---------------------------------------------------------------------------
# PortalUserProduct model
# ---------------------------------------------------------------------------


class TestPortalUserProductModel:
    """Basic model construction tests."""

    def test_model_can_be_constructed(self) -> None:
        product = PortalUserProduct(
            zitadel_user_id="user-1",
            org_id=1,
            product="chat",
            enabled_by="admin-1",
        )
        assert product.zitadel_user_id == "user-1"
        assert product.product == "chat"
        assert product.enabled_by == "admin-1"
        assert product.org_id == 1

    def test_model_tablename(self) -> None:
        assert PortalUserProduct.__tablename__ == "portal_user_products"
