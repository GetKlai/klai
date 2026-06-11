"""API-level regressions for MCP OAuth endpoints."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.requests import Request

from app.api import mcp_oauth as api
from app.services.mcp_oauth import RegisteredClient


def _json_request(
    body: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    client: tuple[str, int] = ("127.0.0.1", 12345),
) -> Request:
    payload = json.dumps(body).encode()
    consumed = False

    async def receive() -> dict[str, Any]:
        nonlocal consumed
        if consumed:
            return {"type": "http.request", "body": b"", "more_body": False}
        consumed = True
        return {"type": "http.request", "body": payload, "more_body": False}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/oauth/register",
        "raw_path": b"/oauth/register",
        "query_string": b"",
        "scheme": "https",
        "server": ("testserver", 443),
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": client,
    }
    return Request(scope, receive)


class _CrossOrgSession:
    def __init__(self) -> None:
        self.db = MagicMock()
        self.db.commit = AsyncMock()

    async def __aenter__(self) -> MagicMock:
        return self.db

    async def __aexit__(self, *_exc: object) -> bool:
        return False


@pytest.mark.asyncio
async def test_register_client_uses_proxy_validated_client_host_for_dcr_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Left-most XFF is attacker-controlled and must not key DCR state."""
    captured: dict[str, str | None] = {}

    async def check_dcr_rate_limit(_redis: object, source_ip: str) -> bool:
        captured["rate_limit_ip"] = source_ip
        return True

    async def register_client(_db: object, **kwargs: object) -> RegisteredClient:
        captured["created_by_ip"] = kwargs["source_ip"]  # type: ignore[assignment]
        return RegisteredClient(
            client_id="client-test",
            client_name="Claude Desktop",
            redirect_uris=["http://localhost:54321/callback"],
            application_type="native",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
            scopes=["mcp:knowledge"],
        )

    async def log_event(**kwargs: object) -> None:
        captured["audit_actor"] = kwargs["actor"]  # type: ignore[assignment]

    monkeypatch.setattr(api, "get_redis_pool", AsyncMock(return_value=object()))
    monkeypatch.setattr(api, "cross_org_session", _CrossOrgSession)
    monkeypatch.setattr(api.svc, "check_dcr_rate_limit", check_dcr_rate_limit)
    monkeypatch.setattr(api.svc, "register_client", register_client)
    monkeypatch.setattr(api.audit, "log_event", log_event)

    request = _json_request(
        {
            "client_name": "Claude Desktop",
            "redirect_uris": ["http://localhost:54321/callback"],
        },
        headers={"x-forwarded-for": "198.51.100.200, 203.0.113.10"},
        client=("203.0.113.10", 48112),
    )

    response = await api.register_client(request)

    assert response.status_code == 201
    assert captured == {
        "rate_limit_ip": "203.0.113.10",
        "created_by_ip": "203.0.113.10",
        "audit_actor": "dcr:203.0.113.10",
    }
