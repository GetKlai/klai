"""Read-only HubSpot support-case reader for the support-gap sync.

SPEC-RAG-SUPPORT-GAP (support-gap-detection.md, "First implementation
contract"). This module turns HubSpot tickets + conversation threads +
linked CRM notes/emails into the tenant-neutral ``SupportCase`` payload
the portal evidence endpoint accepts. It reads ``api.hubapi.com`` over
GET plus the read-only ``POST /crm/v3/objects/tickets/search`` (``after``
cursor on a fixed host, never ``paging.next.link``), keeps the token in
the Authorization header alone, and never touches knowledge ingestion.
Provider drift (a missing ``results`` envelope, an unresolved truncation,
an unclassifiable ticket) fails visibly rather than producing an empty
snapshot that could reconcile-delete evidence.

API contracts verified against current HubSpot docs on 2026-09-17:
``GET /account-info/v3/details`` (``portalId``), ``GET
/crm/v3/pipelines/tickets`` (stage ``metadata.isClosed``), ``POST
/crm/v3/objects/tickets/search`` (server-side scope, results +
``paging.next.after``, ``total``, max 200/page and 10,000/query),
``GET /conversations/v3/conversations/threads?associatedTicketId=`` and
``/threads/{id}/messages`` (``paging.next.after``; message
``truncationStatus`` values ``NOT_TRUNCATED`` /
``TRUNCATED_TO_MOST_RECENT_REPLY`` / ``TRUNCATED``),
``/threads/{tid}/messages/{mid}/original-content`` (``text`` / ``richText``),
and CRM v4 associations ``/crm/v4/objects/tickets/{id}/associations/{type}``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import html2text
import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

HUBSPOT_API_BASE = "https://api.hubapi.com"

# Retryable transient statuses. 429 = rate limit, 5xx = upstream fault.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_RETRY_DELAY_SECONDS = 30.0

# Standard ticket properties requested on the object-list read. Kept to the
# documented-standard set so the list call cannot 400 on an unknown property.
_TICKET_PROPERTIES = (
    "subject",
    "content",
    "hs_pipeline",
    "hs_pipeline_stage",
    "createdate",
    "hs_lastmodifieddate",
    "hs_ticket_priority",
    "hubspot_owner_id",
)

_PAGE_LIMIT = 100

# CRM search page size (documented max 200) and the hard 10,000-result ceiling a
# single search can page through. A scope above the ceiling cannot be read
# completely, so it must fail rather than reconcile from a truncated first page.
_SEARCH_PAGE_LIMIT = 200
_SEARCH_RESULT_CEILING = 10000


class HubSpotError(Exception):
    """Base class for HubSpot reader failures."""


class HubSpotAPIError(HubSpotError):
    """A HubSpot read (GET or the ticket-search POST) failed. Carries the status
    code.

    The message is built from the method and path and status only — never the
    token.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class HubSpotAccountMismatchError(HubSpotError):
    """The live account's portalId does not match the configured account_id."""


class HubSpotPartialFailureError(HubSpotError):
    """A required page of a single case could not be read after retries.

    The case is incomplete; the caller must fail the run and must not
    reconcile deletions from a partial snapshot.
    """


def _html_to_text(html: str) -> str:
    """Convert a HubSpot HTML body to plain text (mirrors the Confluence path)."""
    h = html2text.HTML2Text()
    h.ignore_images = True
    h.ignore_links = False
    h.body_width = 0
    return h.handle(html).strip()


