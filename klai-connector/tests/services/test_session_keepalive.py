"""Tests for SessionKeepAlive — see app/services/session_keepalive.py for why.

Follows tests/services/test_sync_run_reaper.py's mocking style: MagicMock
portal_client/crawl_sync_client with AsyncMock methods, tick() called
directly rather than starting the loop.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.portal_client import PortalConnectorConfig, ScheduledConnector
from app.services.session_keepalive import SessionKeepAlive


def _scheduled(
    *,
    connector_type: str = "web_crawler",
    has_saved_credentials: bool = True,
) -> ScheduledConnector:
    return ScheduledConnector(
        connector_id=uuid.uuid4(),
        org_id="org-a",
        schedule="0 3 * * *",
        connector_type=connector_type,
        has_saved_credentials=has_saved_credentials,
    )


def _config(config: dict[str, str]) -> PortalConnectorConfig:
    return PortalConnectorConfig(
        connector_id="conn-1",
        kb_id=1,
        kb_slug="support",
        zitadel_org_id="org-a",
        connector_type="web_crawler",
        config=config,
        schedule="0 3 * * *",
        is_enabled=True,
    )


def _make_keepalive(
    *,
    scheduled: list[ScheduledConnector],
    config_by_connector: dict[uuid.UUID, PortalConnectorConfig] | None = None,
    keepalive_side_effect: list[dict[str, bool] | Exception] | None = None,
) -> tuple[SessionKeepAlive, MagicMock, MagicMock]:
    portal = MagicMock()
    portal.list_scheduled_connectors = AsyncMock(return_value=scheduled)

    config_by_connector = config_by_connector or {}

    async def _get_config(connector_id: uuid.UUID) -> PortalConnectorConfig:
        return config_by_connector[connector_id]

    portal.get_connector_config = AsyncMock(side_effect=_get_config)

    crawl_client = MagicMock()
    if keepalive_side_effect is not None:
        crawl_client.crawl_keepalive = AsyncMock(side_effect=keepalive_side_effect)
    else:
        crawl_client.crawl_keepalive = AsyncMock(return_value={"ok": True})

    keepalive = SessionKeepAlive(portal_client=portal, crawl_sync_client=crawl_client)
    return keepalive, portal, crawl_client


class TestTickPingsEligibleConnectors:
    @pytest.mark.asyncio
    async def test_pings_web_crawler_with_saved_credentials_using_base_url(self) -> None:
        item = _scheduled()
        cfg = _config({"base_url": "https://help.voys.nl"})
        keepalive, _, crawl_client = _make_keepalive(
            scheduled=[item],
            config_by_connector={item.connector_id: cfg},
        )

        pinged = await keepalive.tick()

        assert pinged == 1
        crawl_client.crawl_keepalive.assert_awaited_once_with(
            connector_id=str(item.connector_id),
            org_id=item.org_id,
            url="https://help.voys.nl",
        )

    @pytest.mark.asyncio
    async def test_prefers_canary_url_over_base_url(self) -> None:
        item = _scheduled()
        cfg = _config({"base_url": "https://help.voys.nl", "canary_url": "https://help.voys.nl/index"})
        keepalive, _, crawl_client = _make_keepalive(
            scheduled=[item],
            config_by_connector={item.connector_id: cfg},
        )

        await keepalive.tick()

        crawl_client.crawl_keepalive.assert_awaited_once_with(
            connector_id=str(item.connector_id),
            org_id=item.org_id,
            url="https://help.voys.nl/index",
        )

    @pytest.mark.asyncio
    async def test_prefers_discovery_seed_url_over_base_url(self) -> None:
        """No canary_url yet (predates the field) -> the validated interior
        seed page beats the site root, which is exactly the URL that 302s to
        a language path in production (see session_keepalive.py docstring)."""
        item = _scheduled()
        cfg = _config(
            {
                "base_url": "https://wiki.example.com",
                "discovery_seed_url": "https://wiki.example.com/en/handbook",
            }
        )
        keepalive, _, crawl_client = _make_keepalive(
            scheduled=[item],
            config_by_connector={item.connector_id: cfg},
        )

        await keepalive.tick()

        crawl_client.crawl_keepalive.assert_awaited_once_with(
            connector_id=str(item.connector_id),
            org_id=item.org_id,
            url="https://wiki.example.com/en/handbook",
        )

    @pytest.mark.asyncio
    async def test_skips_web_crawler_without_saved_credentials(self) -> None:
        item = _scheduled(has_saved_credentials=False)
        keepalive, _, crawl_client = _make_keepalive(scheduled=[item])

        pinged = await keepalive.tick()

        assert pinged == 0
        crawl_client.crawl_keepalive.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_non_web_crawler_connector(self) -> None:
        item = _scheduled(connector_type="notion", has_saved_credentials=True)
        keepalive, _, crawl_client = _make_keepalive(scheduled=[item])

        pinged = await keepalive.tick()

        assert pinged == 0
        crawl_client.crawl_keepalive.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_logged_out_session_logs_error_with_connector_id_and_reason(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """ok=False must be loud (error-level) and actionable (connector id +
        reason), not the WARNING-and-nothing-else that let 5,648 ping_not_ok
        warnings accumulate silently in production."""
        item = _scheduled()
        cfg = _config({"base_url": "https://wiki.example.com"})
        keepalive, _, crawl_client = _make_keepalive(
            scheduled=[item],
            config_by_connector={item.connector_id: cfg},
            keepalive_side_effect=[{"ok": False, "reason": "redirect_to_login"}],
        )

        with caplog.at_level(logging.ERROR):
            pinged = await keepalive.tick()

        assert pinged == 1  # ping succeeded (no exception); session is just stale
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(error_records) == 1
        assert error_records[0].connector_id == str(item.connector_id)
        assert error_records[0].reason == "redirect_to_login"

    @pytest.mark.asyncio
    async def test_repeated_failure_logs_error_once_then_recovers(self, caplog: pytest.LogCaptureFixture) -> None:
        """A connector stuck logged-out must not re-alert every tick forever:
        one error on the failing transition, nothing while it stays failing,
        one info when it recovers."""
        item = _scheduled()
        cfg = _config({"base_url": "https://wiki.example.com"})
        keepalive, _, crawl_client = _make_keepalive(
            scheduled=[item],
            config_by_connector={item.connector_id: cfg},
            keepalive_side_effect=[
                {"ok": False, "reason": "redirect_to_login"},
                {"ok": False, "reason": "redirect_to_login"},
                {"ok": True, "reason": None},
            ],
        )

        with caplog.at_level(logging.INFO):
            await keepalive.tick()  # 1st failure -> error
            await keepalive.tick()  # still failing -> no repeat error
            await keepalive.tick()  # recovered -> info

        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(error_records) == 1
        assert any(r.getMessage() == "session_keepalive_recovered" for r in caplog.records)

    @pytest.mark.asyncio
    async def test_one_bad_ping_does_not_abort_the_batch(self) -> None:
        good, bad = _scheduled(), _scheduled()
        cfg = _config({"base_url": "https://help.voys.nl"})
        keepalive, _, crawl_client = _make_keepalive(
            scheduled=[bad, good],
            config_by_connector={bad.connector_id: cfg, good.connector_id: cfg},
            keepalive_side_effect=[Exception("boom"), {"ok": True}],
        )

        pinged = await keepalive.tick()

        assert pinged == 1
        assert crawl_client.crawl_keepalive.await_count == 2


class TestAsyncRunSurvivesTickFailure:
    @pytest.mark.asyncio
    async def test_outer_loop_survives_exception_in_tick(self, monkeypatch: pytest.MonkeyPatch) -> None:
        keepalive, portal, _ = _make_keepalive(scheduled=[])
        portal.list_scheduled_connectors = AsyncMock(side_effect=Exception("boom"))

        tick_spy = AsyncMock(wraps=keepalive.tick)
        keepalive.tick = tick_spy  # type: ignore[method-assign]

        sleep_calls = {"n": 0}

        async def _fake_sleep(_seconds: float) -> None:
            sleep_calls["n"] += 1
            raise asyncio.CancelledError

        monkeypatch.setattr("app.services.session_keepalive.asyncio.sleep", _fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await keepalive.async_run()

        tick_spy.assert_awaited_once()
        assert sleep_calls["n"] == 1
