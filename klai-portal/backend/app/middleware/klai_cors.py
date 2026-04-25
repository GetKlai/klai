"""KlaiCORSMiddleware — SPEC-SEC-CORS-001 REQ-1.

Replaces the stock Starlette CORSMiddleware for portal-api.

Changes from stock CORSMiddleware:
- Removes the user-tunable cors_allow_origin_regex setting (REQ-1.6).
- Uses a hardcoded first-party regex (REQ-1.2) combined with cors_origins_list
  as the allowlist for credentialed requests.
- Sets Access-Control-Allow-Credentials: true ONLY for allowlisted origins (REQ-1.5).
- Emits a structlog event at info level when a preflight is rejected (AC-13).
- Startup fails closed if the regex fails to compile (AC-14).

The regex is hardcoded at module load.  If it ever fails to compile (programmer
error), _compile_first_party_regex() raises SystemExit(1) with a critical log
line — consistent with the _require_vexa_webhook_secret pattern in config.py.
"""

from __future__ import annotations

import functools
import logging
import re

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# The fixed first-party origin pattern.  This string is exposed as a module
# constant so that AC-14's test can monkeypatch it before calling
# _compile_first_party_regex().
#
# Matches:
#   https://getklai.com
#   https://my.getklai.com
#   https://acme.getklai.com
#   ... any single-label LDH subdomain of getklai.com
#
# Does NOT match:
#   http://getklai.com       (no plaintext)
#   https://evil.my.getklai.com   (multi-label blocked)
#   https://evil.getklai.com.attacker.tld  (not the getklai.com TLD)
_FIRST_PARTY_ORIGIN_PATTERN = r"^https://([a-z0-9][a-z0-9-]*\.)?getklai\.com$"

logger = structlog.get_logger()
_startup_logger = logging.getLogger(__name__)

# All HTTP methods Starlette CORSMiddleware normally allows with "*"
_ALL_METHODS = (
    "DELETE",
    "GET",
    "HEAD",
    "OPTIONS",
    "PATCH",
    "POST",
    "PUT",
)


def _compile_first_party_regex() -> re.Pattern[str]:
    """Compile _FIRST_PARTY_ORIGIN_PATTERN; exit(1) on failure (AC-14).

    Called once at module load.  May be called again by tests after
    monkeypatching _FIRST_PARTY_ORIGIN_PATTERN to restore state.
    """
    try:
        return re.compile(_FIRST_PARTY_ORIGIN_PATTERN)
    except re.error as exc:
        _startup_logger.critical(
            "CORS origin regex failed to compile: %s — portal-api cannot start safely.",
            exc,
        )
        raise SystemExit(1) from exc


# Compiled once at module load; fail-closed if the pattern is bad (AC-14).
_FIRST_PARTY_ORIGIN_RE: re.Pattern[str] = _compile_first_party_regex()


def _is_first_party_origin(origin: str, cors_origins_list: list[str]) -> bool:
    """Return True when origin matches the allowlist or the first-party regex.

    REQ-1.2: allowlist = cors_origins_list UNION first-party regex.
    """
    if origin in cors_origins_list:
        return True
    return bool(_FIRST_PARTY_ORIGIN_RE.match(origin))


