"""Tests for IdentityAsserter end-to-end behaviour with mocked portal."""

from __future__ import annotations

from typing import cast

import httpx
import pytest

from klai_identity_assert import (
    IdentityAsserter,
    IdentityDenied,
    PortalUnreachable,
    VerifyResult,
)

# Test-only constants. The ``portal_url`` / ``internal_secret`` fixtures in
# conftest.py mirror these — kept in sync deliberately so the asserter fixture
# and the ad-hoc transports built here use the same wire-level values.
PORTAL_URL = "http://portal-api:8000"
INTERNAL_SECRET = "test-internal-secret"  # noqa: S105


def _mock_portal(
    *,
    status_code: int = 200,
    body: dict[str, object] | None = None,
    capture: dict[str, object] | None = None,
) -> httpx.MockTransport:
    """Build an httpx MockTransport that returns a canned portal response.

    ``capture`` (when provided) records the last request's headers and body
    so tests can assert on what was actually sent over the wire.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture["url"] = str(request.url)
            capture["headers"] = dict(request.headers)
            capture["body"] = request.read().decode("utf-8")
        return httpx.Response(status_code=status_code, json=body or {})

    return httpx.MockTransport(handler)


async def _build_asserter(transport: httpx.MockTransport) -> IdentityAsserter:
    client = httpx.AsyncClient(transport=transport)
    return IdentityAsserter(
        portal_base_url=PORTAL_URL,
        internal_secret=INTERNAL_SECRET,
        http_client=client,
        cache_ttl_seconds=60.0,
    )


async def test_verify_returns_allow_on_jwt_evidence(fake_user_id: str, fake_org_id: str) -> None:
    transport = _mock_portal(
        body={
            "verified": True,
            "user_id": fake_user_id,
            "org_id": fake_org_id,
            "cache_ttl_seconds": 60,
            "evidence": "jwt",
        },
    )
    asserter = await _build_asserter(transport)

    result = await asserter.verify(
        caller_service="scribe",
        claimed_user_id=fake_user_id,
        claimed_org_id=fake_org_id,
        bearer_jwt="some.jwt.value",
    )

    assert result.verified is True
    assert result.user_id == fake_user_id
    assert result.org_id == fake_org_id
    assert result.evidence == "jwt"
    assert result.cached is False
    await asserter.aclose()


async def test_verify_returns_allow_on_membership_evidence(
    fake_user_id: str, fake_org_id: str
) -> None:
    transport = _mock_portal(
        body={
            "verified": True,
            "user_id": fake_user_id,
            "org_id": fake_org_id,
            "cache_ttl_seconds": 60,
            "evidence": "membership",
        },
    )
    asserter = await _build_asserter(transport)

    result = await asserter.verify(
        caller_service="knowledge-mcp",
        claimed_user_id=fake_user_id,
        claimed_org_id=fake_org_id,
        bearer_jwt=None,
    )

    assert result.verified is True
    assert result.evidence == "membership"
    await asserter.aclose()


async def test_verify_returns_deny_on_no_membership(
    fake_user_id: str, other_org_id: str
) -> None:
    transport = _mock_portal(
        status_code=403,
        body={"verified": False, "reason": "no_membership"},
    )
    asserter = await _build_asserter(transport)

    result = await asserter.verify(
        caller_service="scribe",
        claimed_user_id=fake_user_id,
        claimed_org_id=other_org_id,
        bearer_jwt=None,
    )

    assert result.verified is False
    assert result.reason == "no_membership"
    assert result.user_id is None
    assert result.org_id is None
    await asserter.aclose()


async def test_verify_returns_deny_on_jwt_identity_mismatch(
    fake_user_id: str, fake_org_id: str
) -> None:
    transport = _mock_portal(
        status_code=403,
        body={"verified": False, "reason": "jwt_identity_mismatch"},
    )
    asserter = await _build_asserter(transport)

    result = await asserter.verify(
        caller_service="scribe",
        claimed_user_id=fake_user_id,
        claimed_org_id=fake_org_id,
        bearer_jwt="forged.or.expired.jwt",
    )

    assert result.verified is False
    assert result.reason == "jwt_identity_mismatch"
    await asserter.aclose()


async def test_verify_fails_closed_on_network_error(
    fake_user_id: str, fake_org_id: str
) -> None:
    def fail(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("portal down")

    transport = httpx.MockTransport(fail)
    asserter = await _build_asserter(transport)

    result = await asserter.verify(
        caller_service="scribe",
        claimed_user_id=fake_user_id,
        claimed_org_id=fake_org_id,
        bearer_jwt=None,
    )

    assert result.verified is False
    assert result.reason == "portal_unreachable"
    await asserter.aclose()


async def test_verify_fails_closed_on_5xx(fake_user_id: str, fake_org_id: str) -> None:
    transport = _mock_portal(status_code=503, body={"detail": "redis down"})
    asserter = await _build_asserter(transport)

    result = await asserter.verify(
        caller_service="scribe",
        claimed_user_id=fake_user_id,
        claimed_org_id=fake_org_id,
        bearer_jwt=None,
    )

    assert result.verified is False
    assert result.reason == "portal_unreachable"
    await asserter.aclose()


async def test_verify_fails_closed_on_unknown_caller_service(
    fake_user_id: str, fake_org_id: str
) -> None:
    transport = _mock_portal(body={"verified": True})
    asserter = await _build_asserter(transport)

    result = await asserter.verify(
        caller_service="unknown-service",
        claimed_user_id=fake_user_id,
        claimed_org_id=fake_org_id,
        bearer_jwt=None,
    )

    assert result.verified is False
    assert result.reason == "library_misconfigured"
    await asserter.aclose()


async def test_verify_fails_closed_on_malformed_json(
    fake_user_id: str, fake_org_id: str
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    transport = httpx.MockTransport(handler)
    asserter = await _build_asserter(transport)

    result = await asserter.verify(
        caller_service="scribe",
        claimed_user_id=fake_user_id,
        claimed_org_id=fake_org_id,
        bearer_jwt=None,
    )

    assert result.verified is False
    assert result.reason == "portal_unreachable"
    await asserter.aclose()


async def test_verify_caches_allow_results(
    fake_user_id: str, fake_org_id: str
) -> None:
    call_count = {"value": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        call_count["value"] += 1
        return httpx.Response(
            200,
            json={
                "verified": True,
                "user_id": fake_user_id,
                "org_id": fake_org_id,
                "cache_ttl_seconds": 60,
                "evidence": "jwt",
            },
        )

    transport = httpx.MockTransport(handler)
    asserter = await _build_asserter(transport)

    first = await asserter.verify(
        caller_service="scribe",
        claimed_user_id=fake_user_id,
        claimed_org_id=fake_org_id,
        bearer_jwt="jwt-1",
    )
    second = await asserter.verify(
        caller_service="scribe",
        claimed_user_id=fake_user_id,
        claimed_org_id=fake_org_id,
        bearer_jwt="jwt-1",
    )

    assert call_count["value"] == 1, "second call should hit cache, not portal"
    assert first.cached is False
    assert second.cached is True
    assert second.user_id == fake_user_id
    await asserter.aclose()


async def test_verify_does_not_cache_denials(fake_user_id: str, fake_org_id: str) -> None:
    call_count = {"value": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        call_count["value"] += 1
        return httpx.Response(403, json={"verified": False, "reason": "no_membership"})

    transport = httpx.MockTransport(handler)
    asserter = await _build_asserter(transport)

    await asserter.verify(
        caller_service="scribe",
        claimed_user_id=fake_user_id,
        claimed_org_id=fake_org_id,
        bearer_jwt=None,
    )
    await asserter.verify(
        caller_service="scribe",
        claimed_user_id=fake_user_id,
        claimed_org_id=fake_org_id,
        bearer_jwt=None,
    )

    assert call_count["value"] == 2, "denials must re-query portal — no negative cache"
    await asserter.aclose()


async def test_verify_propagates_x_request_id(fake_user_id: str, fake_org_id: str) -> None:
    capture: dict[str, object] = {}
    transport = _mock_portal(
        body={
            "verified": True,
            "user_id": fake_user_id,
            "org_id": fake_org_id,
            "cache_ttl_seconds": 60,
            "evidence": "membership",
        },
        capture=capture,
    )
    asserter = await _build_asserter(transport)

    await asserter.verify(
        caller_service="scribe",
        claimed_user_id=fake_user_id,
        claimed_org_id=fake_org_id,
        bearer_jwt=None,
        request_headers={"X-Request-ID": "trace-abc-123"},
    )

    headers = capture["headers"]
    assert isinstance(headers, dict)
    headers_dict = cast("dict[str, str]", headers)
    assert headers_dict.get("x-request-id") == "trace-abc-123"
    # Authorization header carries the shared INTERNAL_SECRET (matches
    # portal-api's _require_internal_token convention).
    assert headers_dict.get("authorization") == f"Bearer {INTERNAL_SECRET}"
    await asserter.aclose()


async def test_verify_uses_authorization_bearer_for_internal_secret(
    fake_user_id: str, fake_org_id: str
) -> None:
    """portal-api's /internal/* contract expects ``Authorization: Bearer <secret>``.

    This is intentionally NOT a custom ``X-Internal-Secret`` header — that
    convention is for callees of portal-api (knowledge-ingest, retrieval-api),
    not callers OF portal-api. Drift between the two would make the call
    fail with HTTP 401.
    """

    capture: dict[str, object] = {}
    transport = _mock_portal(
        body={
            "verified": True,
            "user_id": fake_user_id,
            "org_id": fake_org_id,
            "cache_ttl_seconds": 60,
            "evidence": "jwt",
        },
        capture=capture,
    )
    asserter = await _build_asserter(transport)

    await asserter.verify(
        caller_service="scribe",
        claimed_user_id=fake_user_id,
        claimed_org_id=fake_org_id,
        bearer_jwt="jwt-1",
    )

    headers = capture["headers"]
    assert isinstance(headers, dict)
    headers_dict = cast("dict[str, str]", headers)
    assert headers_dict["authorization"] == f"Bearer {INTERNAL_SECRET}"
    # X-Internal-Secret must NOT be set — that's the wrong convention for
    # portal-api's /internal/* surface.
    assert "x-internal-secret" not in headers_dict
    assert "/internal/identity/verify" in str(capture["url"])
    await asserter.aclose()


async def test_verify_or_raise_returns_on_allow(
    fake_user_id: str, fake_org_id: str
) -> None:
    transport = _mock_portal(
        body={
            "verified": True,
            "user_id": fake_user_id,
            "org_id": fake_org_id,
            "cache_ttl_seconds": 60,
            "evidence": "jwt",
        },
    )
    asserter = await _build_asserter(transport)

    result = await asserter.verify_or_raise(
        caller_service="scribe",
        claimed_user_id=fake_user_id,
        claimed_org_id=fake_org_id,
        bearer_jwt="jwt-1",
    )

    assert result.verified is True
    await asserter.aclose()


async def test_verify_or_raise_raises_portal_unreachable(
    fake_user_id: str, fake_org_id: str
) -> None:
    def fail(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("portal down")

    transport = httpx.MockTransport(fail)
    asserter = await _build_asserter(transport)

    with pytest.raises(PortalUnreachable):
        await asserter.verify_or_raise(
            caller_service="scribe",
            claimed_user_id=fake_user_id,
            claimed_org_id=fake_org_id,
            bearer_jwt=None,
        )
    await asserter.aclose()


async def test_verify_or_raise_raises_identity_denied(
    fake_user_id: str, other_org_id: str
) -> None:
    transport = _mock_portal(
        status_code=403,
        body={"verified": False, "reason": "no_membership"},
    )
    asserter = await _build_asserter(transport)

    with pytest.raises(IdentityDenied) as exc_info:
        await asserter.verify_or_raise(
            caller_service="scribe",
            claimed_user_id=fake_user_id,
            claimed_org_id=other_org_id,
            bearer_jwt=None,
        )
    assert exc_info.value.reason == "no_membership"
    await asserter.aclose()


def test_init_rejects_empty_portal_base_url() -> None:
    with pytest.raises(ValueError, match="portal_base_url"):
        IdentityAsserter(portal_base_url="", internal_secret="x")


def test_init_rejects_empty_internal_secret() -> None:
    with pytest.raises(ValueError, match="internal_secret"):
        IdentityAsserter(portal_base_url="http://x", internal_secret="")


async def test_aclose_owns_only_constructed_client(
    fake_user_id: str, fake_org_id: str
) -> None:
    """Asserter MUST NOT close a borrowed http_client (caller owns lifecycle)."""

    capture: dict[str, object] = {}
    transport = _mock_portal(
        body={
            "verified": True,
            "user_id": fake_user_id,
            "org_id": fake_org_id,
            "cache_ttl_seconds": 60,
            "evidence": "jwt",
        },
        capture=capture,
    )
    borrowed = httpx.AsyncClient(transport=transport)

    asserter = IdentityAsserter(
        portal_base_url=PORTAL_URL,
        internal_secret=INTERNAL_SECRET,
        http_client=borrowed,
    )
    await asserter.aclose()

    # Borrowed client must still be usable.
    response = await borrowed.post(
        f"{PORTAL_URL}/internal/identity/verify",
        json={
            "caller_service": "scribe",
            "claimed_user_id": fake_user_id,
            "claimed_org_id": fake_org_id,
            "bearer_jwt": None,
        },
    )
    assert response.status_code == 200
    await borrowed.aclose()


async def test_verify_request_body_shape(fake_user_id: str, fake_org_id: str) -> None:
    capture: dict[str, object] = {}
    transport = _mock_portal(
        body={
            "verified": True,
            "user_id": fake_user_id,
            "org_id": fake_org_id,
            "cache_ttl_seconds": 60,
            "evidence": "jwt",
        },
        capture=capture,
    )
    asserter = await _build_asserter(transport)

    await asserter.verify(
        caller_service="scribe",
        claimed_user_id=fake_user_id,
        claimed_org_id=fake_org_id,
        bearer_jwt="jwt-1",
    )

    import json as _json

    body = _json.loads(str(capture["body"]))
    assert body["caller_service"] == "scribe"
    assert body["claimed_user_id"] == fake_user_id
    assert body["claimed_org_id"] == fake_org_id
    assert body["bearer_jwt"] == "jwt-1"
    await asserter.aclose()


async def test_unrecognised_reason_code_collapses_to_portal_unreachable(
    fake_user_id: str, fake_org_id: str
) -> None:
    """If portal returns a reason code the library does not know, fail closed."""

    transport = _mock_portal(
        status_code=403,
        body={"verified": False, "reason": "some_future_code_we_do_not_know"},
    )
    asserter = await _build_asserter(transport)

    result = await asserter.verify(
        caller_service="scribe",
        claimed_user_id=fake_user_id,
        claimed_org_id=fake_org_id,
        bearer_jwt=None,
    )

    assert result.verified is False
    assert result.reason == "portal_unreachable"
    await asserter.aclose()


async def test_verify_returns_verify_result_type(
    asserter: IdentityAsserter, fake_user_id: str, fake_org_id: str
) -> None:
    """Smoke test: the asserter fixture composes correctly."""
    # No transport mounted on the fixture; we expect fail-closed.
    result = await asserter.verify(
        caller_service="scribe",
        claimed_user_id=fake_user_id,
        claimed_org_id=fake_org_id,
        bearer_jwt=None,
    )

    assert isinstance(result, VerifyResult)
    assert result.verified is False
    assert result.reason == "portal_unreachable"
