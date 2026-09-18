"""Acceptance tests for the HubSpot support-case reader.

SPEC-RAG-SUPPORT-GAP (support-gap-detection.md, "First implementation
contract"). These tests are written BEFORE the reader exists and pin the
observable behaviour the shared contract requires:

- account verification against the configured account_id;
- pagination across ticket / thread / message / association pages;
- older-open-ticket retention via pipeline-stage metadata;
- truncated-message original-content resolution;
- unsupported attachments = explicit incomplete evidence (not discarded);
- a failed required page = partial failure (raises), never empty success;
- bounded 429 retry that respects Retry-After;
- source_updated_at derived from every stream, not the ticket mtime;
- no access token in raised error strings.

All fixtures are synthetic. No real HubSpot account is contacted: every
request is served by an httpx.MockTransport pinned to api.hubapi.com.
"""

# ruff: noqa: S105, S106  -- test-only placeholder token strings

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.services.support_source import (
    HUBSPOT_API_BASE,
    HubSpotAccountMismatchError,
    HubSpotAPIError,
    HubSpotPartialFailureError,
    HubSpotSupportReader,
)

Handler = Callable[[httpx.Request], httpx.Response]


def _reader(
    handler: Handler,
    *,
    config: dict[str, Any] | None = None,
    now: datetime | None = None,
    sleeps: list[float] | None = None,
) -> HubSpotSupportReader:
    """Build a reader whose HTTP client is a MockTransport on api.hubapi.com."""
    cfg: dict[str, Any] = {
        "access_token": "pat-super-secret-token",
        "account_id": "12345",
        "lookback_days": 30,
        "pipeline_ids": [],
        "inbox_ids": [],
    }
    if config:
        cfg.update(config)

    client = httpx.AsyncClient(base_url=HUBSPOT_API_BASE, transport=httpx.MockTransport(handler))

    async def _sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)

    return HubSpotSupportReader(
        cfg,
        client=client,
        now=now or datetime(2026, 9, 17, 12, 0, 0, tzinfo=UTC),
        sleep=_sleep,
    )


def _json(payload: dict[str, Any], status: int = 200, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(payload).encode(), headers=headers)


# ---------------------------------------------------------------------------
# Account verification
# ---------------------------------------------------------------------------


async def test_verify_account_ok_when_portal_id_matches() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/account-info/v3/details"
        assert request.headers["authorization"] == "Bearer pat-super-secret-token"
        return _json({"portalId": 12345, "uiDomain": "app.hubspot.com"})

    reader = _reader(handler)
    await reader.verify_account()
    await reader.aclose()


async def test_verify_account_mismatch_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json({"portalId": 99999, "uiDomain": "app.hubspot.com"})

    reader = _reader(handler)
    with pytest.raises(HubSpotAccountMismatchError):
        await reader.verify_account()
    await reader.aclose()


# ---------------------------------------------------------------------------
# Ticket selection: pagination + older-open retention
# ---------------------------------------------------------------------------


def _ticket(tid: str, stage: str, created: str, modified: str | None = None) -> dict[str, Any]:
    return {
        "id": tid,
        "properties": {
            "subject": f"ticket {tid}",
            "content": f"body {tid}",
            "hs_pipeline": "0",
            "hs_pipeline_stage": stage,
            "createdate": created,
            "hs_lastmodifieddate": modified or created,
            "hubspot_owner_id": "55",
        },
    }


def _pipelines_payload() -> dict[str, Any]:
    return {
        "results": [
            {
                "id": "0",
                "label": "Support",
                "stages": [
                    {"id": "1", "label": "New", "metadata": {"isClosed": "false"}},
                    {"id": "2", "label": "Waiting", "metadata": {"isClosed": "false"}},
                    {"id": "9", "label": "Closed", "metadata": {"isClosed": "true"}},
                ],
            }
        ]
    }


