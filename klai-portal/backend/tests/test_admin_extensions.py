"""SPEC-PORTAL-EXTENSIONS-UNIFY-001 — read-only /api/admin/extensions endpoint.

Tests the GET endpoint that powers the /admin/settings Uitbreidingen UI.
The PATCH endpoint was retired during cleanup — writes consolidate on
``PATCH /api/admin/orgs/{slug}/platform-unlocks`` (see
test_platform_unlocks.py for that coverage).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.admin.extensions import list_extensions
from tests.conftest import make_org, make_perms


def _db_with_org(org: MagicMock) -> AsyncMock:
    """Mock AsyncSession that returns the given org on ``db.get(PortalOrg, org_id)``.

    The endpoint resolves the caller's org via ``_load_org_or_500`` which uses
    ``db.get``; older versions of these tests stubbed ``db.execute`` directly,
    which silently no-op'd after the cleanup PR.
    """
    db = AsyncMock()
    db.get = AsyncMock(return_value=org)
    return db


class TestListExtensions:
    @pytest.mark.asyncio
    async def test_returns_full_known_features_list(self) -> None:
        perms = make_perms(role="admin", org_id=42, platform_unlocked_features=["scribe", "docs"])
        org = make_org(org_id=42, slug="acme", platform_unlocked_features=["scribe", "docs"])
        db = _db_with_org(org)

        result = await list_extensions(perms=perms, db=db)
        keys = {item.key for item in result.extensions}
        assert keys == {"partner_api", "widgets", "custom_mcps", "scribe", "docs"}
        enabled = {item.key for item in result.extensions if item.enabled}
        assert enabled == {"scribe", "docs"}

    @pytest.mark.asyncio
    async def test_manageable_by_caller_false_for_tenant_admin(self) -> None:
        perms = make_perms(role="admin", org_id=42, is_platform_admin=False)
        org = make_org(org_id=42, slug="acme")
        db = _db_with_org(org)
        result = await list_extensions(perms=perms, db=db)
        for item in result.extensions:
            assert item.manageable_by_caller is False

    @pytest.mark.asyncio
    async def test_manageable_by_caller_true_for_platform_admin(self) -> None:
        perms = make_perms(role="admin", org_id=1, is_platform_admin=True)
        org = make_org(org_id=1, slug="getklai")
        db = _db_with_org(org)
        result = await list_extensions(perms=perms, db=db)
        for item in result.extensions:
            assert item.manageable_by_caller is True

    @pytest.mark.asyncio
    async def test_response_payload_is_sorted_and_language_agnostic(self) -> None:
        """SPEC-PORTAL-EXTENSIONS-UNIFY-001 polish: backend returns keys only,
        no language-specific label/description. Frontend maps to Paraglide
        messages client-side so NL/EN switching is server-free."""
        perms = make_perms(role="admin", org_id=42)
        org = make_org(org_id=42, slug="acme", platform_unlocked_features=["partner_api"])
        db = _db_with_org(org)
        result = await list_extensions(perms=perms, db=db)
        # Sorted by key alphabetically.
        keys_in_order = [item.key for item in result.extensions]
        assert keys_in_order == sorted(keys_in_order)
        # No label/description fields leak through — the schema is i18n-clean.
        for item in result.extensions:
            assert not hasattr(item, "label") or "label" not in item.model_fields
            assert not hasattr(item, "description") or "description" not in item.model_fields