def _parse_ts(value: str | None) -> datetime | None:
    """Parse a HubSpot ISO-8601 timestamp; return None when absent/invalid."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _role_from_actor(senders: list[dict[str, Any]]) -> str:
    """Map a message's first sender actor prefix to a proven role.

    HubSpot actor IDs are prefixed by actor type: ``V-`` visitor
    (customer), ``A-`` agent. Anything else (system, integration, email
    actor, or no sender) stays ``unknown`` — the contract forbids
    guessing a role without actor evidence.
    """
    for sender in senders:
        actor_id = str(sender.get("actorId", ""))
        if actor_id.startswith("V"):
            return "customer"
        if actor_id.startswith("A"):
            return "agent"
    return "unknown"


# hs_email_direction (CRM email activity guide): INCOMING_EMAIL is a customer
# reply; EMAIL and FORWARDED_EMAIL are agent-sent. Any other value stays unknown.
_EMAIL_ROLE = {"INCOMING_EMAIL": "customer", "EMAIL": "agent", "FORWARDED_EMAIL": "agent"}

# Documented HubSpot channels; preserve unrecognized IDs without guessing a medium.
_CHANNEL_MEDIUM = {"1000": "chat", "1001": "chat", "1002": "email"}


def _require_results(data: dict[str, Any], path: str) -> list[dict[str, Any]]:
    """Return the ``results`` array, raising on a missing/malformed envelope.

    A drifted ``{}`` response is NOT an empty snapshot: treating it as one
    would let reconciliation delete every case. Only an explicit ``[]``
    means genuinely empty.
    """
    results = data.get("results")
    if not isinstance(results, list):
        raise HubSpotAPIError(f"HubSpot {path}: response missing a 'results' array")
    return cast("list[dict[str, Any]]", results)


def _scoped_id(kind: str, raw: str) -> str:
    """Scope an object id by kind so ids from different HubSpot domains
    (message/note/email/ticket) cannot collide when the analyzer keys by id."""
    return f"{kind}:{raw}"


@dataclass
class SupportMessage:
    """One evidence entry within a support case (contract message shape)."""

    id: str
    kind: str  # message | note | email | transcript | ticket
    role: str  # customer | agent | unknown
    text: str
    occurred_at: str | None
    visibility: str  # customer | internal | unknown
    medium: str = "unknown"  # call | email | chat | unknown
    channel_id: str | None = None
    thread_id: str | None = None
    reply_to_id: str | None = None
    speaker_id: str | None = None
    start_seconds: float | None = None
    end_seconds: float | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "role": self.role,
            "text": self.text,
            "occurred_at": self.occurred_at,
            "visibility": self.visibility,
            "medium": self.medium,
            "channel_id": self.channel_id,
            "thread_id": self.thread_id,
            "reply_to_id": self.reply_to_id,
            "speaker_id": self.speaker_id,
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
        }


@dataclass
class SupportCase:
    """The validated case payload posted to the portal evidence endpoint."""

    account_id: str
    external_id: str
    subject: str
    language: str | None
    source_url: str | None
    source_updated_at: str | None
    complete: bool
    incomplete_reasons: list[str]
    messages: list[SupportMessage]
    metadata: dict[str, Any]
    source: str = "hubspot"

    def to_payload(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "account_id": self.account_id,
            "external_id": self.external_id,
            "subject": self.subject,
            "language": self.language,
            "source_url": self.source_url,
            "source_updated_at": self.source_updated_at,
            "complete": self.complete,
            "incomplete_reasons": self.incomplete_reasons,
            "messages": [m.to_payload() for m in self.messages],
            "metadata": self.metadata,
        }


class _CaseBuilder:
    """Mutable accumulator while assembling one case from several streams."""

    def __init__(self) -> None:
        self.messages: list[SupportMessage] = []
        self.incomplete_reasons: list[str] = []
        self.thread_ids: list[str] = []
        self.inbox_ids: list[str] = []
        self.resolved_truncated: list[str] = []
        self.latest: datetime | None = None
        self.threads_total = 0
        self.threads_in_scope = 0
        self.threads_excluded = 0

    def note_time(self, value: str | None) -> None:
        parsed = _parse_ts(value)
        if parsed is not None and (self.latest is None or parsed > self.latest):
            self.latest = parsed


class HubSpotSupportReader:
    """Reads support cases for one connector config from HubSpot.

    A fresh instance is built per sync run from the decrypted connector
    config; it holds no cross-tenant state. The httpx client can be
    injected for tests (a MockTransport pinned to ``api.hubapi.com``).
    """

    def __init__(
        self,
        config: dict[str, Any],
        *,
        client: httpx.AsyncClient | None = None,
        now: datetime | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        max_retries: int = 3,
    ) -> None:
        access_token = str(config.get("access_token") or "").strip()
        if not access_token:
            raise ValueError("hubspot_support config missing required 'access_token'")
        account_id = str(config.get("account_id") or "").strip()
        if not account_id.isdigit():
            raise ValueError("hubspot_support config 'account_id' must be a numeric string")
        lookback_days = int(config.get("lookback_days") or 30)
        if not 1 <= lookback_days <= 90:
            raise ValueError("hubspot_support config 'lookback_days' must be between 1 and 90")

        pipeline_ids: list[Any] = config.get("pipeline_ids") or []
        inbox_ids: list[Any] = config.get("inbox_ids") or []
        self.account_id = account_id
        self._lookback_days = lookback_days
        self._pipeline_ids = {str(p) for p in pipeline_ids}
        self._inbox_ids = {str(i) for i in inbox_ids}
        self._now = now or datetime.now(UTC)
        self._sleep = sleep or asyncio.sleep
        self._max_retries = max_retries
        # Set once verify_account() runs; used only to build citation URLs.
        self.ui_domain = "app.hubspot.com"

        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(base_url=HUBSPOT_API_BASE, timeout=30.0)
        # Apply auth on the client (owned or injected) so every request
        # carries it and the token never has to be threaded per-call. The
        # token lives only here — never in metadata, cursors, or errors.
        self._client.headers["Authorization"] = f"Bearer {access_token}"

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # -- HTTP with bounded retry -------------------------------------------

    @staticmethod
    def _retry_delay(resp: httpx.Response) -> float:
        raw = resp.headers.get("Retry-After")
        if raw is None:
            return 1.0
        try:
            return min(float(raw), _MAX_RETRY_DELAY_SECONDS)
        except ValueError:
            return 1.0

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a request to a fixed api.hubapi.com path with bounded retry on
        429/5xx.

        Raises :class:`HubSpotAPIError` on a non-retryable 4xx or after the
        retry budget is exhausted. The message names the method + path +
        status only, so the access token can never leak into an error string.
        """
        for attempt in range(self._max_retries + 1):
            resp = await self._client.request(method, path, params=params, json=json)
            if resp.status_code in _RETRYABLE_STATUS and attempt < self._max_retries:
                await self._sleep(self._retry_delay(resp))
                continue
            if resp.status_code >= 400:
                raise HubSpotAPIError(
                    f"HubSpot {method} {path} -> {resp.status_code}",
                    status_code=resp.status_code,
                )
            return resp.json()
        # Unreachable: the loop either returns or raises above.
        raise HubSpotAPIError(f"HubSpot {method} {path} exhausted retries")

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._send("GET", path, params=params)

    async def _post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        return await self._send("POST", path, json=json)

    async def _paginate(self, path: str, params: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """Yield every result across pages using the ``after`` cursor only.

        The ``paging.next.link`` absolute URL is ignored (reads stay on
        api.hubapi.com), the envelope is validated per page, and a repeated
        cursor raises instead of looping forever.
        """
        after: str | None = None
        seen_cursors: set[str] = set()
        while True:
            page_params = dict(params)
            if after:
                page_params["after"] = after
            data = await self._get(path, page_params)
            for item in _require_results(data, path):
                yield item
            paging: dict[str, Any] = data.get("paging") or {}
            next_page: dict[str, Any] = paging.get("next") or {}
            raw_after = next_page.get("after")
            if not raw_after:
                return
            after = str(raw_after)
            if after in seen_cursors:
                raise HubSpotAPIError(f"HubSpot {path}: pagination cursor repeated ({after})")
            seen_cursors.add(after)

    # -- Account + selection ------------------------------------------------

    async def verify_account(self) -> None:
        """Confirm the live portalId equals the configured account_id."""
        data = await self._get("/account-info/v3/details")
        portal_id = str(data.get("portalId"))
        if portal_id != self.account_id:
            raise HubSpotAccountMismatchError(
                f"HubSpot account mismatch: token account {portal_id} != configured {self.account_id}"
            )
        self.ui_domain = str(data.get("uiDomain") or self.ui_domain)

    async def _open_stage_ids(self) -> set[str]:
        """Stage IDs whose pipeline metadata marks them as not closed.

        Empty/drifted pipeline metadata raises: with no open-stage set the
        older-open retention silently collapses and reconciliation would
        delete still-open cases.
        """
        data = await self._get("/crm/v3/pipelines/tickets")
        pipelines = _require_results(data, "/crm/v3/pipelines/tickets")
        if not pipelines:
            raise HubSpotAPIError("HubSpot ticket pipelines empty; cannot classify open stages")
        # A configured pipeline absent from live metadata is not "no open
        # stages"; skipping it silently would collapse the open-stage set and
        # let reconcile delete still-open older tickets. Fail before selecting.
        unknown = self._pipeline_ids - {str(p.get("id")) for p in pipelines}
        if unknown:
            raise HubSpotAPIError(f"HubSpot configured pipeline_ids not found in live metadata: {sorted(unknown)}")
        open_stages: set[str] = set()
        for pipeline in pipelines:
            if self._pipeline_ids and str(pipeline.get("id")) not in self._pipeline_ids:
                continue
            stages: list[dict[str, Any]] = pipeline.get("stages") or []
            for stage in stages:
                meta: dict[str, Any] = stage.get("metadata") or {}
                if str(meta.get("isClosed", "")).lower() != "true":
                    open_stages.add(str(stage.get("id")))
        return open_stages

    async def _validate_inbox_ids(self) -> None:
        """Fail if any configured inbox id is unknown in live metadata.

        Inbox scope is a client-side thread filter (``_collect_threads``); an
        id that no live inbox carries would exclude every thread, yielding
        empty in-scope cases that reconcile could delete. Verify the selection
        against ``GET /conversations/v3/conversations/inboxes`` (``results[].id``
        + ``paging.next.after``; archived inboxes cannot hold live threads) so
        a misconfigured id fails loudly before any snapshot is produced.
        """
        if not self._inbox_ids:
            return
        live_ids: set[str] = set()
        async for inbox in self._paginate("/conversations/v3/conversations/inboxes", {"archived": "false"}):
            inbox_id = inbox.get("id")
            if inbox_id is not None:
                live_ids.add(str(inbox_id))
        unknown = self._inbox_ids - live_ids
        if unknown:
            raise HubSpotAPIError(f"HubSpot configured inbox_ids not found in live metadata: {sorted(unknown)}")

    def _selection_search_body(self, open_stages: set[str], window_start_ms: int) -> dict[str, Any]:
        """Build the search body scoping tickets to (created within the window)
        OR (modified within the window) OR (still open), each AND-ed with the
        configured pipelines.

        Filter groups are OR-ed by HubSpot; filters within a group are AND-ed.
        The ``hs_lastmodifieddate`` group brings in tickets whose original
        create date and closed stage would otherwise exclude them but which
        changed inside the window; it does not replace the created/open groups
        because a note or message edit does not always bump that timestamp, so
        those groups stay as the completeness floor. The open group is omitted
        when no open stage exists, so the ``IN`` filter never carries an empty
        value list (which the API rejects).
        """
        pipeline_filter = (
            {"propertyName": "hs_pipeline", "operator": "IN", "values": sorted(self._pipeline_ids)}
            if self._pipeline_ids
            else None
        )

        def _group(*filters: dict[str, Any]) -> dict[str, Any]:
            group = list(filters)
            if pipeline_filter is not None:
                group.append(pipeline_filter)
            return {"filters": group}

        filter_groups = [
            _group({"propertyName": "createdate", "operator": "GTE", "value": str(window_start_ms)}),
            _group({"propertyName": "hs_lastmodifieddate", "operator": "GTE", "value": str(window_start_ms)}),
        ]
        if open_stages:
            filter_groups.append(
                _group({"propertyName": "hs_pipeline_stage", "operator": "IN", "values": sorted(open_stages)})
            )

        return {
            "filterGroups": filter_groups,
            "properties": list(_TICKET_PROPERTIES),
            "limit": _SEARCH_PAGE_LIMIT,
            "sorts": [{"propertyName": "createdate", "direction": "ASCENDING"}],
        }

    async def _search_tickets(self, body: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """Yield every ticket matching the search body across pages.

        Pagination uses the ``after`` cursor in the POST body only (never
        ``paging.next.link``), validates the envelope per page, and stops a
        repeated cursor. When ``total`` exceeds the search ceiling the scope
        cannot be read completely, so it raises rather than reconcile from a
        silently-truncated snapshot.
        """
        path = "/crm/v3/objects/tickets/search"
        after: str | None = None
        seen_cursors: set[str] = set()
        while True:
            page_body = dict(body)
            if after:
                page_body["after"] = after
            data = await self._post(path, page_body)
            total = data.get("total")
            if isinstance(total, int) and total > _SEARCH_RESULT_CEILING:
                raise HubSpotAPIError(
                    f"HubSpot {path}: {total} tickets exceed the {_SEARCH_RESULT_CEILING}-result search "
                    "ceiling; narrow pipeline_ids or lookback_days"
                )
            for item in _require_results(data, path):
                yield item
            paging: dict[str, Any] = data.get("paging") or {}
            next_page: dict[str, Any] = paging.get("next") or {}
            raw_after = next_page.get("after")
            if not raw_after:
                return
            after = str(raw_after)
            if after in seen_cursors:
                raise HubSpotAPIError(f"HubSpot {path}: pagination cursor repeated ({after})")
            seen_cursors.add(after)

    async def select_tickets(self) -> list[dict[str, Any]]:
        """Tickets in scope: created within the window OR modified within the
        window OR still open.

        Coverage deliberately favours completeness: every run re-selects the
        same window plus all older still-open tickets, and additionally any
        ticket whose ``hs_lastmodifieddate`` falls inside the window even when
        its create date is older and its stage is closed. The older-open group
        stays because ``hs_lastmodifieddate`` does not update on every note or
        message edit, so it cannot be the only signal for a changed case. The
        scope is applied server-side by ``POST /crm/v3/objects/tickets/search``
        so a large account is never fully enumerated; a scope above the
        10,000-result ceiling fails loudly instead of truncating.
        """
        open_stages = await self._open_stage_ids()
        await self._validate_inbox_ids()
        window_start_ms = int((self._now - timedelta(days=self._lookback_days)).timestamp() * 1000)

        selected: list[dict[str, Any]] = []
        async for ticket in self._search_tickets(self._selection_search_body(open_stages, window_start_ms)):
            ticket_id = ticket.get("id")
            if not ticket_id:
                raise HubSpotAPIError("HubSpot ticket missing 'id' in selection page")
            props: dict[str, Any] = ticket.get("properties") or {}
            if _parse_ts(props.get("createdate")) is None:
                raise HubSpotAPIError(f"HubSpot ticket {ticket_id}: missing/unparseable createdate")
            selected.append(ticket)
        return selected

    # -- Per-case assembly --------------------------------------------------

    async def fetch_case(self, ticket: dict[str, Any]) -> SupportCase | None:
        """Assemble one case, or ``None`` when it is outside the selected
        inbox scope. Raise on any failed/malformed required page."""
        try:
            return await self._fetch_case(ticket)
        except HubSpotAPIError as exc:
            # A required page failed/drifted. Do not degrade to an
            # empty/partial success — the run must fail and skip reconcile.
            raise HubSpotPartialFailureError(f"HubSpot case {ticket.get('id')} incomplete: {exc}") from exc

    async def _fetch_case(self, ticket: dict[str, Any]) -> SupportCase | None:
        ticket_id = str(ticket.get("id"))
        props: dict[str, Any] = ticket.get("properties") or {}
        builder = _CaseBuilder()
        builder.note_time(props.get("hs_lastmodifieddate"))
        builder.note_time(props.get("createdate"))

        # Threads first: they decide inbox scope before any other evidence
        # (ticket body, notes, emails) is imported.
        await self._collect_threads(ticket_id, builder)
        if self._inbox_ids and builder.threads_in_scope == 0:
            # No conversation in a selected inbox: the case is wholly out of
            # scope. Import nothing; reconcile will remove any stale copy.
            return None

        content = str(props.get("content") or "").strip()
        if content:
            builder.messages.insert(
                0,
                SupportMessage(
                    id=_scoped_id("ticket", ticket_id),
                    kind="ticket",
                    role="unknown",
                    text=content,
                    occurred_at=props.get("createdate"),
                    visibility="customer",
                ),
            )

        await self._collect_associated(ticket_id, "notes", builder)
        await self._collect_associated(ticket_id, "emails", builder)

        if self._inbox_ids and builder.threads_excluded > 0:
            # Some threads sit in other inboxes: the selected-scope view is
            # not the full support context, so the case is incomplete.
            builder.incomplete_reasons.append("partial_inbox_scope")

        metadata: dict[str, Any] = {
            "pipeline_id": props.get("hs_pipeline"),
            "pipeline_stage_id": props.get("hs_pipeline_stage"),
            "owner_id": props.get("hubspot_owner_id"),
            "priority": props.get("hs_ticket_priority"),
            "created_at": props.get("createdate"),
            "last_modified_at": props.get("hs_lastmodifieddate"),
            "thread_ids": builder.thread_ids,
            "inbox_ids": sorted(set(builder.inbox_ids)),
            "threads_total": builder.threads_total,
            "threads_in_scope": builder.threads_in_scope,
            "resolved_truncated_message_ids": builder.resolved_truncated,
            "message_count": len(builder.messages),
        }

        return SupportCase(
            account_id=self.account_id,
            external_id=ticket_id,
            subject=str(props.get("subject") or ""),
            language=None,
            source_url=f"https://{self.ui_domain}/contacts/{self.account_id}/ticket/{ticket_id}",
            source_updated_at=builder.latest.isoformat() if builder.latest else None,
            complete=len(builder.incomplete_reasons) == 0,
            incomplete_reasons=builder.incomplete_reasons,
            messages=builder.messages,
            metadata=metadata,
        )

    async def _collect_threads(self, ticket_id: str, builder: _CaseBuilder) -> None:
        thread_params = {"associatedTicketId": ticket_id, "limit": _PAGE_LIMIT}
        async for thread in self._paginate("/conversations/v3/conversations/threads", thread_params):
            builder.threads_total += 1
            inbox_id = thread.get("inboxId")
            if self._inbox_ids and str(inbox_id) not in self._inbox_ids:
                builder.threads_excluded += 1
                continue
            builder.threads_in_scope += 1
            thread_id = str(thread.get("id"))
            builder.thread_ids.append(thread_id)
            if inbox_id is not None:
                builder.inbox_ids.append(str(inbox_id))
            await self._collect_messages(thread_id, builder)

    async def _collect_messages(self, thread_id: str, builder: _CaseBuilder) -> None:
        msg_params = {"limit": _PAGE_LIMIT}
        async for msg in self._paginate(f"/conversations/v3/conversations/threads/{thread_id}/messages", msg_params):
            msg_type = str(msg.get("type") or "MESSAGE")
            # WELCOME_MESSAGE is an automated greeting, not case evidence.
            if msg_type == "WELCOME_MESSAGE":
                continue
            raw_id = str(msg.get("id"))
            scoped = _scoped_id("message", raw_id)
            text = str(msg.get("text") or "")
            truncation = str(msg.get("truncationStatus") or "NOT_TRUNCATED")
            if truncation != "NOT_TRUNCATED":
                resolved = await self._original_content(thread_id, raw_id)
                if resolved is not None:
                    text = resolved
                    builder.resolved_truncated.append(scoped)
                else:
                    # Original content unavailable: keep the visible truncated
                    # text but never relabel it complete.
                    builder.incomplete_reasons.append(f"truncated_unresolved:{scoped}")
            elif not text:
                rich = str(msg.get("richText") or "")
                text = _html_to_text(rich) if rich else ""

            if msg.get("attachments"):
                builder.incomplete_reasons.append(f"unsupported_attachment:{scoped}")

            occurred_at = msg.get("createdAt")
            builder.note_time(occurred_at)
            senders: list[dict[str, Any]] = msg.get("senders") or []
            raw_channel = msg.get("channelId")
            channel_id = str(raw_channel) if raw_channel is not None else None
            # A COMMENT is an internal note, never a customer email/chat, so its
            # medium stays unknown regardless of the channel it rode in on. HubSpot
            # exposes no canonical reply pointer here, so reply_to_id stays None
            # rather than a chronological guess.
            medium = "unknown" if msg_type == "COMMENT" else _CHANNEL_MEDIUM.get(channel_id or "", "unknown")
            builder.messages.append(
                SupportMessage(
                    id=scoped,
                    kind="note" if msg_type == "COMMENT" else "message",
                    role=_role_from_actor(senders),
                    text=text,
                    occurred_at=occurred_at,
                    visibility="internal" if msg_type == "COMMENT" else "customer",
                    medium=medium,
                    channel_id=channel_id,
                    thread_id=thread_id,
                )
            )

    async def _original_content(self, thread_id: str, message_id: str) -> str | None:
        """Full body of a truncated message, or ``None`` if unavailable.

        The known-truncated ``richText`` from the message list is never used
        as a fallback: that would relabel truncated content as complete.
        """
        data = await self._get(
            f"/conversations/v3/conversations/threads/{thread_id}/messages/{message_id}/original-content"
        )
        text = str(data.get("text") or "")
        if text:
            return text
        rich = str(data.get("richText") or "")
        return _html_to_text(rich) if rich else None

    async def _collect_associated(self, ticket_id: str, object_type: str, builder: _CaseBuilder) -> None:
        assoc_path = f"/crm/v4/objects/tickets/{ticket_id}/associations/{object_type}"
        object_ids: list[str] = []
        async for assoc in self._paginate(assoc_path, {"limit": 500}):
            to_id = assoc.get("toObjectId")
            if to_id is not None:
                object_ids.append(str(to_id))

        for object_id in object_ids:
            if object_type == "notes":
                builder.messages.append(await self._fetch_note(object_id, builder))
            else:
                builder.messages.append(await self._fetch_email(object_id, builder))

    async def _fetch_note(self, note_id: str, builder: _CaseBuilder) -> SupportMessage:
        data = await self._get(
            f"/crm/v3/objects/notes/{note_id}",
            {"properties": "hs_note_body,hs_timestamp"},
        )
        props: dict[str, Any] = data.get("properties") or {}
        body = str(props.get("hs_note_body") or "")
        builder.note_time(props.get("hs_timestamp"))
        return SupportMessage(
            id=_scoped_id("note", note_id),
            kind="note",
            role="unknown",
            text=_html_to_text(body) if body else "",
            occurred_at=props.get("hs_timestamp"),
            visibility="internal",
        )

    async def _fetch_email(self, email_id: str, builder: _CaseBuilder) -> SupportMessage:
        data = await self._get(
            f"/crm/v3/objects/emails/{email_id}",
            {"properties": "hs_email_text,hs_email_html,hs_timestamp,hs_email_direction"},
        )
        props: dict[str, Any] = data.get("properties") or {}
        text = str(props.get("hs_email_text") or "")
        if not text:
            html = str(props.get("hs_email_html") or "")
            text = _html_to_text(html) if html else ""
        # Documented hs_email_direction only; any other value stays unknown.
        direction = str(props.get("hs_email_direction") or "")
        role = _EMAIL_ROLE.get(direction, "unknown")
        builder.note_time(props.get("hs_timestamp"))
        return SupportMessage(
            id=_scoped_id("email", email_id),
            kind="email",
            role=role,
            text=text,
            occurred_at=props.get("hs_timestamp"),
            visibility="customer",
            medium="email",
        )
