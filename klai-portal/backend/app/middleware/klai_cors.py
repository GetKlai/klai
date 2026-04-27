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
"""

from __future__ import annotations

import logging
import re

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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

    # @MX:NOTE: Observability hook — log every rejected cross-origin request
    # exactly once. The actual header behaviour (preflight 400, simple 200
    # without ACAO) is delegated unchanged to the parent class.
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await super().__call__(scope, receive, send)
            return

        headers = Headers(scope=scope)
        origin = headers.get("origin")

        if origin and not self.is_allowed_origin(origin):
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