class KlaiCORSMiddleware:
    """CORS middleware with a hardcoded first-party regex and rejection logging.

    Implements the same interface as Starlette CORSMiddleware but:
    - Origin matching uses _is_first_party_origin() (REQ-1.2).
    - ACAC is set only when the origin is allowed (REQ-1.5).
    - Rejected preflights emit a structlog event (AC-13).

    Parameters
    ----------
    app:
        The ASGI application to wrap.
    cors_origins:
        Pre-parsed list of explicit allowed origins (typically
        ``settings.cors_origins_list``).  Used for explicit dev/staging origins
        such as ``http://localhost:5174``.  The first-party ``*.getklai.com``
        origins are handled by the hardcoded regex independently of this list.
    allow_credentials:
        When True, sets ACAC only for allowlisted origins (REQ-1.5).
    allow_methods:
        List of allowed HTTP methods (default: all standard methods).
    allow_headers:
        List of allowed request headers (default: all headers with ``*``).

    Notes
    -----
    No ``**kwargs`` catch-all is accepted: passing an unknown keyword raises
    ``TypeError`` at construction time, which prevents the silent-discard
    footgun that would let e.g. ``expose_headers=...`` be lost without warning.
    """

    def __init__(
        self,
        app: ASGIApp,
        cors_origins: list[str] | None = None,
        allow_credentials: bool = True,
        allow_methods: list[str] | None = None,
        allow_headers: list[str] | None = None,
    ) -> None:
        self.app = app
        self._cors_origins_list: list[str] = list(cors_origins) if cors_origins else []
        self._allow_credentials = allow_credentials
        self._allow_methods: tuple[str, ...] = (
            tuple(allow_methods) if allow_methods and allow_methods != ["*"] else _ALL_METHODS
        )
        # Starlette safe-listed headers always allowed; "*" means mirror back
        self._allow_all_headers = (allow_headers is None) or (allow_headers == ["*"])
        self._allow_headers: list[str] = [] if self._allow_all_headers else (allow_headers or [])

    def _is_allowed(self, origin: str) -> bool:
        return _is_first_party_origin(origin, self._cors_origins_list)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        origin = headers.get("origin")

        if origin is None:
            await self.app(scope, receive, send)
            return

        method = scope["method"]
        is_preflight = (
            method == "OPTIONS"
            and "access-control-request-method" in headers
        )

        if is_preflight:
            # Log rejected preflights (AC-13)
            if not self._is_allowed(origin):
                path = scope.get("path", "")
                request_id = headers.get("x-request-id") or "unknown"
                sanitised_origin = origin[:256]
                logger.info(
                    "cors_origin_rejected",
                    origin=sanitised_origin,
                    path=path,
                    request_id=request_id,
                )
            response = self._preflight_response(headers)
            await response(scope, receive, send)
            return

        # Simple / actual request: wrap send to add CORS headers on the response
        send = functools.partial(self._send, send=send, origin=origin)
        await self.app(scope, receive, send)

    def _preflight_response(self, request_headers: Headers) -> Response:
        """Build the OPTIONS preflight response."""
        origin = request_headers["origin"]
        requested_method = request_headers.get("access-control-request-method", "")
        requested_headers_str = request_headers.get("access-control-request-headers")

        resp_headers: dict[str, str] = {"Vary": "Origin"}
        failures = []

        if self._is_allowed(origin):
            resp_headers["Access-Control-Allow-Origin"] = origin
            if self._allow_credentials:
                resp_headers["Access-Control-Allow-Credentials"] = "true"
        else:
            failures.append("origin")

        if requested_method and requested_method not in self._allow_methods:
            failures.append("method")

        if self._allow_all_headers and requested_headers_str is not None:
            resp_headers["Access-Control-Allow-Headers"] = requested_headers_str
        elif requested_headers_str is not None:
            for h in requested_headers_str.split(","):
                if h.strip().lower() not in self._allow_headers:
                    failures.append("headers")
                    break

        resp_headers["Access-Control-Allow-Methods"] = ", ".join(self._allow_methods)
        resp_headers["Access-Control-Max-Age"] = "600"

        if failures:
            return PlainTextResponse(
                "Disallowed CORS " + ", ".join(failures),
                status_code=400,
                headers=resp_headers,
            )

        return PlainTextResponse("OK", status_code=200, headers=resp_headers)

    async def _send(
        self,
        message: Message,
        send: Send,
        origin: str,
    ) -> None:
        """Intercept response.start to add CORS headers for simple requests."""
        if message["type"] != "http.response.start":
            await send(message)
            return

        headers = MutableHeaders(scope=message)
        headers.append("Vary", "Origin")

        if self._is_allowed(origin):
            headers.append("Access-Control-Allow-Origin", origin)
            if self._allow_credentials:
                headers.append("Access-Control-Allow-Credentials", "true")

        await send(message)