async def test_select_tickets_paginates_and_retains_older_open() -> None:
    """Recent tickets AND older still-open tickets are selected across pages;

    an older closed ticket outside the window is excluded.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/crm/v3/pipelines/tickets":
            return _json(_pipelines_payload())
        if path == "/crm/v3/objects/tickets":
            after = request.url.params.get("after")
            if after is None:
                # page 1: one recent open ticket
                return _json(
                    {
                        "results": [_ticket("201", "1", "2026-09-16T09:00:00Z")],
                        "paging": {"next": {"after": "p2", "link": "https://evil.example/next"}},
                    }
                )
            assert after == "p2"
            # page 2: an OLD but still-open ticket, and an OLD closed ticket
            return _json(
                {
                    "results": [
                        _ticket("150", "2", "2026-01-01T09:00:00Z"),  # old + open -> keep
                        _ticket("120", "9", "2026-01-01T09:00:00Z"),  # old + closed -> drop
                    ],
                }
            )
        raise AssertionError(f"unexpected path {path}")

    reader = _reader(handler)
    tickets = await reader.select_tickets()
    await reader.aclose()

    ids = sorted(t["id"] for t in tickets)
    assert ids == ["150", "201"], "recent + older-open kept, older-closed dropped"


async def test_select_tickets_never_follows_paging_link_host() -> None:
    """The reader paginates with the `after` cursor on api.hubapi.com only;

    it must never fetch the attacker-controlled paging.next.link URL.
    """
    seen_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_hosts.append(request.url.host)
        path = request.url.path
        if path == "/crm/v3/pipelines/tickets":
            return _json(_pipelines_payload())
        if path == "/crm/v3/objects/tickets":
            if request.url.params.get("after") is None:
                return _json(
                    {
                        "results": [_ticket("201", "1", "2026-09-16T09:00:00Z")],
                        "paging": {"next": {"after": "p2", "link": "https://evil.example/next"}},
                    }
                )
            return _json({"results": [_ticket("202", "1", "2026-09-16T10:00:00Z")]})
        raise AssertionError(path)

    reader = _reader(handler)
    await reader.select_tickets()
    await reader.aclose()
    assert set(seen_hosts) == {"api.hubapi.com"}


# ---------------------------------------------------------------------------
# Case fetch: threads / messages / associations pagination + roles
# ---------------------------------------------------------------------------


def _full_case_handler(
    *,
    truncated: bool = False,
    with_attachment: bool = False,
    message_page_status: int = 200,
    note_timestamp: str = "2026-09-16T09:30:00Z",
    original_content_empty: bool = False,
    email_direction: str = "INCOMING_EMAIL",
    thread302_inbox: str = "7",
) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = request.url.params
        # threads for a ticket, paginated
        if path == "/conversations/v3/conversations/threads":
            assert params.get("associatedTicketId") == "201"
            if params.get("after") is None:
                return _json(
                    {
                        "results": [{"id": "301", "inboxId": "7"}],
                        "paging": {"next": {"after": "t2"}},
                    }
                )
            return _json({"results": [{"id": "302", "inboxId": thread302_inbox}]})
        # messages in a thread, paginated
        if path == "/conversations/v3/conversations/threads/301/messages":
            if message_page_status != 200:
                return httpx.Response(message_page_status, content=b"{}")
            if params.get("after") is None:
                msg: dict[str, Any] = {
                    "id": "401",
                    "type": "MESSAGE",
                    "channelId": "1000",  # HubSpot live chat -> chat medium
                    "text": "short" if not truncated else "truncated visible part",
                    "createdAt": "2026-09-16T09:05:00Z",
                    "senders": [{"actorId": "V-1"}],
                    "direction": "INCOMING",
                    "truncationStatus": "TRUNCATED" if truncated else "NOT_TRUNCATED",
                    "attachments": [{"fileId": "f1"}] if with_attachment else [],
                }
                return _json({"results": [msg], "paging": {"next": {"after": "m2"}}})
            return _json(
                {
                    "results": [
                        {
                            "id": "402",
                            "type": "MESSAGE",
                            "channelId": "1002",  # HubSpot email channel -> email medium
                            "text": "agent reply",
                            "createdAt": "2026-09-16T09:10:00Z",
                            "senders": [{"actorId": "A-9"}],
                            "direction": "OUTGOING",
                            "truncationStatus": "NOT_TRUNCATED",
                            "attachments": [],
                        }
                    ]
                }
            )
        if path == "/conversations/v3/conversations/threads/302/messages":
            return _json(
                {
                    "results": [
                        {
                            "id": "403",
                            "type": "COMMENT",
                            "channelId": "9999",  # internal note on an unrecognized channel -> unknown
                            "text": "internal agent comment",
                            "createdAt": "2026-09-16T09:12:00Z",
                            "senders": [{"actorId": "A-9"}],
                            "truncationStatus": "NOT_TRUNCATED",
                            "attachments": [],
                        }
                    ]
                }
            )
        # original content for truncated message
        if path == "/conversations/v3/conversations/threads/301/messages/401/original-content":
            if original_content_empty:
                return _json({})
            return _json({"text": "the full untruncated body", "richText": "<p>full</p>"})
        # associations v4: notes + emails, paginated
        if path == "/crm/v4/objects/tickets/201/associations/notes":
            if params.get("after") is None:
                return _json({"results": [{"toObjectId": 501}], "paging": {"next": {"after": "n2"}}})
            return _json({"results": [{"toObjectId": 502}]})
        if path == "/crm/v4/objects/tickets/201/associations/emails":
            return _json({"results": [{"toObjectId": 601}]})
        if path == "/crm/v3/objects/notes/501":
            return _json(
                {"id": "501", "properties": {"hs_note_body": "<p>note one</p>", "hs_timestamp": note_timestamp}}
            )
        if path == "/crm/v3/objects/notes/502":
            return _json(
                {"id": "502", "properties": {"hs_note_body": "<p>note two</p>", "hs_timestamp": "2026-09-16T09:31:00Z"}}
            )
        if path == "/crm/v3/objects/emails/601":
            return _json(
                {
                    "id": "601",
                    "properties": {
                        "hs_email_text": "customer email body",
                        "hs_timestamp": "2026-09-16T09:02:00Z",
                        "hs_email_direction": email_direction,
                    },
                }
            )
        raise AssertionError(f"unexpected path {path}")

    return handler


def _ticket_201(modified: str = "2026-09-16T09:00:00Z") -> dict[str, Any]:
    return _ticket("201", "1", "2026-09-16T08:00:00Z", modified)


async def test_fetch_case_paginates_all_streams_and_maps_roles() -> None:
    reader = _reader(_full_case_handler(), config={"ui_domain": "app.hubspot.com"})
    reader.ui_domain = "app.hubspot.com"
    case = await reader.fetch_case(_ticket_201())
    await reader.aclose()

    assert case is not None
    assert case.external_id == "201"
    assert case.account_id == "12345"
    assert case.complete is True
    assert case.incomplete_reasons == []

    by_id = {m.id: m for m in case.messages}
    # ticket body captured as a "ticket" message
    assert any(m.kind == "ticket" for m in case.messages)
    # ids are scoped by kind so cross-domain integers cannot collide
    assert by_id["message:401"].role == "customer"
    assert by_id["message:402"].role == "agent"
    # internal COMMENT -> internal visibility
    assert by_id["message:403"].visibility == "internal"
    # notes across two pages, both internal
    assert by_id["note:501"].kind == "note" and by_id["note:501"].visibility == "internal"
    assert "note:502" in by_id
    # associated CRM email, direction proves customer
    assert by_id["email:601"].kind == "email" and by_id["email:601"].role == "customer"


async def test_to_payload_preserves_medium_thread_and_channel() -> None:
    """A mixed case keeps each exchange's medium, thread and channel through
    to_payload: live chat -> chat, the email channel and the CRM email -> email,
    an internal COMMENT on an unrecognized channel -> unknown (raw channel kept).
    The ticket body and notes are not a customer medium, and HubSpot supplies no
    canonical reply pointer, so reply_to_id stays None everywhere."""
    reader = _reader(_full_case_handler())
    reader.ui_domain = "app.hubspot.com"
    case = await reader.fetch_case(_ticket_201())
    await reader.aclose()

    assert case is not None
    payload = {m["id"]: m for m in case.to_payload()["messages"]}

    assert (payload["message:401"]["medium"], payload["message:401"]["channel_id"]) == ("chat", "1000")
    assert payload["message:401"]["thread_id"] == "301"
    assert (payload["message:402"]["medium"], payload["message:402"]["channel_id"]) == ("email", "1002")
    assert payload["message:402"]["thread_id"] == "301"
    # internal COMMENT on an unrecognized channel: medium unknown, raw channel kept
    assert (payload["message:403"]["medium"], payload["message:403"]["channel_id"]) == ("unknown", "9999")
    assert payload["message:403"]["thread_id"] == "302"
    assert payload["message:403"]["kind"] == "note"
    # CRM-linked email carries no conversation channel/thread
    assert (payload["email:601"]["medium"], payload["email:601"]["channel_id"]) == ("email", None)
    assert payload["email:601"]["thread_id"] is None
    # ticket body and notes are evidence, not a customer medium
    assert payload["ticket:201"]["medium"] == "unknown"
    assert payload["note:501"]["medium"] == "unknown"
    # HubSpot has no verified reply relationship: never invent one
    assert all(m["reply_to_id"] is None for m in payload.values())


async def test_fetch_case_resolves_truncated_message_original_content() -> None:
    reader = _reader(_full_case_handler(truncated=True))
    reader.ui_domain = "app.hubspot.com"
    case = await reader.fetch_case(_ticket_201())
    await reader.aclose()

    assert case is not None
    body = {m.id: m.text for m in case.messages}
    assert body["message:401"] == "the full untruncated body", "truncated text replaced by original-content"
    assert case.complete is True


async def test_fetch_case_attachment_is_explicit_incomplete_not_discarded() -> None:
    reader = _reader(_full_case_handler(with_attachment=True))
    reader.ui_domain = "app.hubspot.com"
    case = await reader.fetch_case(_ticket_201())
    await reader.aclose()

    assert case is not None
    assert case.complete is False
    assert any("attachment" in r for r in case.incomplete_reasons)
    # the message itself is still present, not dropped
    assert any(m.id == "message:401" for m in case.messages)


async def test_fetch_case_failed_required_page_raises_partial() -> None:
    reader = _reader(_full_case_handler(message_page_status=500))
    reader.ui_domain = "app.hubspot.com"
    with pytest.raises(HubSpotPartialFailureError):
        await reader.fetch_case(_ticket_201())
    await reader.aclose()


async def test_source_updated_at_reflects_latest_stream_not_ticket_mtime() -> None:
    # Ticket last-modified is stale; a note is newer.
    reader = _reader(_full_case_handler(note_timestamp="2026-09-17T08:00:00Z"))
    reader.ui_domain = "app.hubspot.com"
    case = await reader.fetch_case(_ticket_201(modified="2026-09-16T09:00:00Z"))
    await reader.aclose()
    assert case is not None
    assert case.source_updated_at is not None
    assert case.source_updated_at >= "2026-09-17T08:00:00", "newer stream event drives source_updated_at"


# ---------------------------------------------------------------------------
# Retry + secret hygiene
# ---------------------------------------------------------------------------


async def test_get_retries_on_429_and_respects_retry_after() -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, content=b"{}")
        return _json({"portalId": 12345, "uiDomain": "app.hubspot.com"})

    reader = _reader(handler, sleeps=sleeps)
    await reader.verify_account()
    await reader.aclose()
    assert calls["n"] == 2
    assert sleeps == [2.0]


async def test_errors_never_contain_access_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b'{"message":"MISSING_SCOPES"}')

    reader = _reader(handler)
    with pytest.raises(Exception) as exc:  # noqa: PT011 -- asserting on message, not type
        await reader.verify_account()
    await reader.aclose()
    assert "pat-super-secret-token" not in str(exc.value)


# ---------------------------------------------------------------------------
# Envelope validation, cursor cycles, selection identity (data-loss shapes)
# ---------------------------------------------------------------------------


def _pipelines_or(request: httpx.Request) -> httpx.Response | None:
    if request.url.path == "/crm/v3/pipelines/tickets":
        return _json(_pipelines_payload())
    return None


async def test_missing_results_envelope_fails_not_empty_snapshot() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pipelines = _pipelines_or(request)
        if pipelines is not None:
            return pipelines
        if request.url.path == "/crm/v3/objects/tickets":
            return _json({})  # provider drift: no 'results' array
        raise AssertionError(request.url.path)

    reader = _reader(handler)
    with pytest.raises(HubSpotAPIError):
        await reader.select_tickets()
    await reader.aclose()


async def test_empty_pipeline_metadata_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crm/v3/pipelines/tickets":
            return _json({"results": []})
        raise AssertionError(request.url.path)

    reader = _reader(handler)
    with pytest.raises(HubSpotAPIError):
        await reader.select_tickets()
    await reader.aclose()


async def test_ticket_missing_createdate_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pipelines = _pipelines_or(request)
        if pipelines is not None:
            return pipelines
        if request.url.path == "/crm/v3/objects/tickets":
            return _json({"results": [{"id": "201", "properties": {"hs_pipeline_stage": "1"}}]})
        raise AssertionError(request.url.path)

    reader = _reader(handler)
    with pytest.raises(HubSpotAPIError):
        await reader.select_tickets()
    await reader.aclose()


async def test_pagination_cursor_cycle_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pipelines = _pipelines_or(request)
        if pipelines is not None:
            return pipelines
        if request.url.path == "/crm/v3/objects/tickets":
            # every page returns the SAME cursor -> would loop forever
            return _json(
                {
                    "results": [_ticket("201", "1", "2026-09-16T09:00:00Z")],
                    "paging": {"next": {"after": "loop"}},
                }
            )
        raise AssertionError(request.url.path)

    reader = _reader(handler)
    with pytest.raises(HubSpotAPIError):
        await reader.select_tickets()
    await reader.aclose()


# ---------------------------------------------------------------------------
# Configured scope validation: unknown pipeline/inbox ids must fail loudly
# before any snapshot, or reconcile deletes evidence the empty scope hid.
# ---------------------------------------------------------------------------


def _inboxes_page(request: httpx.Request, pages: dict[str | None, dict[str, Any]]) -> httpx.Response:
    """Serve the conversations inbox-metadata endpoint from a cursor->payload map."""
    assert request.url.params.get("archived") == "false"
    return _json(pages[request.url.params.get("after")])


async def test_configured_inbox_ids_validated_across_pages() -> None:
    """A configured inbox that only appears on page 2 of the inbox metadata is
    accepted: validation must follow the `after` cursor, not just read page 1.
    """
    inbox_pages = {
        None: {"results": [{"id": "7"}], "paging": {"next": {"after": "i2"}}},
        "i2": {"results": [{"id": "8"}]},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/crm/v3/pipelines/tickets":
            return _json(_pipelines_payload())
        if path == "/conversations/v3/conversations/inboxes":
            return _inboxes_page(request, inbox_pages)
        if path == "/crm/v3/objects/tickets":
            return _json({"results": [_ticket("201", "1", "2026-09-16T09:00:00Z")]})
        raise AssertionError(path)

    reader = _reader(handler, config={"inbox_ids": ["7", "8"]})
    tickets = await reader.select_tickets()
    await reader.aclose()
    assert [t["id"] for t in tickets] == ["201"], "second-page inbox accepted, selection proceeds"


async def test_unknown_selected_inbox_id_fails_before_snapshot() -> None:
    """A configured inbox absent from live metadata raises instead of silently
    producing an empty in-scope snapshot that reconcile would delete from.
    """
    inbox_pages = {None: {"results": [{"id": "7"}, {"id": "8"}]}}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/crm/v3/pipelines/tickets":
            return _json(_pipelines_payload())
        if path == "/conversations/v3/conversations/inboxes":
            return _inboxes_page(request, inbox_pages)
        raise AssertionError(path)

    reader = _reader(handler, config={"inbox_ids": ["7", "999"]})
    with pytest.raises(HubSpotAPIError) as exc:
        await reader.select_tickets()
    await reader.aclose()
    assert "999" in str(exc.value), "the unknown inbox id is named"


async def test_unknown_selected_pipeline_id_fails_before_snapshot() -> None:
    """A configured pipeline absent from live metadata raises: the old code
    silently skipped it, collapsing the open-stage set so reconcile deleted
    still-open older tickets.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crm/v3/pipelines/tickets":
            return _json(_pipelines_payload())
        raise AssertionError(request.url.path)

    reader = _reader(handler, config={"pipeline_ids": ["0", "77"]})
    with pytest.raises(HubSpotAPIError) as exc:
        await reader.select_tickets()
    await reader.aclose()
    assert "77" in str(exc.value), "the unknown pipeline id is named"


