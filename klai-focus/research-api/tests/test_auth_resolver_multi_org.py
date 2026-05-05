"""Tests for multi-org auth resolver fix (Finding A-12, SPEC-TI-004-RLS-RESEARCH).

AC-12: _get_user_org uses JWT resourceowner claim as authoritative tenant
selector. Multi-org users have one portal_users row per org; the JWT
resourceowner tells us which org the token was issued for.

Key invariants:
- JWT resourceowner claim is REQUIRED — no fall-back.
- A matching portal_users row must exist for (user_id, resourceowner_org_id).
- If no row: 403 user_not_in_resourceowner_tenant.
- set_tenant() is called with the resolved zitadel_org_id so RLS is enforced.
"""

from __future__ import annotations

import os

os.environ.setdefault("POSTGRES_DSN", "postgresql+asyncpg://test/test")
os.environ.setdefault("RETRIEVAL_API_URL", "http://retrieval-api:8040")
os.environ.setdefault("RETRIEVAL_API_INTERNAL_SECRET", "test-secret")
os.environ.setdefault("ZITADEL_API_AUDIENCE", "test-audience")

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Constants for test data
USER_ID = "user-zitadel-sub-abc123"
ORG_A_ZITADEL_ID = str(uuid.uuid4())
ORG_B_ZITADEL_ID = str(uuid.uuid4())
ORG_A_PORTAL_ID = 1
ORG_B_PORTAL_ID = 2

# Canonical JWT claim name for org context
RESOURCEOWNER_CLAIM = "urn:zitadel:iam:org:project:resourceowner"
ROLES_CLAIM = "urn:zitadel:iam:org:project:roles"


def _make_payload(user_id: str, resourceowner_id: str, roles: dict | None = None) -> dict:
    """Build a minimal Zitadel JWT payload."""
    return {
        "sub": user_id,
        RESOURCEOWNER_CLAIM: resourceowner_id,
        ROLES_CLAIM: roles or {},
    }


def _make_db_row(portal_id: int, zitadel_org_id: str):
    """Simulate a portal_users JOIN portal_orgs row."""
    row = MagicMock()
    row.__getitem__ = lambda self, i: [portal_id, zitadel_org_id][i]
    return row


@pytest.mark.asyncio
async def test_get_current_user_selects_resourceowner_tenant():
    """JWT resourceowner determines which tenant row is selected for multi-org user."""
    from app.core import auth as auth_mod

    payload = _make_payload(USER_ID, ORG_A_ZITADEL_ID)

    db = AsyncMock()
    db.add = MagicMock()
    # Simulate DB returning org-A row
    mock_result = MagicMock()
    mock_result.fetchone.return_value = _make_db_row(ORG_A_PORTAL_ID, ORG_A_ZITADEL_ID)
    db.execute = AsyncMock(return_value=mock_result)

    set_tenant_calls: list[str] = []

    async def _fake_set_tenant(session, tid):
        set_tenant_calls.append(tid)

    with (
        patch.object(auth_mod, "_decode_token", AsyncMock(return_value=payload)),
        patch.object(auth_mod, "set_tenant", _fake_set_tenant),
    ):
        from fastapi.security import HTTPAuthorizationCredentials

        creds = MagicMock(spec=HTTPAuthorizationCredentials)
        creds.credentials = "fake-token"

        user = await auth_mod.get_current_user(credentials=creds, db=db)

    assert user.user_id == USER_ID
    assert user.tenant_id == ORG_A_ZITADEL_ID
    assert user.zitadel_org_id == ORG_A_ZITADEL_ID
    # Verify set_tenant was called with org-A
    assert set_tenant_calls == [ORG_A_ZITADEL_ID], (
        "set_tenant must be called with the JWT-resolved tenant_id"
    )


@pytest.mark.asyncio
async def test_get_current_user_uses_resourceowner_in_query():
    """DB query must include WHERE po.zitadel_org_id = :rid to filter by resourceowner."""
    from app.core import auth as auth_mod

    payload = _make_payload(USER_ID, ORG_B_ZITADEL_ID)

    executed_params: list[dict] = []

    db = AsyncMock()
    db.add = MagicMock()

    mock_result = MagicMock()
    mock_result.fetchone.return_value = _make_db_row(ORG_B_PORTAL_ID, ORG_B_ZITADEL_ID)

    async def capture_execute(stmt, params=None):
        if params:
            executed_params.append(params)
        return mock_result

    db.execute = capture_execute

    with (
        patch.object(auth_mod, "_decode_token", AsyncMock(return_value=payload)),
        patch.object(auth_mod, "set_tenant", AsyncMock()),
    ):
        from fastapi.security import HTTPAuthorizationCredentials

        creds = MagicMock(spec=HTTPAuthorizationCredentials)
        creds.credentials = "fake-token"

        await auth_mod.get_current_user(credentials=creds, db=db)

    # The query must pass both uid and rid
    assert any("uid" in p and "rid" in p for p in executed_params), (
        "Query must bind both :uid (user_id) and :rid (resourceowner_id)"
    )
    assert any(p.get("rid") == ORG_B_ZITADEL_ID for p in executed_params), (
        "Query must filter by resourceowner_id from JWT"
    )


