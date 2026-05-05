"""Tests for personal-scope notebook tenant isolation (AC-13, SPEC-TI-004-RLS-RESEARCH).

Finding A-10 + A-12: _get_notebook_or_404 personal-scope branch only checked
owner_user_id but NOT tenant_id. A user who switches orgs (or a multi-org user
whose auth resolves the wrong org) could access personal notebooks that belong
to a different tenant.

AC-13: personal notebook in previous tenant must NOT be visible in new tenant context.
"""

from __future__ import annotations

import os

os.environ.setdefault("POSTGRES_DSN", "postgresql+asyncpg://test/test")
os.environ.setdefault("RETRIEVAL_API_URL", "http://retrieval-api:8040")
os.environ.setdefault("RETRIEVAL_API_INTERNAL_SECRET", "test-secret")
os.environ.setdefault("ZITADEL_API_AUDIENCE", "test-audience")

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

TENANT_A = str(uuid.uuid4())
TENANT_B = str(uuid.uuid4())
USER_ID = "user-abc123"


def _make_notebook(
    owner_user_id: str,
    tenant_id: str,
    scope: str = "personal",
) -> MagicMock:
    """Build a mock Notebook ORM object."""
    nb = MagicMock()
    nb.id = "nb_test"
    nb.owner_user_id = owner_user_id
    nb.tenant_id = uuid.UUID(tenant_id)
    nb.scope = scope
    nb.name = "Test notebook"
    nb.description = None
    nb.default_mode = "narrow"
    nb.save_history = True
    nb.created_at = datetime.utcnow()
    nb.updated_at = datetime.utcnow()
    return nb


def _make_user(user_id: str, tenant_id: str) -> MagicMock:
    from app.core.auth import CurrentUser

    return CurrentUser(
        user_id=user_id,
        tenant_id=tenant_id,
        zitadel_org_id=tenant_id,
        roles=[],
    )


@pytest.mark.asyncio
async def test_personal_notebook_same_tenant_accessible():
    """Owner can access their personal notebook in the same tenant."""
    from app.api.notebooks import _get_notebook_or_404

    nb = _make_notebook(owner_user_id=USER_ID, tenant_id=TENANT_A)
    user = _make_user(user_id=USER_ID, tenant_id=TENANT_A)

    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = nb
    db.execute = AsyncMock(return_value=mock_result)

    result = await _get_notebook_or_404("nb_test", db, user)
    assert result is nb


@pytest.mark.asyncio
async def test_personal_notebook_different_tenant_not_accessible():
    """Personal notebook in tenant-A is NOT accessible when user is in tenant-B.

    This is the A-10 bug scenario: a user who moved from org-A to org-B
    should NOT see org-A's personal notebooks when operating as org-B.
    """
    from fastapi import HTTPException

    from app.api.notebooks import _get_notebook_or_404

    # Notebook belongs to tenant-A
    nb = _make_notebook(owner_user_id=USER_ID, tenant_id=TENANT_A)
    # User is now in tenant-B (e.g., after moving orgs or multi-org JWT resolves B)
    user = _make_user(user_id=USER_ID, tenant_id=TENANT_B)

    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = nb
    db.execute = AsyncMock(return_value=mock_result)

    with pytest.raises(HTTPException) as exc_info:
        await _get_notebook_or_404("nb_test", db, user)

    assert exc_info.value.status_code == 404, (
        "Cross-tenant personal notebook access must return 404 (not 403) "
        "to avoid leaking notebook existence to wrong-tenant callers"
    )


@pytest.mark.asyncio
async def test_personal_notebook_other_user_same_tenant_not_accessible():
    """Another user's personal notebook in same tenant is not accessible."""
    from fastapi import HTTPException

    from app.api.notebooks import _get_notebook_or_404

    other_user_id = "other-user-xyz"
    nb = _make_notebook(owner_user_id=other_user_id, tenant_id=TENANT_A)
    user = _make_user(user_id=USER_ID, tenant_id=TENANT_A)

    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = nb
    db.execute = AsyncMock(return_value=mock_result)

    with pytest.raises(HTTPException) as exc_info:
        await _get_notebook_or_404("nb_test", db, user)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_org_notebook_same_tenant_accessible():
    """Org-scope notebook is accessible for any user in the same tenant."""
    from app.api.notebooks import _get_notebook_or_404

    other_user_id = "org-admin-xyz"
    nb = _make_notebook(owner_user_id=other_user_id, tenant_id=TENANT_A, scope="org")
    user = _make_user(user_id=USER_ID, tenant_id=TENANT_A)

    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = nb
    db.execute = AsyncMock(return_value=mock_result)

    result = await _get_notebook_or_404("nb_test", db, user)
    assert result is nb


@pytest.mark.asyncio
async def test_org_notebook_different_tenant_not_accessible():
    """Org-scope notebook in tenant-A is NOT accessible for tenant-B user."""
    from fastapi import HTTPException

    from app.api.notebooks import _get_notebook_or_404

    nb = _make_notebook(owner_user_id=USER_ID, tenant_id=TENANT_A, scope="org")
    user = _make_user(user_id=USER_ID, tenant_id=TENANT_B)

    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = nb
    db.execute = AsyncMock(return_value=mock_result)

    with pytest.raises(HTTPException) as exc_info:
        await _get_notebook_or_404("nb_test", db, user)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_missing_notebook_returns_404():
    """Non-existent notebook ID → 404."""
    from fastapi import HTTPException

    from app.api.notebooks import _get_notebook_or_404

    user = _make_user(user_id=USER_ID, tenant_id=TENANT_A)

    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=mock_result)

    with pytest.raises(HTTPException) as exc_info:
        await _get_notebook_or_404("nb_nonexistent", db, user)

    assert exc_info.value.status_code == 404
