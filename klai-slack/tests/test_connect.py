from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from slack_sdk.errors import SlackApiError
from structlog.testing import capture_logs

from app import repo
from app.clients.portal import (
    LinkConflict,
    LinkForbidden,
    LinkKeyInvalid,
    LinkResult,
    PortalUnavailable,
)
from app.commands import ack_klai, connect
from app.commands.context import CommandContext

KEY = "pk_live_synthetic_key_0001"
COMMAND = {"team_id": "T1", "team_domain": "voorbeeld", "user_id": "U1", "text": f"connect {KEY}"}


class FakeSlack:
    def __init__(self, user: dict[str, Any] | None = None, error: bool = False) -> None:
        self.user = user if user is not None else {"is_admin": True}
        self.error = error

    async def users_info(self, user: str) -> dict[str, Any]:
        if self.error:
            raise SlackApiError("boom", {"ok": False, "error": "ratelimited"})
        return {"ok": True, "user": {"id": user, **self.user}}


class FakePortal:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.links: list[tuple[str, str, str, str]] = []
        self.unlinked: list[str] = []

    async def link(
        self, api_key: str, team_id: str, team_name: str, slack_user_id: str
    ) -> LinkResult:
        self.links.append((api_key, team_id, team_name, slack_user_id))
        if self.error:
            raise self.error
        return LinkResult(
            org_id=7,
            org_name="Voorbeeld BV",
            key_id="4f2c9a1e-0000-4000-8000-000000000042",
            kb_ids=[1, 2],
        )

    async def unlink(self, team_id: str) -> None:
        self.unlinked.append(team_id)


class Store:
    def __init__(self) -> None:
        self.link: Any = None
        self.saved: list[dict[str, Any]] = []
        self.save_error = False


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch, fake_db: object) -> Store:
    state = Store()

    async def get_link(_s: object, _team_id: str) -> Any:
        return state.link

    async def get_team_name(_s: object, _team_id: str) -> str:
        return "Voorbeeld"

    async def save_link(_s: object, **kwargs: Any) -> None:
        if state.save_error:
            raise RuntimeError("db down")
        state.saved.append(kwargs)

    monkeypatch.setattr(repo, "get_link", get_link)
    monkeypatch.setattr(repo, "get_team_name", get_team_name)
    monkeypatch.setattr(repo, "save_link", save_link)
    return state


async def _run(
    args: str = KEY, slack: FakeSlack | None = None, portal: FakePortal | None = None
) -> tuple[list[str], FakePortal]:
    said: list[str] = []
    portal = portal or FakePortal()

    async def respond(text: str, response_type: str) -> None:
        assert response_type == "ephemeral"
        said.append(text)

    ctx = CommandContext(
        args=args,
        command=COMMAND,
        client=slack or FakeSlack(),  # pyright: ignore[reportArgumentType]
        respond=respond,  # pyright: ignore[reportArgumentType]
        portal=portal,  # pyright: ignore[reportArgumentType]
    )
    with capture_logs() as logs:
        await connect.run(ctx)
    assert all(KEY not in str(entry) for entry in logs), "the Klai key must never be logged"
    assert all(KEY not in text for text in said), "the Klai key must never be echoed"
    return said, portal


async def test_happy_path_links_and_stores_key(store: Store) -> None:
    said, portal = await _run()
    assert portal.links == [(KEY, "T1", "Voorbeeld", "U1")]
    assert store.saved == [
        {
            "team_id": "T1",
            "org_id": 7,
            "org_name": "Voorbeeld BV",
            "api_key": KEY,
            "key_id": "4f2c9a1e-0000-4000-8000-000000000042",
            "linked_by_slack_user_id": "U1",
        }
    ]
    assert said == [connect.LINKED.format(org="Voorbeeld BV", count=2, kb_word="kennisbanken")]


@pytest.mark.parametrize("args", ["", "sk_live_x", f"{KEY} extra"])
async def test_malformed_key_shows_usage(store: Store, args: str) -> None:
    said, portal = await _run(args=args)
    assert said == [connect.USAGE]
    assert portal.links == []


async def test_non_admin_is_refused(store: Store) -> None:
    said, portal = await _run(slack=FakeSlack(user={"is_admin": False, "is_owner": False}))
    assert said == [connect.NOT_ADMIN]
    assert portal.links == []


async def test_owner_counts_as_admin(store: Store) -> None:
    await _run(slack=FakeSlack(user={"is_admin": False, "is_owner": True}))
    assert store.saved


async def test_users_info_failure(store: Store) -> None:
    said, portal = await _run(slack=FakeSlack(error=True))
    assert said == [connect.SLACK_ERROR]
    assert portal.links == []


async def test_already_linked_team_is_refused_locally(store: Store) -> None:
    store.link = SimpleNamespace(org_name="Bestaand BV", revoked_at=None)
    said, portal = await _run()
    assert said == [connect.ALREADY_LINKED.format(org="Bestaand BV")]
    assert portal.links == []


async def test_revoked_link_can_be_relinked(store: Store) -> None:
    store.link = SimpleNamespace(org_name="Oud BV", revoked_at=datetime.now(UTC))
    await _run()
    assert len(store.saved) == 1


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (LinkKeyInvalid(), connect.KEY_INVALID),
        (LinkForbidden("feature_not_unlocked"), connect.FEATURE_LOCKED),
        (LinkConflict("org_already_linked"), connect.ORG_ALREADY_LINKED),
        (LinkConflict("team_already_linked"), connect.TEAM_ALREADY_LINKED),
        (PortalUnavailable("down"), connect.PORTAL_UNAVAILABLE),
    ],
)
async def test_portal_errors_map_to_messages_and_store_nothing(
    store: Store, error: Exception, message: str
) -> None:
    said, _ = await _run(portal=FakePortal(error=error))
    assert said == [message]
    assert store.saved == []


async def test_store_failure_releases_the_portal_link(store: Store) -> None:
    store.save_error = True
    said, portal = await _run()
    assert said == [connect.STORE_FAILED]
    assert portal.unlinked == ["T1"]


@pytest.mark.parametrize(
    ("text", "expected"),
    # kb opens a modal and help (also for unknown or missing subcommands) answers
    # ephemerally from the lazy listener, so their ack is empty.
    [(f"connect {KEY}", connect.ACK_TEXT), ("kb", None), ("onbekend", None), ("", None)],
)
async def test_ack_text(text: str, expected: str | None) -> None:
    acked: list[str | None] = []

    async def ack(text: str | None = None) -> None:
        acked.append(text)

    await ack_klai(ack, {"text": text})  # pyright: ignore[reportArgumentType]
    assert acked == [expected]
