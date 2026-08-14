"""Tests for ``require_connector_manage_access`` (app/services/access.py).

Regression for the Voys/Ascend incident (2026-08-14): a kb_manager walked
through the connector-wizard and got "403: Owner access required" on the
crawl-preview, even though profiles.py grants kb_manager+ the
``Capability.KB_CONNECTORS`` capability ("may use all connector types").
Root cause: two diverging owner-checks (connectors.py vs crawl-preview) that
both ignored the profile-capability layer, so on an org KB with
``default_org_role='contributor'`` only the KB creator could manage
connectors.

The helper is the single source of truth for connector CRUD, crawl-preview
and auth-probe. Matrix under test:

| caller                                   | org KB (default contributor) | personal KB |
|------------------------------------------|------------------------------|-------------|
| platform admin                           | allowed                      | allowed     |
| KB owner (explicit grant / creator)      | allowed                      | allowed     |
| kb_manager, contributor via default      | allowed  (the fix)           | 403         |
| kb_manager, explicit viewer grant        | 403 (KB-role layer wins)     | 403         |
| company profile, contributor via default | 403 (no capability)          | 403         |
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.services.access import require_connector_manage_access
from tests.conftest import make_perms

_ORG_ID = 101


def _kb(*, owner_type: str = "org", default_org_role: str | None = "contributor") -> MagicMock:
    kb = MagicMock()
    kb.id = 139
    kb.org_id = _ORG_ID
    kb.owner_type = owner_type
    kb.default_org_role = default_org_role
    kb.created_by = "creator-uid"
    return kb


def _patch_role(role: str | None):
    return patch(
        "app.services.access.get_user_role_for_kb",
        new_callable=AsyncMock,
        return_value=role,
    )


@pytest.mark.asyncio
async def test_platform_admin_bypasses_role_check() -> None:
    perms = make_perms(role="admin", is_platform_admin=True, org_id=_ORG_ID)
    with _patch_role(None) as role_mock:
        await require_connector_manage_access(_kb(), perms, MagicMock())
    role_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_kb_owner_is_allowed() -> None:
    perms = make_perms(role="kb_manager", org_id=_ORG_ID)
    with _patch_role("owner"):
        await require_connector_manage_access(_kb(), perms, MagicMock())


@pytest.mark.asyncio
async def test_kb_manager_contributor_on_org_kb_is_allowed() -> None:
    """THE regression: kb_manager + default contributor role on an org KB
    must be able to manage connectors (Harmen / Ascend)."""
    perms = make_perms(role="kb_manager", org_id=_ORG_ID)
    with _patch_role("contributor"):
        await require_connector_manage_access(_kb(), perms, MagicMock())


@pytest.mark.asyncio
async def test_explicit_viewer_grant_still_restricts_kb_manager() -> None:
    """The KB-role layer keeps its veto: an explicit viewer grant on the KB
    blocks connector management even for capability holders."""
    perms = make_perms(role="kb_manager", org_id=_ORG_ID)
    with _patch_role("viewer"):
        with pytest.raises(HTTPException) as exc:
            await require_connector_manage_access(_kb(), perms, MagicMock())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_company_profile_contributor_is_denied() -> None:
    """company/personal profiles lack Capability.KB_CONNECTORS — contributor
    KB-role alone is not enough."""
    perms = make_perms(role="company", org_id=_ORG_ID)
    with _patch_role("contributor"):
        with pytest.raises(HTTPException) as exc:
            await require_connector_manage_access(_kb(), perms, MagicMock())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_kb_manager_denied_on_someone_elses_personal_kb() -> None:
    """Personal KBs stay owner-only for non-platform-admins."""
    perms = make_perms(role="kb_manager", org_id=_ORG_ID)
    with _patch_role("contributor"):
        with pytest.raises(HTTPException) as exc:
            await require_connector_manage_access(_kb(owner_type="user", default_org_role=None), perms, MagicMock())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_no_role_at_all_is_denied() -> None:
    perms = make_perms(role="kb_manager", org_id=_ORG_ID)
    with _patch_role(None):
        with pytest.raises(HTTPException) as exc:
            await require_connector_manage_access(_kb(), perms, MagicMock())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_role_lookup_receives_default_org_role() -> None:
    """The old connectors.py check omitted default_org_role, so org members
    resolved to None instead of contributor. Pin the kwargs."""
    perms = make_perms(role="kb_manager", org_id=_ORG_ID)
    kb = _kb()
    with _patch_role("contributor") as role_mock:
        await require_connector_manage_access(kb, perms, MagicMock())
    kwargs = role_mock.await_args.kwargs
    assert kwargs["default_org_role"] == "contributor"
    assert kwargs["kb_org_id"] == _ORG_ID
    assert kwargs["kb_created_by"] == "creator-uid"
