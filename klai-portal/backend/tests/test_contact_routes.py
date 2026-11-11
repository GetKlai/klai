"""A widget's contact routes: what the admin may store, and which route a turn at a human names."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from pydantic import ValidationError

from app.api.admin_widgets import SavedWidgetConfig, UpdateWidgetRequest, WidgetConfig
from app.services import contact_routes
from app.services.contact_routes import ContactRoute, pick_exit, pick_route

LITELLM = "http://litellm.test"


def _route(**values) -> dict:
    return {"name": "Support", "phone": "010 000 0000", **values}


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
        {"name": "# Support"},
        {"name": "Support ~~oud~~"},
        {"note": "Ma | Di | Wo"},
        {"note": "- op werkdagen"},
        {"note": "+ op werkdagen"},
        {"note": "> op werkdagen"},
        {"note": "1. op werkdagen"},
        {"link_url": "https://example.com/a", "link_label": "2) Lees meer"},
        {"email": "*hulp*@example.com"},
        {"email": "_hulp@example.com"},
        {"email": "hulp_@example.com"},
        {"email": "hulp._team@example.com"},
        {"email": "hu`lp@example.com"},
        {"email": "hu~lp@example.com"},
    ],
)
def test_a_route_value_that_could_inject_markdown_or_a_link_is_refused(values):
    with pytest.raises(ValidationError):
        ContactRoute.model_validate({**_route(), **values})


def test_ordinary_punctuation_times_hyphens_and_underscores_inside_an_address_stay_legal():
    route = ContactRoute.model_validate(
        _route(
            name="Klantenservice-team 2",
            note="Bereikbaar vanaf 8.30 uur, ook op zaterdag - niet op feestdagen!",
            email="first_last@example.com",
            link_url="https://help.example.com/a",
            link_label="Stel je vraag (online)",
        )
    )

    assert route.email == "first_last@example.com"


def test_a_route_without_any_way_to_reach_it_is_refused_on_save_with_its_position():
    """The editor shows the error on the route the loc names."""
    with pytest.raises(ValidationError) as refused:
        routes = [_route(is_default=True), {"name": "Verkoop", "note": "Op werkdagen."}]
        UpdateWidgetRequest.model_validate({"widget_config": {"contact_routes": routes}})

    (error,) = refused.value.errors()
    # FastAPI answers 422 with "body" in front of this loc.
    assert error["loc"] == ("widget_config", "contact_routes", 1)
    assert "needs a way to reach it" in error["msg"]
    # A route that only refers subjects is reached through its referral text.
    SavedWidgetConfig(contact_routes=[{"name": "Support", "referral_text": "Bel ons even.", "is_default": True}])
    # The stored and returned config does not apply the rule, so an older route still reads back.
    WidgetConfig(contact_routes=[{"name": "Support", "is_default": True}])


def test_a_stored_route_without_any_channel_is_still_read():
    (route,) = contact_routes.routes_from_config({"contact_routes": [{"name": "Support", "is_default": True}]})

    assert route.name == "Support"


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


def test_the_referral_text_is_free_text_up_to_the_old_limit():
    text = "Voor prijzen plan je [een gesprek](https://book.example.com/a) of mail info@example.com."
    assert ContactRoute.model_validate(_route(referral_text=text)).referral_text == text
    with pytest.raises(ValidationError):
        ContactRoute.model_validate(_route(referral_text="x" * 501))


def test_reading_stored_routes_drops_bad_values_skips_unusable_routes_and_logs_only_field_names():
    """A legacy value the old code never checked may not 500 the chat, the widget config or the admin list."""
    from structlog.testing import capture_logs

    stored = {
        "contact_routes": [
            {"id": "a", "name": "Support", "booking_url": "https://", "email": "geen adres", "phone": "0800-HELP"},
            {"id": "a", "name": "Facturen", "link_url": "https://help.example.com/f", "is_default": True},
            {"id": "c", "name": "[Bel](https://evil.example.com)"},
            "geen route",
            {"id": "d", "name": "Verkoop", "is_default": True},
        ]
    }

    with capture_logs() as logs:
        routes = contact_routes.routes_from_config(stored, org_id=7, widget_id="wgt_acme")

    assert [route.name for route in routes] == ["Support", "Facturen", "Verkoop"]
    assert (routes[0].booking_url, routes[0].email, routes[0].phone) == (None, None, None)
    assert (routes[1].link_url, routes[1].link_label) == (None, None)
    assert routes[1].id != "a"
    assert [route.is_default for route in routes] == [False, True, False]
    fields = sorted(log["field"] for log in logs if log["event"] == "contact_route_repaired")
    assert fields == sorted(
        ["booking_url", "email", "phone", "link_label", "link_url", "id", "name", "route", "is_default"]
    )
    assert all(set(log) >= {"org_id", "widget_id"} for log in logs if log["event"] == "contact_route_repaired")
    assert "geen adres" not in str(logs) and "evil" not in str(logs)


def test_the_admin_list_reads_a_widget_with_a_bad_stored_route():
    from unittest.mock import MagicMock

    from app.api.admin_widgets import _widget_to_response

    widget = MagicMock(widget_config={"contact_routes": [{"name": "Support", "booking_url": "https://"}]})
    widget.public_share_enabled = False
    widget.rate_limit_rpm = 60
    widget.last_used_at = None
    widget.created_at, widget.created_by = "2026-01-01", "user-1"
    widget.name, widget.description, widget.id, widget.widget_id = "Bot", None, "uuid-1", "wgt_acme"

    (route,) = _widget_to_response(widget, kb_access_count=0).widget_config.contact_routes
    assert (route.name, route.booking_url, route.is_default) == ("Support", None, True)


def test_a_save_keeps_the_old_contact_keys_so_the_previous_release_still_works_after_a_rollback():
    from app.api.admin_widgets import WidgetConfig, _keeping_legacy_contact_keys

    stored = {
        "booking_url": "https://book.example.com/old",
        "off_topic_subjects": "prijzen",
        "integrations": {"appointments": {"enabled": True, "booking_url": "https://book.example.com/embed"}},
    }
    saved = WidgetConfig(contact_routes=[{"name": "Support", "is_default": True}]).model_dump()

    kept = _keeping_legacy_contact_keys(stored, saved)

    assert (kept["booking_url"], kept["off_topic_subjects"]) == ("https://book.example.com/old", "prijzen")
    assert kept["integrations"]["appointments"] == {"enabled": False, "booking_url": "https://book.example.com/embed"}
    assert kept["contact_routes"] == saved["contact_routes"]


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


# ─── The exit call: what kind of message a "not found" turn is ───────────


async def _exit(reply: httpx.Response | Exception, settings, routes=None, latest="top, ik probeer het morgen", **kw):
    sent: list[dict] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        if isinstance(reply, Exception):
            raise reply
        return reply

    with respx.mock() as router:
        router.post(f"{LITELLM}/v1/chat/completions").mock(side_effect=answer)
        result = await pick_exit(
            _routes() if routes is None else routes,
            latest,
            "Dit vind ik niet terug in onze helpartikelen.",
            first_turn=kw.pop("first_turn", False),
            problem="",
            product="",
            settings=settings,
            delegated_org_id="zorg-acme",
        )
    return result, sent


async def test_one_call_reads_the_kind_and_picks_the_route_from_closed_lists(settings):
    result, sent = await _exit(_content({"kind": "request", "route": "billing"}), settings)

    assert (result.kind, result.how, result.route[0].id, result.route[1]) == ("request", "model", "billing", "model")
    (body,) = sent
    properties = body["response_format"]["json_schema"]["schema"]["properties"]
    assert properties["kind"]["enum"] == ["question", "request", "closing"]
    assert properties["route"]["enum"] == ["support", "billing"]
    assert body["metadata"]["_klai_delegated_org_id"] == "zorg-acme"


async def test_one_route_or_none_is_a_call_for_the_kind_alone(settings):
    only, *_ = _routes()
    for routes, expected in (((only,), (only, "single")), ((), None)):
        result, sent = await _exit(_content({"kind": "request"}), settings, routes=routes)

        assert (result.kind, result.route) == ("request", expected)
        assert "route" not in sent[0]["response_format"]["json_schema"]["schema"]["properties"]


async def test_a_closing_stands_on_a_follow_up_without_a_question_mark(settings):
    result, _ = await _exit(_content({"kind": "closing", "route": "billing"}), settings)

    assert (result.kind, result.how) == ("closing", "model")


@pytest.mark.parametrize(
    ("latest", "first_turn"),
    [("Goedemiddag,", True), ("Top, werkt dat ook zo op mobiel?", False), ("Top, werkt dat ook zo\uff1f", False)],
    ids=["first_message", "question_mark", "fullwidth_question_mark"],
)
async def test_a_closing_is_refused_in_code_on_a_first_message_and_on_a_question_mark(settings, latest, first_turn):
    result, _ = await _exit(
        _content({"kind": "closing", "route": "billing"}), settings, latest=latest, first_turn=first_turn
    )

    assert (result.kind, result.how) == ("question", "rule")
    # The route the same call picked is still used for the not-found reply.
    assert result.route[0].id == "billing"


@pytest.mark.parametrize(
    "reply",
    [
        _content({"kind": "refund", "route": "sales"}),
        _content({"kind": "request", "route": "sales"}),
        _content({"kind": "closing", "route": "sales"}),
        httpx.Response(500),
        httpx.ConnectError("down"),
    ],
    ids=["unknown_kind", "request_with_unknown_route", "closing_with_unknown_route", "server_error", "unreachable"],
)
async def test_an_exit_call_that_fails_is_a_question_for_the_default_route(settings, reply):
    result, _ = await _exit(reply, settings)

    assert (result.kind, result.how, result.route[0].id, result.route[1]) == ("question", "failed", "support", "failed")