async def test_inbox_metadata_unavailable_fails_not_empty_snapshot() -> None:
    """If inbox metadata cannot be read, selection fails rather than treating an
    unverifiable scope as empty.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/crm/v3/pipelines/tickets":
            return _json(_pipelines_payload())
        if path == "/conversations/v3/conversations/inboxes":
            return httpx.Response(500, content=b"{}")
        raise AssertionError(path)

    reader = _reader(handler, config={"inbox_ids": ["7"]})
    with pytest.raises(HubSpotAPIError):
        await reader.select_tickets()
    await reader.aclose()


async def test_valid_scope_subset_with_empty_tickets_still_succeeds() -> None:
    """A valid pipeline+inbox subset with a genuinely empty ticket list returns
    an empty selection without raising: legitimately-empty must stay legal.
    """
    inbox_pages = {None: {"results": [{"id": "7"}, {"id": "8"}]}}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/crm/v3/pipelines/tickets":
            return _json(_pipelines_payload())
        if path == "/conversations/v3/conversations/inboxes":
            return _inboxes_page(request, inbox_pages)
        if path == "/crm/v3/objects/tickets":
            return _json({"results": []})
        raise AssertionError(path)

    reader = _reader(handler, config={"pipeline_ids": ["0"], "inbox_ids": ["7"]})
    tickets = await reader.select_tickets()
    await reader.aclose()
    assert tickets == [], "empty ticket result is a valid empty selection, not a failure"


# ---------------------------------------------------------------------------
# Truncation, email direction, inbox scope, id scoping
# ---------------------------------------------------------------------------


async def test_original_content_unresolved_marks_incomplete() -> None:
    reader = _reader(_full_case_handler(truncated=True, original_content_empty=True))
    reader.ui_domain = "app.hubspot.com"
    case = await reader.fetch_case(_ticket_201())
    await reader.aclose()

    assert case is not None
    assert case.complete is False
    assert any("truncated_unresolved" in r for r in case.incomplete_reasons)
    assert "message:401" not in case.metadata["resolved_truncated_message_ids"]
    body = {m.id: m.text for m in case.messages}
    assert body["message:401"] == "truncated visible part", "truncated text is not relabelled complete"


async def test_email_direction_maps_documented_roles() -> None:
    for direction, expected in (("EMAIL", "agent"), ("FORWARDED_EMAIL", "agent"), ("WEIRD_VALUE", "unknown")):
        reader = _reader(_full_case_handler(email_direction=direction))
        reader.ui_domain = "app.hubspot.com"
        case = await reader.fetch_case(_ticket_201())
        await reader.aclose()
        assert case is not None
        by_id = {m.id: m for m in case.messages}
        assert by_id["email:601"].role == expected, direction


async def test_inbox_filter_excludes_wholly_unmatched_case() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/conversations/v3/conversations/threads":
            return _json({"results": [{"id": "301", "inboxId": "9"}]})
        raise AssertionError(request.url.path)

    reader = _reader(handler, config={"inbox_ids": ["7"]})
    reader.ui_domain = "app.hubspot.com"
    case = await reader.fetch_case(_ticket_201())
    await reader.aclose()
    assert case is None, "no thread in a selected inbox -> case is out of scope"


async def test_inbox_filter_mixed_threads_marks_incomplete() -> None:
    reader = _reader(_full_case_handler(thread302_inbox="9"), config={"inbox_ids": ["7"]})
    reader.ui_domain = "app.hubspot.com"
    case = await reader.fetch_case(_ticket_201())
    await reader.aclose()

    assert case is not None
    assert case.complete is False
    assert "partial_inbox_scope" in case.incomplete_reasons
    # no evidence from the excluded thread 302 (its comment 403) is imported
    assert all(m.id != "message:403" for m in case.messages)


async def test_scoped_ids_prevent_cross_domain_collision() -> None:
    """A conversation message and a CRM note that share raw id 500 stay distinct."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/conversations/v3/conversations/threads":
            return _json({"results": [{"id": "301", "inboxId": "7"}]})
        if path == "/conversations/v3/conversations/threads/301/messages":
            return _json(
                {
                    "results": [
                        {
                            "id": "500",
                            "type": "MESSAGE",
                            "text": "msg 500",
                            "createdAt": "2026-09-16T09:05:00Z",
                            "senders": [{"actorId": "V-1"}],
                            "truncationStatus": "NOT_TRUNCATED",
                            "attachments": [],
                        }
                    ]
                }
            )
        if path == "/crm/v4/objects/tickets/201/associations/notes":
            return _json({"results": [{"toObjectId": 500}]})
        if path == "/crm/v4/objects/tickets/201/associations/emails":
            return _json({"results": []})
        if path == "/crm/v3/objects/notes/500":
            return _json(
                {"id": "500", "properties": {"hs_note_body": "note 500", "hs_timestamp": "2026-09-16T09:30:00Z"}}
            )
        raise AssertionError(path)

    reader = _reader(handler)
    reader.ui_domain = "app.hubspot.com"
    case = await reader.fetch_case(_ticket_201())
    await reader.aclose()

    assert case is not None
    ids = {m.id for m in case.messages}
    assert "message:500" in ids and "note:500" in ids, "same raw id, different kinds -> distinct entries"
