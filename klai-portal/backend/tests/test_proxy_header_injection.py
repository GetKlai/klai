"""SPEC-SEC-INTERNAL-001 REQ-3 / AC-3: BFF proxy strips client-supplied secret-bearing headers.

Pinned invariants:
- ``X-Internal-Secret`` and three sibling explicit names are stripped (AC-3.1).
- Regex catch-all matches ``X-Klai-Internal-*``, ``Internal-Auth-*``, ``Internal-Token-*`` (AC-3.2).
- Legitimate headers (X-Request-ID, X-Forwarded-For, etc.) pass through (AC-3.2).
- A blocked-injection attempt emits ``proxy_header_injection_blocked`` (AC-3.3).
- The Authorization header on the upstream request is the portal-injected Bearer
  token, never an inbound value (AC-3.4).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import structlog.testing


def _request_with_headers(headers: dict[str, str]) -> MagicMock:
    request = MagicMock()
    request.headers = headers  # _build_upstream_headers iterates .items()
    return request


def _session() -> SimpleNamespace:
    return SimpleNamespace(access_token="real-portal-bearer-token-99999")


# SPEC-SEC-IDENTITY-ASSERT-002 REQ-2.3: BFF verified-identity values used by
# every _build_upstream_headers test. The BFF supplies these from a verified
# decision; the test fixtures use stable placeholders.
_VERIFIED_KW = {
    "verified_user_id": "u-zitadel-1",
    "verified_org_id": "o-zitadel-1",
    "verified_org_slug": "voys",
}


def test_explicit_x_internal_secret_is_stripped():
    """AC-3.1: ``X-Internal-Secret`` from the client never reaches the upstream."""
    from app.api.proxy import _build_upstream_headers

    headers = _build_upstream_headers(
        _request_with_headers(
            {
                "X-Internal-Secret": "attacker-guess",
                "Accept": "application/json",
            },
        ),
        _session(),
        service="scribe",
        **_VERIFIED_KW,
    )
    # SPEC-SEC-IDENTITY-ASSERT-002 REQ-2.3: portal-api strips the inbound
    # value AND re-injects its own. The attacker's value never wins, even
    # though the header name is now present.
    assert headers["X-Internal-Secret"] != "attacker-guess"
    assert "attacker-guess" not in headers.values()
    assert headers.get("Accept") == "application/json"


def test_all_explicit_blocklist_names_are_stripped():
    """AC-3.1: each name in the explicit deny-list never reaches upstream
    with its CLIENT-supplied value. ``X-Internal-Secret`` is re-injected
    by portal-api with its own value (REQ-2.3); the other three sibling
    names are stripped without re-injection.
    """
    from app.api.proxy import _build_upstream_headers

    only_stripped = [
        "X-Klai-Internal-Secret",
        "X-Retrieval-Api-Internal-Secret",
        "X-Scribe-Api-Internal-Secret",
    ]
    inbound = {name: "attacker-attempt" for name in [*only_stripped, "X-Internal-Secret"]}
    inbound["X-Request-ID"] = "abc-123-good"

    headers = _build_upstream_headers(
        _request_with_headers(inbound),
        _session(),
        service="scribe",
        **_VERIFIED_KW,
    )
    lowered = {k.lower() for k in headers}
    for name in only_stripped:
        assert name.lower() not in lowered, f"{name} survived the strip"
    # X-Internal-Secret IS present but with portal's value, never the attacker's.
    assert headers["X-Internal-Secret"] != "attacker-attempt"
    assert "attacker-attempt" not in headers.values()
    assert headers.get("X-Request-ID") == "abc-123-good"


def test_regex_catch_all_blocks_forward_compatible_names():
    """AC-3.2: the regex catches future X-Klai-Internal-* and Internal-Auth-* / Internal-Token-* names."""
    from app.api.proxy import _build_upstream_headers

    inbound = {
        "X-Klai-Internal-Foo": "leak1",
        "X-Klai-Internal-Bar": "leak2",
        "Internal-Auth-Whatever": "leak3",
        "Internal-Token-Future": "leak4",
        "X-Custom-Business-Header": "OK",
    }
    headers = _build_upstream_headers(
        _request_with_headers(inbound),
        _session(),
        service="docs",
        **_VERIFIED_KW,
    )
    lowered = {k.lower() for k in headers}
    for blocked in ("x-klai-internal-foo", "x-klai-internal-bar", "internal-auth-whatever", "internal-token-future"):
        assert blocked not in lowered, f"{blocked} survived the regex catch-all"
    # Legitimate header passes through.
    assert headers.get("X-Custom-Business-Header") == "OK"


def test_legitimate_headers_pass_through_unchanged():
    """AC-3.2: ten known-good headers pass through untouched."""
    from app.api.proxy import _build_upstream_headers

    legitimate = {
        "X-Request-ID": "req-abc",
        "X-Forwarded-For": "203.0.113.10",
        "X-Real-IP": "203.0.113.10",
        "X-Custom-Business-Header": "biz",
        "Accept-Language": "nl",
        "User-Agent": "klai-portal-frontend/1.0",
        "Accept": "application/json",
        "X-CSRF-Token": "csrf-tok",  # not a *secret* header in the strict sense
        "X-Tenant-Slug": "getklai",
        "Content-Type": "application/json",
    }
    headers = _build_upstream_headers(
        _request_with_headers(legitimate),
        _session(),
        service="scribe",
        **_VERIFIED_KW,
    )
    for name, value in legitimate.items():
        assert headers.get(name) == value, f"{name} did not pass through"


def test_blocked_injection_emits_structlog_entry_without_value():
    """AC-3.3: ``proxy_header_injection_blocked`` is emitted; the value is never logged."""
    from app.api.proxy import _build_upstream_headers

    with structlog.testing.capture_logs() as logs:
        _build_upstream_headers(
            _request_with_headers(
                {
                    "X-Internal-Secret": "attacker-attempt-9999",
                    "Accept": "application/json",
                },
            ),
            _session(),
            service="scribe",
            **_VERIFIED_KW,
        )

    matches = [e for e in logs if e.get("event") == "proxy_header_injection_blocked"]
    assert len(matches) == 1, f"expected exactly one injection-blocked entry, got {logs}"
    entry = matches[0]
    assert entry["header"] == "x-internal-secret"
    assert entry["service"] == "scribe"
    # The value MUST NOT be logged anywhere on the entry.
    serialised = repr(entry)
    assert "attacker-attempt-9999" not in serialised


def test_authorization_is_portal_injected_not_inbound():
    """AC-3.4: portal-api always injects its own Bearer; inbound Authorization is dropped first."""
    from app.api.proxy import _build_upstream_headers

    headers = _build_upstream_headers(
        _request_with_headers(
            {
                "Authorization": "Bearer client-attempt-do-not-forward",
                "Accept": "application/json",
            },
        ),
        _session(),
        service="scribe",
        **_VERIFIED_KW,
    )
    # The hop-by-hop strip removes the inbound Authorization; portal injects its own.
    assert headers.get("Authorization") == "Bearer real-portal-bearer-token-99999"
