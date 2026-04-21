"""Tests for /api/app/chat-health pre-flight probe.

Locks in the behaviour that LibreChat v0.8.5 requires (see app/api/app_chat.py):
the probe hits only public endpoints (/health and /api/config). Any regression
back to an auth-gated endpoint like /api/endpoints would break chat for every
tenant, so we pin the exact URL sequence + reason codes here.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest


def _mock_org(
    container: str | None = "librechat-getklai",
    status: str = "ready",
    org_id: int = 1,
) -> MagicMock:
    org = MagicMock()
    org.librechat_container = container
    org.provisioning_status = status
    org.id = org_id
    return org


def _mock_httpx_client(responses: dict[str, MagicMock | Exception]):
    """Return an httpx.AsyncClient class replacement whose get() routes by URL suffix.

    Each key in `responses` is matched as a URL suffix against the requested URL;
    the first match wins. A value that is an Exception is raised instead of returned.
    """
    client = MagicMock()

    async def _get(url, **_):
        for suffix, value in responses.items():
            if url.endswith(suffix):
                if isinstance(value, Exception):
                    raise value
                return value
        raise AssertionError(f"unexpected GET {url} — no mock for it")

    client.get = AsyncMock(side_effect=_get)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=client)


def _response(status_code: int) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    return resp


class TestChatHealth:
    @pytest.mark.asyncio
    async def test_not_provisioned_when_no_container(self, monkeypatch):
        from app.api import app_chat

        org = _mock_org(container=None)
        monkeypatch.setattr(app_chat, "_get_caller_org", AsyncMock(return_value=("sub", org, None)))

        out = await app_chat.get_chat_health(credentials=MagicMock(), db=MagicMock())

        assert out.healthy is False
        assert out.reason == "not_provisioned"

    @pytest.mark.asyncio
    async def test_provisioning_in_progress_when_status_not_ready(self, monkeypatch):
        from app.api import app_chat

        org = _mock_org(status="provisioning")
        monkeypatch.setattr(app_chat, "_get_caller_org", AsyncMock(return_value=("sub", org, None)))

        out = await app_chat.get_chat_health(credentials=MagicMock(), db=MagicMock())

        assert out.healthy is False
        assert out.reason == "provisioning_in_progress"

    @pytest.mark.asyncio
    async def test_healthy_when_health_and_config_both_200(self, monkeypatch):
        from app.api import app_chat

        org = _mock_org()
        monkeypatch.setattr(app_chat, "_get_caller_org", AsyncMock(return_value=("sub", org, None)))
        monkeypatch.setattr(
            "app.api.app_chat.httpx.AsyncClient",
            _mock_httpx_client({"/health": _response(200), "/api/config": _response(200)}),
        )

        out = await app_chat.get_chat_health(credentials=MagicMock(), db=MagicMock())

        assert out.healthy is True
        assert out.reason is None

    @pytest.mark.asyncio
    async def test_never_probes_api_endpoints(self, monkeypatch):
        """Regression guard: /api/endpoints requires auth in LibreChat v0.8.5+
        and MUST NOT be part of the probe. Any GET to that path should never
        be issued.
        """
        from app.api import app_chat

        seen: list[str] = []

        async def _record(url, **_):
            seen.append(url)
            return _response(200)

        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=_record)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        org = _mock_org()
        monkeypatch.setattr(app_chat, "_get_caller_org", AsyncMock(return_value=("sub", org, None)))
        monkeypatch.setattr("app.api.app_chat.httpx.AsyncClient", MagicMock(return_value=mock_client))

        await app_chat.get_chat_health(credentials=MagicMock(), db=MagicMock())

        assert not any(url.endswith("/api/endpoints") for url in seen), seen

    @pytest.mark.asyncio
    async def test_container_unhealthy_when_health_returns_non_200(self, monkeypatch):
        from app.api import app_chat

        org = _mock_org()
        monkeypatch.setattr(app_chat, "_get_caller_org", AsyncMock(return_value=("sub", org, None)))
        monkeypatch.setattr(app_chat, "_emit_failure", MagicMock())
        monkeypatch.setattr(
            "app.api.app_chat.httpx.AsyncClient",
            _mock_httpx_client({"/health": _response(503)}),
        )

        out = await app_chat.get_chat_health(credentials=MagicMock(), db=MagicMock())

        assert out.healthy is False
        assert out.reason == "container_unhealthy"

    @pytest.mark.asyncio
    async def test_app_not_ready_when_config_returns_non_200(self, monkeypatch):
        from app.api import app_chat

        org = _mock_org()
        monkeypatch.setattr(app_chat, "_get_caller_org", AsyncMock(return_value=("sub", org, None)))
        monkeypatch.setattr(app_chat, "_emit_failure", MagicMock())
        monkeypatch.setattr(
            "app.api.app_chat.httpx.AsyncClient",
            _mock_httpx_client({"/health": _response(200), "/api/config": _response(500)}),
        )

        out = await app_chat.get_chat_health(credentials=MagicMock(), db=MagicMock())

        assert out.healthy is False
        assert out.reason == "app_not_ready"

    @pytest.mark.asyncio
    async def test_timeout_mapped_to_timeout_reason(self, monkeypatch):
        from app.api import app_chat

        org = _mock_org()
        monkeypatch.setattr(app_chat, "_get_caller_org", AsyncMock(return_value=("sub", org, None)))
        monkeypatch.setattr(app_chat, "_emit_failure", MagicMock())
        monkeypatch.setattr(
            "app.api.app_chat.httpx.AsyncClient",
            _mock_httpx_client({"/health": httpx.TimeoutException("slow")}),
        )

        out = await app_chat.get_chat_health(credentials=MagicMock(), db=MagicMock())

        assert out.healthy is False
        assert out.reason == "timeout"

    @pytest.mark.asyncio
    async def test_connect_error_mapped_to_container_unreachable(self, monkeypatch):
        from app.api import app_chat

        org = _mock_org()
        monkeypatch.setattr(app_chat, "_get_caller_org", AsyncMock(return_value=("sub", org, None)))
        monkeypatch.setattr(app_chat, "_emit_failure", MagicMock())
        monkeypatch.setattr(
            "app.api.app_chat.httpx.AsyncClient",
            _mock_httpx_client({"/health": httpx.ConnectError("refused")}),
        )

        out = await app_chat.get_chat_health(credentials=MagicMock(), db=MagicMock())

        assert out.healthy is False
        assert out.reason == "container_unreachable"
