from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.partner import (
    StartHubSpotHandoffRequest,
    start_widget_hubspot_handoff,
    stream_widget_hubspot_handoff_events,
)
from app.api.partner_dependencies import PartnerAuthContext
from app.services.widget_handoff import build_handoff_context_text, record_hubspot_agent_reply


class _Result:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


@dataclass
class _FakeWidget:
    id: str = "widget-uuid-1"
    org_id: int = 42
    widget_id: str = "wgt_abcdef1234567890abcdef1234567890abcdef12"
    widget_config: dict = field(
        default_factory=lambda: {
            "integrations": {
                "hubspot": {
                    "status": "connected",
                    "channel_account_id": "3307400689",
                }
            }
        }
    )


@dataclass
class _FakeOrg:
    id: int = 42
    slug: str = "getklai"


def _widget_auth() -> PartnerAuthContext:
    return PartnerAuthContext(
        key_id="wgt_abcdef1234567890abcdef1234567890abcdef12",
        org_id=42,
        zitadel_org_id="zitadel-org-123",
        permissions={"chat": True},
        kb_access={1: "read"},
        rate_limit_rpm=60,
        session_key="session-key-1",
    )


def _widget_request(origin: str = "https://getklai.getklai.com"):
    request = AsyncMock()
    request.headers = {"origin": origin}
    return request


class _SessionLocal:
    def __init__(self, order: list[str], db: AsyncMock):
        self.order = order
        self.db = db

    async def __aenter__(self):
        self.order.append("db_enter")
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        self.order.append("db_exit")


class _FakeRedis:
    def __init__(self, order: list[str]):
        self.order = order

    def pubsub(self):
        return _FakePubSub(self.order)


class _FakePubSub:
    def __init__(self, order: list[str]):
        self.order = order

    async def subscribe(self, channel: str) -> None:
        self.order.append("subscribe")

    async def get_message(self, *, ignore_subscribe_messages: bool, timeout: int):
        self.order.append("get_message")
        return None

    async def unsubscribe(self, channel: str | None) -> None:
        self.order.append("unsubscribe")

    async def aclose(self) -> None:
        self.order.append("close_pubsub")


@pytest.mark.asyncio
async def test_start_handoff_rejects_non_getklai_tenant() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result((_FakeWidget(), _FakeOrg(slug="voys"))))

    with patch("app.api.partner.start_hubspot_handoff", new_callable=AsyncMock) as start_handoff:
        with pytest.raises(HTTPException) as exc_info:
            await start_widget_hubspot_handoff(
                http_request=_widget_request(),
                request=StartHubSpotHandoffRequest(summary="Help nodig"),
                auth=_widget_auth(),
                db=db,
            )

    assert exc_info.value.status_code == 403
    start_handoff.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_handoff_rejects_widget_without_hubspot_integration() -> None:
    db = AsyncMock()
    widget = _FakeWidget(widget_config={"integrations": {"hubspot": {"status": "disconnected"}}})
    db.execute = AsyncMock(return_value=_Result((widget, _FakeOrg(slug="getklai"))))

    with patch("app.api.partner.start_hubspot_handoff", new_callable=AsyncMock) as start_handoff:
        with pytest.raises(HTTPException) as exc_info:
            await start_widget_hubspot_handoff(
                http_request=_widget_request(),
                request=StartHubSpotHandoffRequest(summary="Help nodig"),
                auth=_widget_auth(),
                db=db,
            )

    assert exc_info.value.status_code == 403
    start_handoff.assert_not_awaited()


@pytest.mark.asyncio
async def test_handoff_events_subscribes_before_replay_and_closes_db_before_stream() -> None:
    order: list[str] = []
    db = AsyncMock()

    async def fake_get_partner_key(request, db):
        order.append("auth")
        return _widget_auth()

    async def fake_require_enabled(*, db, auth, request) -> None:
        order.append("gate")

    async def fake_get_active_handoff_session_id(*args, **kwargs) -> int:
        order.append("active")
        return 123

    async def fake_get_redis_pool():
        order.append("redis")
        return _FakeRedis(order)

    async def fake_list_visible_handoff_messages(*args, **kwargs):
        order.append("replay")
        return [{"id": 4, "content": "Hallo", "direction": "agent"}]

    with (
        patch("app.api.partner.AsyncSessionLocal", return_value=_SessionLocal(order, db)),
        patch("app.api.partner.get_partner_key", side_effect=fake_get_partner_key),
        patch("app.api.partner._require_hubspot_widget_handoff_enabled", side_effect=fake_require_enabled),
        patch("app.api.partner.get_active_handoff_session_id", side_effect=fake_get_active_handoff_session_id),
        patch("app.api.partner.get_redis_pool", side_effect=fake_get_redis_pool),
        patch("app.api.partner.list_visible_handoff_messages", side_effect=fake_list_visible_handoff_messages),
    ):
        response = await stream_widget_hubspot_handoff_events(request=_widget_request(), last_event_id=3)

        assert response.status_code == 200
        assert order == ["db_enter", "auth", "gate", "active", "db_exit"]

        iterator = response.body_iterator
        chunk = await anext(iterator)
        assert b"id: 4" in chunk
        if hasattr(iterator, "aclose"):
            await iterator.aclose()

    assert order == [
        "db_enter",
        "auth",
        "gate",
        "active",
        "db_exit",
        "redis",
        "subscribe",
        "db_enter",
        "replay",
        "db_exit",
        "unsubscribe",
        "close_pubsub",
    ]


