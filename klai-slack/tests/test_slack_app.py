from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from slack_bolt.request.async_request import AsyncBoltRequest

from app import repo
from app.clients.portal import PortalClient, PortalUnavailable
from app.config import get_settings
from app.slack_app import build_oauth_settings, build_slack_app, dedupe_events, teardown_team

EVENT = {
    "type": "event_callback",
    "event_id": "Ev0001",
    "team_id": "T1",
    "event": {"type": "app_mention"},
}


@pytest.fixture
def seen_events(monkeypatch: pytest.MonkeyPatch, fake_db: object) -> set[str]:
    seen: set[str] = set()

    async def mark(_session: object, event_id: str) -> bool:
        if event_id in seen:
            return False
        seen.add(event_id)
        return True

    monkeypatch.setattr(repo, "mark_event_processed", mark)
    return seen


async def test_retry_with_same_event_id_is_acked_and_skipped(seen_events: set[str]) -> None:
    calls: list[int] = []

    async def next_() -> None:
        calls.append(1)

    first = AsyncBoltRequest(body="{}", headers={})
    retry = AsyncBoltRequest(body="{}", headers={"x-slack-retry-num": ["1"]})

    assert await dedupe_events(EVENT, first, next_) is None
    response = await dedupe_events(EVENT, retry, next_)

    assert calls == [1]
    assert response is not None
    assert response.status == 200


async def test_non_event_requests_pass_through(seen_events: set[str]) -> None:
    calls: list[int] = []

    async def next_() -> None:
        calls.append(1)

    command = {"command": "/klai", "text": "connect"}
    req = AsyncBoltRequest(body="{}", headers={})
    await dedupe_events(command, req, next_)
    await dedupe_events(command, req, next_)
    assert calls == [1, 1]
    assert seen_events == set()


class _Portal:
    def __init__(self, fail: bool = False) -> None:
        self.unlinked: list[str] = []
        self.fail = fail

    async def unlink(self, team_id: str) -> None:
        self.unlinked.append(team_id)
        if self.fail:
            raise PortalUnavailable("down")


@pytest.mark.parametrize("portal_fails", [False, True])
async def test_teardown_unlinks_and_purges(
    monkeypatch: pytest.MonkeyPatch, fake_db: object, portal_fails: bool
) -> None:
    purged: list[str] = []

    async def purge(_session: Any, team_id: str) -> None:
        purged.append(team_id)

    monkeypatch.setattr(repo, "purge_team", purge)
    portal = _Portal(fail=portal_fails)
    await teardown_team("T1", portal, "app_uninstalled")  # pyright: ignore[reportArgumentType]
    assert portal.unlinked == ["T1"]
    assert purged == ["T1"]


def test_oauth_settings_match_the_manifest_contract() -> None:
    """Spec §7.4: exactly these bot scopes, and the public install/redirect URLs."""
    store = SimpleNamespace()
    portal = PortalClient("http://p", "s")
    oauth_settings = build_oauth_settings(get_settings(), store, store, portal)  # pyright: ignore[reportArgumentType]
    app = build_slack_app(get_settings(), oauth_settings, portal)
    oauth = app.oauth_flow
    assert oauth is not None
    assert sorted(oauth.settings.scopes or []) == sorted(
        [
            "app_mentions:read",
            "chat:write",
            "im:history",
            "im:write",
            "channels:history",
            "groups:history",
            "commands",
            "users:read",
            "assistant:write",
        ]
    )
    assert oauth.settings.install_path == "/slack/install"
    assert oauth.settings.redirect_uri == "https://slack.example.com/slack/oauth_redirect"