@pytest.mark.asyncio
async def test_get_current_user_raises_403_when_no_row_for_resourceowner():
    """User with no portal_users row for the JWT resourceowner org → 403."""
    from fastapi import HTTPException

    from app.core import auth as auth_mod

    payload = _make_payload(USER_ID, ORG_B_ZITADEL_ID)

    db = AsyncMock()
    db.add = MagicMock()
    # No matching row (user exists in Zitadel but not in org-B in klai)
    mock_result = MagicMock()
    mock_result.fetchone.return_value = None
    db.execute = AsyncMock(return_value=mock_result)

    with (
        patch.object(auth_mod, "_decode_token", AsyncMock(return_value=payload)),
        patch.object(auth_mod, "set_tenant", AsyncMock()),
    ):
        from fastapi.security import HTTPAuthorizationCredentials

        creds = MagicMock(spec=HTTPAuthorizationCredentials)
        creds.credentials = "fake-token"

        with pytest.raises(HTTPException) as exc_info:
            await auth_mod.get_current_user(credentials=creds, db=db)

    assert exc_info.value.status_code == 403
    assert "user_not_in_resourceowner_tenant" in str(exc_info.value.detail), (
        "Must return user_not_in_resourceowner_tenant detail, not a generic error"
    )


@pytest.mark.asyncio
async def test_get_current_user_raises_403_when_resourceowner_claim_missing():
    """JWT without resourceowner claim → 403 (no silent fall-back to LIMIT 1)."""
    from fastapi import HTTPException

    from app.core import auth as auth_mod

    # JWT payload WITHOUT the resourceowner claim
    payload = {
        "sub": USER_ID,
        ROLES_CLAIM: {},
        # No RESOURCEOWNER_CLAIM — this is the A-12 bug trigger
    }

    db = AsyncMock()
    db.add = MagicMock()

    with (
        patch.object(auth_mod, "_decode_token", AsyncMock(return_value=payload)),
        patch.object(auth_mod, "set_tenant", AsyncMock()),
    ):
        from fastapi.security import HTTPAuthorizationCredentials

        creds = MagicMock(spec=HTTPAuthorizationCredentials)
        creds.credentials = "fake-token"

        with pytest.raises(HTTPException) as exc_info:
            await auth_mod.get_current_user(credentials=creds, db=db)

    assert exc_info.value.status_code == 403
    assert "resourceowner" in str(exc_info.value.detail).lower(), (
        "Error must mention resourceowner so operators know what claim is missing"
    )


@pytest.mark.asyncio
async def test_multi_org_user_gets_correct_tenant_based_on_jwt():
    """User in org-A and org-B: JWT for org-B → resolves org-B, not org-A."""
    from app.core import auth as auth_mod

    # JWT says user authenticated via org-B
    payload = _make_payload(USER_ID, ORG_B_ZITADEL_ID)

    db = AsyncMock()
    db.add = MagicMock()
    # DB returns org-B row (the query filtered by resourceowner)
    mock_result = MagicMock()
    mock_result.fetchone.return_value = _make_db_row(ORG_B_PORTAL_ID, ORG_B_ZITADEL_ID)
    db.execute = AsyncMock(return_value=mock_result)

    resolved_tenant: list[str] = []

    async def _capture_set_tenant(session, tid):
        resolved_tenant.append(tid)

    with (
        patch.object(auth_mod, "_decode_token", AsyncMock(return_value=payload)),
        patch.object(auth_mod, "set_tenant", _capture_set_tenant),
    ):
        from fastapi.security import HTTPAuthorizationCredentials

        creds = MagicMock(spec=HTTPAuthorizationCredentials)
        creds.credentials = "fake-token"

        user = await auth_mod.get_current_user(credentials=creds, db=db)

    # Must resolve org-B, not org-A (the A-12 bug would have returned arbitrary)
    assert user.tenant_id == ORG_B_ZITADEL_ID
    assert resolved_tenant == [ORG_B_ZITADEL_ID]


def test_resourceowner_claim_constant():
    """The claim name constant must match Zitadel's actual claim name."""
    from app.core.auth import _RESOURCEOWNER_CLAIM

    assert _RESOURCEOWNER_CLAIM == "urn:zitadel:iam:org:project:resourceowner", (
        "Claim name must match Zitadel's documented resourceowner claim"
    )