@pytest.mark.asyncio
async def test_start_handoff_rejects_wrong_origin() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result((_FakeWidget(), _FakeOrg(slug="getklai"))))

    with patch("app.api.partner.start_hubspot_handoff", new_callable=AsyncMock) as start_handoff:
        with pytest.raises(HTTPException) as exc_info:
            await start_widget_hubspot_handoff(
                http_request=_widget_request("https://voys.getklai.com"),
                request=StartHubSpotHandoffRequest(summary="Help nodig"),
                auth=_widget_auth(),
                db=db,
            )

    assert exc_info.value.status_code == 403
    start_handoff.assert_not_awaited()


def test_build_handoff_context_text_includes_summary_and_recent_transcript() -> None:
    text = build_handoff_context_text(
        summary="Klant wil realtime hulp.",
        visitor_name="Mark Vletter",
        visitor_email="mark@example.com",
        messages=[
            {"role": "user", "content": "Hoi"},
            {"role": "assistant", "content": "Waarmee kan ik helpen?"},
        ],
    )

    assert "Nieuwe live support overdracht" in text
    assert "Samenvatting:" in text
    assert "Klant wil realtime hulp." in text
    assert "Bezoeker:" in text
    assert "Naam: Mark Vletter" in text
    assert "E-mail: mark@example.com" in text
    assert "Bezoeker: Hoi" in text
    assert "Klai: Waarmee kan ik helpen?" in text


def _tracking_cross_org_scope(events: list[str]):
    """Stand-in for `app.core.database.cross_org_scope`.

    The real helper flips `session.info["cross_org_admin"]` and re-applies the
    transaction-local context. It runs on the CALLER'S session — no second
    pooled connection. This fake records enter/exit so a test can assert the
    bypass is live during the lookup and cleared afterwards, and it asserts it
    was handed the very session the handler was called with.
    """

    @asynccontextmanager
    async def _cm(session):
        session.info["cross_org_admin"] = True
        events.append("scope_enter")
        try:
            yield session
        finally:
            session.info["cross_org_admin"] = False
            events.append("scope_exit")

    return _cm


def _explode_if_second_session():
    """`cross_org_session()` must NOT be reachable from the webhook path.

    Opening it there would hold a second pooled connection for the duration of
    every webhook request — a HubSpot burst then exhausts the pool.
    """

    @asynccontextmanager
    async def _cm():
        raise AssertionError("record_hubspot_agent_reply must not open a second pooled session")
        yield  # pragma: no cover — unreachable, keeps this a generator

    return _cm


@pytest.mark.asyncio
async def test_record_hubspot_agent_reply_records_and_publishes() -> None:
    events: list[str] = []
    db = AsyncMock()
    db.info = {}  # set_tenant / cross_org_scope record their state here

    async def _execute(stmt, params=None):
        sql = str(stmt)
        if "widget_handoff_sessions" in sql:
            # The bypass must be LIVE while the globally-unique thread id is
            # mapped to a tenant — that row belongs to another org's scope.
            events.append(f"lookup:cross_org={db.info.get('cross_org_admin')}")
            return _Result((42, 7))
        if "INSERT INTO widget_handoff_messages" in sql:
            events.append(f"insert:cross_org={db.info.get('cross_org_admin')}")
            return _Result((99, "2026-05-27T13:05:31Z"))
        events.append("set_config")
        return _Result(None)

    db.execute = AsyncMock(side_effect=_execute)
    db.commit = AsyncMock()
    payload = {
        "message": {
            "id": "hubspot-msg-1",
            "conversationsThreadId": "19615248623",
            "text": "Hier een reactie",
            "sender": {"name": "Mark Vletter"},
        }
    }

    with (
        patch("app.services.widget_handoff.get_redis_pool", AsyncMock(return_value=None)),
        patch("app.services.widget_handoff.cross_org_scope", _tracking_cross_org_scope(events)),
        patch("app.core.database.cross_org_session", _explode_if_second_session()),
    ):
        result = await record_hubspot_agent_reply(db, payload)

    assert result["status"] == "recorded"
    assert result["handoff_session_id"] == 42
    assert result["id"] == 99
    # Everything runs on the ONE session the handler was given.
    assert "lookup:cross_org=True" in events, events
    assert "insert:cross_org=False" in events, events
    assert events.index("scope_exit") < events.index("insert:cross_org=False"), events
    assert db.info["cross_org_admin"] is False, "bypass must not outlive the lookup"
    insert_call = next(c for c in db.execute.await_args_list if "INSERT INTO" in str(c.args[0]))
    assert insert_call.args[1]["agent_name"] == "Mark Vletter"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_hubspot_agent_reply_ignores_unmapped_thread() -> None:
    db = AsyncMock()
    events: list[str] = []
    db.info = {}
    db.execute = AsyncMock(return_value=_Result(None))
    db.commit = AsyncMock()
    payload = {
        "message": {
            "id": "hubspot-msg-1",
            "conversationsThreadId": "19615248623",
            "text": "Hier een reactie",
        }
    }

    with (
        patch("app.services.widget_handoff.cross_org_scope", _tracking_cross_org_scope(events)),
        patch("app.core.database.cross_org_session", _explode_if_second_session()),
    ):
        result = await record_hubspot_agent_reply(db, payload)

    assert result == {"status": "ignored", "reason": "unmapped_thread"}
    # Exactly one statement (the lookup), on the request session, inside the scope.
    assert db.execute.await_count == 1
    assert "widget_handoff_sessions" in str(db.execute.await_args_list[0].args[0])
    assert events == ["scope_enter", "scope_exit"], events
    assert db.info["cross_org_admin"] is False
    db.commit.assert_not_awaited()
