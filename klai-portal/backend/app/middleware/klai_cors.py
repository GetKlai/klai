"""KlaiCORSMiddleware — SPEC-SEC-CORS-001 REQ-1.

Subclasses Starlette's CORSMiddleware so we lean on its battle-tested
preflight + response-header logic, and add only the two pieces the SPEC
demands on top:

- AC-13: emit a structlog ``cors_origin_rejected`` event whenever a
  cross-origin request from a non-allowlisted origin is observed (both
  preflight AND simple/actual cross-origin requests).
- AC-14: fail-closed startup if the hardcoded first-party regex string
  fails to compile. Matches the ``_require_vexa_webhook_secret`` pattern
  in ``app/core/config.py``.

Origin policy (REQ-1.2):
    allowlist = settings.cors_origins_list (explicit list)  UNION
                _FIRST_PARTY_ORIGIN_PATTERN  (compiled regex)

The regex is hardcoded in this module. It is NOT loaded from settings —
``cors_allow_origin_regex`` was removed from config.py per REQ-1.6 to
prevent operators from re-introducing the audit-flagged ``r".*"`` knob.

@MX:NOTE: This is a thin subclass — Starlette CORSMiddleware does the
heavy lifting (preflight responses, header construction, simple-request
header injection, fullmatch on the regex). We only override __call__ to
hook in observability before delegating, never duplicate Starlette's
header logic.

@MX:NOTE: The widget-side equivalent helper is
``app.services.widget_auth.origin_allowed`` (wildcard pattern matching,
e.g. ``https://*.customer.com``). It is intentionally separate — different
algorithm for a different problem (per-widget partner allowlists vs
first-party portal allowlist). Do not consolidate without a SPEC: the
shapes diverge enough that a single helper would parameter-sprawl.

@MX:NOTE: This middleware holds NO widget paths. The widget bundle runs on
the customer's own site, so the first-party allowlist cannot apply there;
each widget route declares itself via the marker in
``app.api.widget_public`` and the middleware reads that from the app's
resolved route tree — routers wrapped by ``include_router`` and ``Mount``
containers included (see ``_widget_cors_mode``). Adding a widget endpoint
means marking its route in ``app/api/partner.py`` — never editing this file.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import Response
from starlette.routing import BaseRoute, Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.widget_public import MODE_ROUTE_DECIDES, widget_cors_mode_of

# The fixed first-party origin pattern. Exposed as a module constant so the
# AC-14 test can monkeypatch it before re-invoking _compile_first_party_regex().
#
# Matches:
#   https://getklai.com
#   https://my.getklai.com
#   https://acme.getklai.com
#   ... any single-label LDH subdomain of getklai.com
#
# Does NOT match:
#   http://getklai.com                       (no plaintext)
#   https://evil.my.getklai.com              (multi-label blocked)
#   https://evil.getklai.com.attacker.tld    (not the getklai.com TLD)
_FIRST_PARTY_ORIGIN_PATTERN = r"^https://([a-z0-9][a-z0-9-]*\.)?getklai\.com$"

logger = structlog.get_logger()
_startup_logger = logging.getLogger(__name__)


# @MX:ANCHOR: Startup invariant — portal-api refuses to boot if the
# first-party regex fails to compile (AC-14, NFR Fail mode). Mirrors
# _require_vexa_webhook_secret in config.py: programmer error must fail
# fast at startup, not silently downgrade CORS behaviour.
def _compile_first_party_regex() -> re.Pattern[str]:
    """Compile _FIRST_PARTY_ORIGIN_PATTERN; SystemExit(1) on failure (AC-14).

    Called once at module load. May be re-called by tests after
    monkeypatching _FIRST_PARTY_ORIGIN_PATTERN to verify the fail-closed
    behaviour.
    """
    try:
        return re.compile(_FIRST_PARTY_ORIGIN_PATTERN)
    except re.error as exc:
        _startup_logger.critical(
            "CORS origin regex failed to compile: %s — portal-api cannot start safely.",
            exc,
        )
        raise SystemExit(1) from exc


# Module-load-time validation. The compiled pattern is not stored — Starlette's
# CORSMiddleware compiles its own copy from the string at __init__ time. The
# call is here purely to fail-closed if the pattern is malformed (AC-14).
_compile_first_party_regex()

# One route-table entry: (leaf route, Mount ancestors to replay, declared
# widget mode or None when the leaf did not declare one).
_WidgetEntry = tuple[Any, tuple[BaseRoute, ...], str | None]


# @MX:NOTE: Regression (PR #1361 follow-up): app.include_router() on FastAPI
# 0.141+ puts an _IncludedRouter in app.routes and the APIRoute objects sit
# INSIDE it; routes under a Starlette Mount sit inside that. A flat scan of
# app.routes therefore saw no marked route at all and 400'd every widget
# preflight from a customer domain although the whole test suite stayed
# green (its fixture registered routes directly on FastAPI). Flatten the
# tree the way the router walks it, and resolve matches per leaf.
def _collect_widget_cors_entries(routes: Iterable[Any], ancestors: tuple[BaseRoute, ...] = ()) -> list[_WidgetEntry]:
    """Flatten the route tree into router-visit-order entries.

    - FastAPI's ``_IncludedRouter`` (what ``include_router`` appends since
      0.141) exposes its matching list as ``effective_candidates()``; those
      ``_EffectiveRouteContext`` leaves carry the route's ``openapi_extra``
      and already match the fully effective path, so no scope replay is
      needed for them.
    - ``Mount``/``Host`` rewrite the path when dispatching, so leaves below
      them record the mount chain as ancestors and replay the rewrite in
      ``_entry_match``. A mount's children live on ``.routes``, on the
      ``.routes`` of a whole ASGI app it was mounted with, or — when the
      mount sits inside an ``include_router`` wrapper — on the effective
      context's ``starlette_route``.
    """
    entries: list[_WidgetEntry] = []
    for route in routes:
        effective = getattr(route, "effective_candidates", None)
        if effective is not None:
            entries.extend(_collect_widget_cors_entries(effective(), ancestors))
            continue
        child_routes = _child_routes(route)
        if isinstance(child_routes, Iterable) and child_routes:
            entries.extend(_collect_widget_cors_entries(child_routes, (*ancestors, route)))
            continue
        entries.append((route, ancestors, widget_cors_mode_of(route)))
    return entries


def _child_routes(route: Any) -> Any:
    """The route's nested routes when it is a container, else ``None``. Duck-
    typed on purpose: Mount/Router expose ``.routes``; a mount of a whole ASGI
    app exposes them via ``.app``; a FastAPI effective context wrapping a Mount
    via ``.starlette_route``. Leaves (APIRoute, Route, WebSocketRoute, plain
    contexts) have none of those as route collections."""
    children = getattr(route, "routes", None) or getattr(getattr(route, "app", None), "routes", None)
    if children is None:
        inner = getattr(route, "starlette_route", None)
        if inner is not None:
            children = getattr(inner, "routes", None) or getattr(getattr(inner, "app", None), "routes", None)
    return children


def _entry_match(entry: _WidgetEntry, scope: Scope) -> Match:
    leaf, ancestors, _ = entry
    for mount in ancestors:
        match, child_scope = mount.matches(scope)
        if match is Match.NONE:
            return Match.NONE
        scope = {**scope, **child_scope}
    return leaf.matches(scope)[0]


class KlaiCORSMiddleware(CORSMiddleware):
    """First-party CORS middleware with rejection observability.

    Inherits all preflight + simple-response header logic from Starlette's
    CORSMiddleware. Adds:

    - REQ-1.2 origin policy: ``allow_origin_regex`` is hardcoded to the
      first-party pattern (cannot be overridden from settings, REQ-1.6).
    - AC-13 observability: logs ``cors_origin_rejected`` on any cross-origin
      request whose Origin is not in the allowlist (both preflights and
      simple/actual requests).

    Parameters
    ----------
    app:
        The ASGI application to wrap.
    cors_origins:
        Pre-parsed list of explicit allowed origins (typically
        ``settings.cors_origins_list``). The first-party
        ``*.getklai.com`` origins are handled by the hardcoded regex
        independently of this list.
    allow_credentials:
        When True, sets ``Access-Control-Allow-Credentials: true`` only
        for allowlisted origins (REQ-1.5; Starlette enforces this).
    allow_methods:
        List of allowed HTTP methods (default: ``["*"]`` — Starlette
        expands ``*`` to ``ALL_METHODS``).
    allow_headers:
        List of allowed request headers (default: ``["*"]`` — Starlette
        mirrors back the requested headers).

    Notes
    -----
    No ``**kwargs`` catch-all: passing an unknown keyword raises
    ``TypeError`` at construction time, which prevents the silent-discard
    footgun that would let e.g. ``expose_headers=...`` be lost without
    warning.
    """

    def __init__(
        self,
        app: ASGIApp,
        cors_origins: list[str] | None = None,
        allow_credentials: bool = True,
        allow_methods: list[str] | None = None,
        allow_headers: list[str] | None = None,
    ) -> None:
        super().__init__(
            app=app,
            allow_origins=tuple(cors_origins) if cors_origins else (),
            allow_origin_regex=_FIRST_PARTY_ORIGIN_PATTERN,
            allow_credentials=allow_credentials,
            allow_methods=tuple(allow_methods) if allow_methods else ("*",),
            allow_headers=tuple(allow_headers) if allow_headers else ("*",),
        )
        # Route table flattened once per instance on the first
        # cross-origin request (full entries + the marked subset); the walk
        # mirrors routing, so re-running it per request would double the
        # hot path. See _widget_cors_mode for the staleness contract.
        self._widget_cors_table: tuple[list[_WidgetEntry], list[_WidgetEntry]] | None = None

    def _widget_cors_mode(self, scope: Scope, headers: Headers) -> str | None:
        """Widget CORS mode for this request, learned from the routes that
        carry the ``app.api.widget_public`` marker — this module knows no
        paths. ``None`` when no marked route is involved at all (first-party
        routes, and paths matching no registered route, so a near-miss on a
        widget path stays on the first-party policy) and when the winning
        route did not declare a marker.

        Matching mirrors Starlette's own routing, including ``include_router``
        wrappers and Mounts (see _collect_widget_cors_entries):
        - a preflight is resolved with ``Access-Control-Request-Method`` as
          the method — a preflight probing a POST route is decided by that
          POST route, never by a marked GET registered on the same path;
        - a FULL match wins and the FIRST PARTIAL applies only when no FULL
          exists anywhere, so a marked route the real request would 405 on
          cannot absorb an unmarked route the router would actually reach.

        @MX:NOTE: Staleness contract — the flattened table is built once, on
        the first cross-origin request. klai-portal registers every router at
        import time in main.py, so routes added after that first request are
        invisible here until the process restarts. Tests must therefore build
        the complete app before its first request (all fixtures do).
        """
        table = self._widget_cors_table
        if table is None:
            entries = _collect_widget_cors_entries(getattr(scope.get("app"), "routes", ()))
            table = (entries, [entry for entry in entries if entry[2] is not None])
            self._widget_cors_table = table
        entries, marked = table

        lookup: Scope = scope
        if scope.get("method") == "OPTIONS" and "access-control-request-method" in headers:
            lookup = {**scope, "method": headers["access-control-request-method"]}

        # Cheap prefilter: only requests that hit a marked route at all pay
        # for the full winner scan (the marked subset is a handful of
        # regexes; the full scan is one routing pass over the whole table).
        if not any(_entry_match(entry, lookup) is not Match.NONE for entry in marked):
            return None

        partial: _WidgetEntry | None = None
        for entry in entries:
            match = _entry_match(entry, lookup)
            if match is Match.FULL:
                return entry[2]
            if match is Match.PARTIAL and partial is None:
                partial = entry
        return partial[2] if partial is not None else None

    # @MX:NOTE: Observability hook — log every rejected cross-origin request
    # exactly once. The actual header behaviour (preflight 400, simple 200
    # without ACAO) is delegated unchanged to the parent class.
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await super().__call__(scope, receive, send)
            return

        headers = Headers(scope=scope)
        origin = headers.get("origin")

        if origin:
            widget_mode = self._widget_cors_mode(scope, headers)
            if widget_mode is not None:
                await self._widget_cors(scope, receive, send, origin=origin, headers=headers, mode=widget_mode)
                return

            if not self.is_allowed_origin(origin):
                method = scope.get("method", "")
                is_preflight = method == "OPTIONS" and "access-control-request-method" in headers
                # Truncate both attacker-controlled fields to bounded lengths to
                # prevent log-bloat. UUIDs are 36 chars; 64 is generous headroom.
                logger.info(
                    "cors_origin_rejected",
                    origin=origin[:256],
                    path=scope.get("path", ""),
                    request_id=(headers.get("x-request-id") or "unknown")[:64],
                    kind="preflight" if is_preflight else "simple",
                )

        await super().__call__(scope, receive, send)

    async def _widget_cors(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        origin: str,
        headers: Headers,
        mode: str,
    ) -> None:
        """CORS for the public widget endpoints: echo the origin, no credentials."""
        if mode == MODE_ROUTE_DECIDES:
            # The route decides per widget allowlist and sets ACAO itself,
            # on the preflight as well as on the actual request (403 without
            # ACAO when the origin is not allowed) — see
            # WIDGET_ROUTE_DECIDES_CORS in app/api/widget_public.py.
            await self.app(scope, receive, send)
            return
        if scope.get("method") == "OPTIONS" and "access-control-request-method" in headers:
            response = Response(
                status_code=204,
                headers={
                    "Access-Control-Allow-Origin": origin,
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                    # Mirror what the browser asks for (Starlette does the same
                    # for allow_headers="*"): the SSE client adds Last-Event-ID
                    # on reconnect, the widget its session id and bearer token.
                    "Access-Control-Allow-Headers": headers.get("access-control-request-headers", "*"),
                    "Access-Control-Max-Age": "600",
                    "Vary": "Origin",
                },
            )
            await response(scope, receive, send)
            return

        async def send_with_origin(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                response_headers = MutableHeaders(scope=message)
                response_headers.setdefault("Access-Control-Allow-Origin", origin)
                if "access-control-allow-credentials" in response_headers:
                    del response_headers["access-control-allow-credentials"]
                response_headers.add_vary_header("Origin")
            await send(message)

        await self.app(scope, receive, send_with_origin)

    # @MX:NOTE: REQ-1.5 enforcement — Starlette's CORSMiddleware always sets
    # Access-Control-Allow-Credentials: true when allow_credentials=True,
    # even on responses for non-allowlisted origins. The audit explicitly
    # forbids this (REQ-1.5: "ACAC SHALL only be set on responses for
    # allowlisted first-party origins"). We strip ACAC from rejected
    # preflights here so browsers see a clean 400 without signalling that
    # credentials are accepted.
    def preflight_response(self, request_headers: Headers) -> Response:
        response = super().preflight_response(request_headers)
        origin = request_headers.get("origin", "")
        if origin and not self.is_allowed_origin(origin):
            if "access-control-allow-credentials" in response.headers:
                del response.headers["access-control-allow-credentials"]
        return response

    # @MX:NOTE: REQ-1.5 enforcement for simple/actual cross-origin requests.
    # Starlette's parent send() unconditionally writes simple_headers (which
    # includes ACAC=true when allow_credentials=True) into every response,
    # only conditionally adding ACAO. For rejected origins we want neither
    # ACAO nor ACAC — only Vary: Origin so caches key correctly. We bypass
    # the parent's header pipeline on rejection and only emit Vary.
    async def send(
        self,
        message: Message,
        send: Send,
        request_headers: Headers,
    ) -> None:
        if message["type"] != "http.response.start":
            await send(message)
            return

        # Starlette Headers is case-insensitive — single lookup suffices.
        origin = request_headers.get("origin", "")
        if not origin or not self.is_allowed_origin(origin):
            message.setdefault("headers", [])
            headers = MutableHeaders(scope=message)
            headers.add_vary_header("Origin")
            await send(message)
            return

        await super().send(message, send=send, request_headers=request_headers)
