"""Regression: /internal/mcp-token/verify must not overflow portal_audit_log.org_id.

The success path used to cast the 18-digit Zitadel org id (``result.org_id``) into
``int`` and pass it to the int4 ``portal_audit_log.org_id`` column. Values like
368884765035593759 exceed int32 (max 2_147_483_647), so the fire-and-forget audit
INSERT raised OverflowError -> ``internal_audit_write_failed`` on every MCP-using
tenant (non-fatal, but a DB audit-trail gap + error-log noise). The success path
must audit with 0 (no internal tenant resolved) like the deny paths; the real
Zitadel org id stays in the ``mcp_token_verify_decision`` structlog event.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_mcp_verify_success_audits_org_id_zero_not_zitadel_id(monkeypatch):
    from app.api import internal as internal_mod
    from app.services.mcp_oauth import VerifyResult

    big_zitadel_org = "368884765035593759"  # 18 digits, > int32 max (2_147_483_647)

    monkeypatch.setattr(internal_mod, "_require_internal_token", AsyncMock())
    monkeypatch.setattr(internal_mod, "get_redis_pool", AsyncMock(return_value=object()))

    @asynccontextmanager
    async def _fake_cross_org_session():
        yield AsyncMock()

    monkeypatch.setattr("app.core.database.cross_org_session", _fake_cross_org_session)
    monkeypatch.setattr(
        "app.services.mcp_oauth.verify_access_token",
        AsyncMock(
            return_value=VerifyResult(
                verified=True,
                user_id="zitadel-user-1",
                org_id=big_zitadel_org,
                org_slug="acme",
                scopes=("klai:internal:retrieval:query",),
                resource_uri="https://mcp.getklai.com",
            )
        ),
    )

    captured: dict[str, int] = {}

    async def _spy_audit(request, org_id):
        captured["org_id"] = org_id

    monkeypatch.setattr(internal_mod, "_audit_internal_call", _spy_audit)

    body = internal_mod.McpTokenVerifyRequest(caller_service="knowledge-mcp", raw_token="klai_mcp_test")
    resp = await internal_mod.verify_mcp_token(MagicMock(), body, db=AsyncMock())

    assert resp.status_code == 200
    # int(big_zitadel_org) overflows the int4 audit column. The success path must
    # audit with 0, exactly like the deny paths in the same handler.
    assert captured["org_id"] == 0
    assert captured["org_id"] != int(big_zitadel_org)
