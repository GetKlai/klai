"""Tests for SPEC-TI-010C B-6: server-side org resolution in get_knowledge_feature.

Before the fix, the endpoint trusted the caller-supplied org_id query param
to call set_tenant(). An attacker could supply any org_id to read another tenant's
feature entitlements (cross-tenant pivot).

After the fix:
  1. org_id param is Optional and IGNORED for tenant resolution.
  2. Tenant is resolved server-side via three-step lookup.
  3. set_tenant() is always called with the server-resolved org_id."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("POSTGRES_DSN", "postgresql+asyncpg://test/test")
os.environ.setdefault("ZITADEL_JWKS_URL", "https://zitadel.test/.well-known/jwks.json")
os.environ.setdefault("ZITADEL_ISSUER", "https://zitadel.test")
os.environ.setdefault("ZITADEL_PROJECT_ID", "test-project")
os.environ.setdefault("INTERNAL_SECRET", "portal-internal-secret-test")
os.environ.setdefault("MONEYBIRD_WEBHOOK_TOKEN", "test-moneybird-webhook-token")


def _make_mock_request(ip="10.0.0.1"):
    mock_req = MagicMock()
    mock_req.headers = {"authorization": "Bearer internal-secret"}
    mock_req.client = MagicMock()
    mock_req.client.host = ip
    return mock_req


def _make_portal_user(org_id=5, role="member", librechat_user_id=None):
    user = MagicMock()
    user.org_id = org_id
    user.role = role
    user.zitadel_user_id = "zitadel-user-xyz"
    user.librechat_user_id = librechat_user_id
    user.kb_retrieval_enabled = True
    user.kb_personal_enabled = False
    user.kb_slugs_filter = None
    user.kb_narrow = False
    user.kb_pref_version = 0
    return user


def _make_mock_db(user_from_librechat_id=None, user_from_zitadel_id=None):
    """Build an AsyncMock db that returns the given users for execute() calls."""
    result_librechat = MagicMock()
    result_librechat.scalar_one_or_none.return_value = user_from_librechat_id
    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.execute = AsyncMock(return_value=result_librechat)
    mock_db.commit = AsyncMock()
    return mock_db


def test_org_id_param_is_optional():
    """org_id must be Optional[str] in the function signature (SPEC-TI-010C B-6)."""
    import inspect
    from app.api.internal import get_knowledge_feature
    sig = inspect.signature(get_knowledge_feature)
    param = sig.parameters.get("org_id")
    assert param is not None, "org_id parameter must exist for backward-compat"
    assert param.default is None, (
        "SPEC-TI-010C B-6: org_id must be Optional with default=None -- "
        "required org_id trusted caller-supplied value for tenant resolution"
    )


@pytest.mark.asyncio
async def test_fast_path_uses_server_resolved_org_id():
    """Fast path: cached librechat_user_id in portal_users uses user.org_id for set_tenant()."""
    from app.api.internal import get_knowledge_feature

    portal_user = _make_portal_user(org_id=9)
    mock_db = _make_mock_db(user_from_librechat_id=portal_user)
    mock_request = _make_mock_request()

    set_tenant_calls = []

    async def capture_set_tenant(db, org_id):
        set_tenant_calls.append(org_id)

    with (
        patch("app.api.internal._require_internal_token", new_callable=AsyncMock),
        patch("app.api.internal.set_tenant", side_effect=capture_set_tenant),
        patch("app.api.internal._audit_internal_call", new_callable=AsyncMock),
        patch("app.api.internal.get_effective_products", new_callable=AsyncMock, return_value={"knowledge"}),
    ):
        resp = await get_knowledge_feature(
            librechat_user_id="mongo-oid-abc",
            request=mock_request,
            db=mock_db,
            org_id="some-other-org-should-be-ignored",  # caller-supplied, must be ignored
        )

    assert resp.enabled is True
    assert len(set_tenant_calls) == 1
    assert set_tenant_calls[0] == 9, (
        "SPEC-TI-010C B-6: set_tenant must use server-resolved org_id=9, "
        "not the caller-supplied value"
    )


@pytest.mark.asyncio
async def test_caller_supplied_org_id_ignored_when_user_found():
    """
    Even if the caller passes a completely wrong org_id, the fast path
    must use the server-resolved org_id from portal_users.org_id.
    """
    from app.api.internal import get_knowledge_feature

    portal_user = _make_portal_user(org_id=7)
    mock_db = _make_mock_db(user_from_librechat_id=portal_user)
    mock_request = _make_mock_request()

    set_tenant_calls = []

    async def capture_set_tenant(db, org_id):
        set_tenant_calls.append(org_id)

    with (
        patch("app.api.internal._require_internal_token", new_callable=AsyncMock),
        patch("app.api.internal.set_tenant", side_effect=capture_set_tenant),
        patch("app.api.internal._audit_internal_call", new_callable=AsyncMock),
        patch("app.api.internal.get_effective_products", new_callable=AsyncMock, return_value=set()),
    ):
        resp = await get_knowledge_feature(
            librechat_user_id="mongo-oid-abc",
            request=mock_request,
            db=mock_db,
            org_id="999",  # attacker-supplied wrong org_id
        )

    # set_tenant must be called with the real server-resolved org_id=7
    assert set_tenant_calls == [7], (
        "SPEC-TI-010C B-6: caller-supplied org_id=999 must be ignored -- "
        "set_tenant must use server-resolved org_id=7 from portal_users"
    )
    assert resp.enabled is False  # no knowledge product


def test_server_side_resolution_in_source_code():
    """Static check: the source must contain portal_users_librechat_index reference."""
    import inspect
    from app.api.internal import get_knowledge_feature
    source = inspect.getsource(get_knowledge_feature)
    assert "portal_users_librechat_index" in source, (
        "SPEC-TI-010C B-6: slow-path-A (portal_users_librechat_index) must be present"
    )
    assert "set_tenant(db, user.org_id)" in source or "set_tenant(db, resolved_org_id)" in source, (
        "SPEC-TI-010C B-6: set_tenant must be called with server-resolved org_id"
    )
