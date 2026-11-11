"""A widget's contact routes: what the admin may store, and which route a turn at a human names."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from pydantic import ValidationError

from app.api.admin_widgets import WidgetConfig
from app.services import contact_routes
from app.services.contact_routes import ContactRoute, pick_route

LITELLM = "http://litellm.test"


def _route(**values) -> dict:
    return {"name": "Support", **values}


def test_a_route_without_an_id_gets_one_and_blank_channels_are_unset():
    route = ContactRoute.model_validate(_route(id=None, phone="", email=" ", is_default=True))

    assert route.id
    assert (route.phone, route.email) == (None, None)


@pytest.mark.parametrize(
    "values",
    [
        {"name": "[Bel ons](https://evil.example.com)"},
        {"name": "Mail info@example.com"},
        {"note": "Kijk op www.example.com"},
        {"note": "Zie https://example.com"},
        {"note": "Eerste regel\nTweede regel"},
        {"link_url": "https://example.com/a", "link_label": "**Klik**"},
        {"link_url": "https://example.com/a"},
        {"link_label": "Alleen een label"},
        {"booking_url": "javascript:alert(1)"},
        {"booking_url": "/relatief"},
        {"link_url": "https://example.com/a)[x](https://evil.example.com", "link_label": "Lees meer"},
        {"phone": "[Bel](https://evil.example.com)"},
        {"phone": "12345"},
        {"email": "geen adres"},
        {"name": ""},
        {"name": "x" * 61},
        {"note": "x" * 201},
        {"link_url": "https://example.com/a", "link_label": "x" * 41},
    ],
)
def test_a_route_value_that_could_inject_markdown_or_a_link_is_refused(values):
    with pytest.raises(ValidationError):
        ContactRoute.model_validate({**_route(), **values})


def test_a_route_with_every_channel_is_accepted():
    route = ContactRoute.model_validate(
        _route(
            phone="+31 (0)10 000 0000",
            email="hulp@example.com",
            booking_url="https://book.example.com/a?t=1",
            link_url="https://help.example.com/vraag",
            link_label="Stel je vraag",
            note="Bereikbaar op werkdagen (9 tot 17).",
        )
    )

    assert route.link_label == "Stel je vraag"


@pytest.mark.parametrize(
    "routes",
    [
        [_route(id="a"), _route(id="b", is_default=True), _route(id="c", is_default=True)],
        [_route(id="a"), _route(id="b")],
        [_route(id="a", is_default=True), _route(id="a")],
        [_route(id=str(n), is_default=n == 0) for n in range(7)],
    ],
    ids=["two_defaults", "no_default", "duplicate_id", "seven_routes"],
)
def test_the_route_list_needs_unique_ids_one_default_and_at_most_six(routes):
    with pytest.raises(ValidationError):
        WidgetConfig(contact_routes=routes)


def test_an_empty_route_list_is_a_widget_without_routes():
    assert WidgetConfig().contact_routes == []


def _routes() -> tuple[ContactRoute, ...]:
    return (
        ContactRoute(id="support", name="Support", description="Storingen en instellingen", is_default=True),
        ContactRoute(id="billing", name="Facturen", description="Facturen en betalingen"),
    )


async def _pick(reply: httpx.Response | Exception, settings) -> tuple[ContactRoute, str, list[dict]]:
    sent: list[dict] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        if isinstance(reply, Exception):
            raise reply
        return reply

    with respx.mock() as router:
        router.post(f"{LITELLM}/v1/chat/completions").mock(side_effect=answer)
        route, picked = await pick_route(
            _routes(),
            "ik heb een vraag over mijn factuur",
            problem="",
            product="",
            settings=settings,
            delegated_org_id="zorg-acme",
        )
    return route, picked, sent


def _content(payload: dict) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)}}]})


@pytest.fixture
def settings(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "litellm_base_url", LITELLM)
    monkeypatch.setattr(settings, "litellm_master_key", "key")
    monkeypatch.setattr(settings, "extraction_model", "klai-fast")
    return settings


async def test_the_pick_names_the_route_the_model_chose_from_a_closed_list(settings):
    route, picked, sent = await _pick(_content({"route": "billing"}), settings)

    assert (route.id, picked) == ("billing", "model")
    (body,) = sent
    assert body["response_format"]["json_schema"]["schema"]["properties"]["route"]["enum"] == ["support", "billing"]
    assert body["metadata"]["_klai_delegated_org_id"] == "zorg-acme"


@pytest.mark.parametrize(
    "reply",
    [_content({"route": "sales"}), httpx.Response(500), httpx.ConnectError("down")],
    ids=["unknown_id", "server_error", "unreachable"],
)
async def test_a_pick_that_fails_falls_back_to_the_default_route(settings, reply):
    route, picked, _ = await _pick(reply, settings)

    assert (route.id, picked) == ("support", "failed")


async def test_a_single_route_needs_no_pick(settings, monkeypatch):
    call = AsyncMock()
    monkeypatch.setattr(contact_routes, "structured_judge_call", call)
    (only,) = _routes()[:1]

    assert await pick_route((only,), "bel me", problem="", product="", settings=settings) == (only, "single")
    call.assert_not_called()


def test_a_reply_that_names_a_route_records_and_logs_its_id_and_how_it_was_picked():
    """No visitor text and no route name in either: the id is enough to count per route."""
    from types import SimpleNamespace

    from structlog.testing import capture_logs

    from app.services.retrieval_context import KnowledgeTurn
    from app.services.widget_turn import _route_reply

    signals: dict = {}
    inputs = SimpleNamespace(
        answer_signals=signals,
        auth=SimpleNamespace(org_id=7, key_id="wgt_acme"),
        request=SimpleNamespace(model="klai-primary"),
    )
    route = _routes()[1]

    with capture_logs() as logs:
        reply = _route_reply(inputs, KnowledgeTurn(), "tekst", "nl", (route, "model"), decision="refusal", refused=True)  # type: ignore[arg-type]

    assert signals["route"] == {"id": "billing", "picked": "model"}
    (event,) = [log for log in logs if log["event"] == "partner_chat_contact_route"]
    assert event == {
        "event": "partner_chat_contact_route",
        "log_level": "info",
        "org_id": 7,
        "wgt_id": "wgt_acme",
        "route_id": "billing",
        "picked": "model",
    }
    # The route has no booking URL, so the reply carries no button.
    assert (reply.appointment, reply.booking_url, signals["appointment"]) == (False, None, False)
