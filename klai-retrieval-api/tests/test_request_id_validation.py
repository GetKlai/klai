"""SPEC-SEC-HYGIENE-001 REQ-41 / AC-41: header validation for trace context.

``RequestContextMiddleware`` binds ``X-Request-ID`` and ``X-Org-ID`` from
incoming request headers into the structlog context. Without validation
an attacker can poison every log line with arbitrary bytes — terminal
escape sequences, HTML tags, multi-megabyte garbage. This test pins the
two regex contracts:

* REQ-41.1 — ``X-Request-ID`` must match ``^[A-Za-z0-9_-]{1,128}$``;
  invalid / missing / oversized values get a server-generated UUID.
* REQ-41.2 — ``X-Org-ID`` must match ``^[0-9]{1,20}$``; invalid values
  are *dropped* from the log context (not rejected at the HTTP layer,
  because the same header has multiple legitimate origins).

REQ-41.4 (apply the same regex cap symmetrically across portal-api,
connector, scribe, mailer, research-api, and knowledge-ingest) is
DEFERRED to a follow-up cross-service slice; this file covers
retrieval-api only.
"""

from __future__ import annotations

import re
import uuid

import pytest
import structlog
from starlette.requests import Request
from starlette.responses import Response

from retrieval_api.logging_setup import RequestContextMiddleware

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _make_request(headers: dict[str, str] | None) -> Request:
    """Build a minimal ASGI scope dict for ``Request`` with the given headers."""
    raw_headers: list[tuple[bytes, bytes]] = []
    for k, v in (headers or {}).items():
        raw_headers.append((k.lower().encode("latin-1"), v.encode("latin-1")))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/health",
        "headers": raw_headers,
        "query_string": b"",
    }
    return Request(scope=scope)


async def _noop_call_next(_request: Request) -> Response:
    return Response("ok")


async def _dispatch_and_capture_context(headers: dict[str, str] | None) -> dict:
    """Drive the middleware once; return the structlog contextvars seen at handler time."""
    middleware = RequestContextMiddleware(app=None)  # type: ignore[arg-type]
    captured: dict = {}

    async def _capture(_request: Request) -> Response:
        # Snapshot contextvars at the moment the inner handler would run —
        # that's what every downstream log call inside this request will see.
        captured.update(structlog.contextvars.get_contextvars())
        return Response("ok")

    await middleware.dispatch(_make_request(headers), _capture)
    return captured


# --------------------------------------------------------------------------- #
# REQ-41.1 — X-Request-ID validation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("inbound", "should_keep"),
    [
        ("abc-123", True),
        ("plain_id", True),
        ("A" * 128, True),                       # exactly at cap
        ("A" * 129, False),                      # over cap → UUID
        ("<script>", False),                     # invalid charset
        ("\x1b[31mred\x1b[0m", False),           # ANSI escape
        ("hello world", False),                  # space disallowed
        ("rid;rm -rf /", False),                 # shell metacharacters
    ],
    ids=[
        "kebab-id",
        "snake-id",
        "exactly-128-A",
        "129-A-overflow",
        "html-tag",
        "ansi-escape",
        "space",
        "shell-injection",
    ],
)
async def test_request_id_validation(inbound: str, should_keep: bool):
    """REQ-41.1: keep valid id verbatim, replace invalid/oversized with a UUID."""
    ctx = await _dispatch_and_capture_context({"x-request-id": inbound})

    request_id = ctx.get("request_id")
    assert request_id, "RequestContextMiddleware must always bind `request_id`"

    if should_keep:
        assert request_id == inbound, (
            f"X-Request-ID {inbound!r} is valid (matches "
            f"^[A-Za-z0-9_-]{{1,128}}$) and must be preserved verbatim, "
            f"got {request_id!r}"
        )
    else:
        assert request_id != inbound, (
            f"X-Request-ID {inbound!r} fails the regex / length cap and "
            f"must be replaced — got {request_id!r}"
        )
        assert _UUID_RE.match(request_id), (
            f"Replacement `request_id` is not a UUID: {request_id!r}"
        )


async def test_request_id_oversized_garbage_does_not_pollute_context():
    """REQ-41.3: 10KB of garbage in X-Request-ID → server-generated UUID."""
    garbage = "A" * 10_000
    ctx = await _dispatch_and_capture_context({"x-request-id": garbage})
    assert ctx["request_id"] != garbage
    assert _UUID_RE.match(ctx["request_id"]), (
        f"Oversized X-Request-ID was not replaced with a UUID: {ctx['request_id']!r}"
    )


@pytest.mark.parametrize("missing_value", ["", None], ids=["empty", "absent"])
async def test_request_id_missing_or_empty_gets_uuid(missing_value: str | None):
    """REQ-41.1: empty / absent X-Request-ID → server-generated UUID."""
    headers = {"x-request-id": missing_value} if missing_value is not None else {}
    ctx = await _dispatch_and_capture_context(headers)
    assert _UUID_RE.match(ctx["request_id"]), (
        f"Missing/empty X-Request-ID did not fall back to a UUID: {ctx['request_id']!r}"
    )


# --------------------------------------------------------------------------- #
# REQ-41.2 — X-Org-ID validation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("inbound", "should_keep"),
    [
        ("42", True),
        ("0", True),
        ("9" * 20, True),                        # exactly at cap
        ("1" * 22, False),                       # over cap → drop
        ("abc", False),                          # non-numeric
        ("-5", False),                           # negative sign
        ("3.14", False),                         # decimal
        ("12 34", False),                        # space
        ("<svg>", False),                        # HTML tag
    ],
    ids=[
        "small-int",
        "zero",
        "exactly-20-digits",
        "22-digit-overflow",
        "alpha",
        "negative",
        "float",
        "space",
        "html-tag",
    ],
)
async def test_org_id_validation(inbound: str, should_keep: bool):
    """REQ-41.2: numeric X-Org-ID kept verbatim, anything else dropped."""
    ctx = await _dispatch_and_capture_context({"x-org-id": inbound})

    if should_keep:
        assert ctx.get("org_id") == inbound, (
            f"X-Org-ID {inbound!r} is valid (matches ^[0-9]{{1,20}}$) and "
            f"must be preserved verbatim, got {ctx.get('org_id')!r}"
        )
    else:
        assert "org_id" not in ctx, (
            f"X-Org-ID {inbound!r} fails the regex / length cap and must be "
            f"DROPPED from log context entirely (not rejected at HTTP layer). "
            f"Found bound value: {ctx.get('org_id')!r}"
        )


def test_uuid_replacement_uses_uuid4():
    """Sanity: ``uuid.uuid4()`` produces strings matching our regex."""
    assert _UUID_RE.match(str(uuid.uuid4()))
