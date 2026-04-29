"""End-to-end contract test: real klai-identity-assert library against the
real portal-api ``/internal/identity/verify`` endpoint via in-process ASGI.

This catches drift between the library's wire format and portal-api's
Pydantic models. Each side has a Pydantic / dataclass schema for the same
JSON shape (REQ-1.1); a one-sided change that breaks the contract surfaces
here as a test failure rather than as a production HTTP 422 / decode error.

Test design:
- Build a throwaway FastAPI app that mounts only the ``internal`` router.
- Override ``get_db`` with an AsyncMock returning a fake membership row.
- Stub Redis pool, JWKS resolver, rate limiter, and ``settings.internal_secret``
  via monkeypatch — the same fixtures the unit tests use.
- Drive the app with ``IdentityAsserter`` configured with an
  ``httpx.ASGITransport`` so requests stay in-process.

If the library's request body shape no longer matches
``IdentityVerifyRequest`` or the response no longer parses into
``VerifyResult``, these tests fail loud at import-time or at first call.

Coverage:
- AC-5a-equivalent: JWT-evidence allow path
- AC-5c-equivalent: membership-evidence allow path
- AC-5d-equivalent: no-membership deny path
- Allowlist-drift guard: library KNOWN_CALLER_SERVICES == server allowlist
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from klai_identity_assert import KNOWN_CALLER_SERVICES as LIBRARY_KNOWN_CALLER_SERVICES
from klai_identity_assert import IdentityAsserter


@contextmanager
def _patch_internal_secret(monkeypatch: pytest.MonkeyPatch, value: str) -> Iterator[None]:
    from app.api import internal as internal_mod

    monkeypatch.setattr(internal_mod.settings, "internal_secret", value)
    yield


def _make_redis_mock() -> AsyncMock:
    store: dict[str, str] = {}

    async def fake_get(key: str) -> str | None:
        return store.get(key)

    async def fake_set(key: str, value: str, ex: int | None = None) -> bool:
        store[key] = value
        return True

    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=fake_get)
    redis.set = AsyncMock(side_effect=fake_set)
    redis._store = store
    return redis


class _FakeJwksResolver:
    class _Key:
        key = "fake"

    def get_signing_key_from_jwt(self, _token: str) -> _Key:
        return self._Key()


@pytest.fixture
def portal_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Throwaway FastAPI app that mounts only the internal router."""

    from app.api.internal import router
    from app.core.database import get_db

    app = FastAPI()
    app.include_router(router)

    async def override_get_db() -> AsyncIterator[AsyncMock]:
        db = AsyncMock()
        result = MagicMock()
        # Returns the canonical slug for both verifier paths:
        # _resolve_active_membership_org_slug (membership) and
        # _resolve_org_slug (JWT). Both use scalar_one_or_none()
        # so a single mock satisfies both code paths in REQ-2.6.
        result.scalar_one_or_none = MagicMock(return_value="acme")
        db.execute = AsyncMock(return_value=result)
        yield db

    app.dependency_overrides[get_db] = override_get_db

    redis = _make_redis_mock()
    monkeypatch.setattr("app.api.internal.get_redis_pool", AsyncMock(return_value=redis))
    monkeypatch.setattr("app.api.internal._check_rate_limit_internal", AsyncMock())
    monkeypatch.setattr(
        "app.api.internal._get_identity_jwks_resolver",
        lambda: _FakeJwksResolver(),
    )
    return app


@pytest_asyncio.fixture
async def asserter_against_app(portal_app: FastAPI) -> AsyncIterator[IdentityAsserter]:
    """An IdentityAsserter wired through ASGITransport into ``portal_app``.

    The httpx.AsyncClient is owned by the caller (us) — we close it on
    fixture teardown so the asserter's borrowed-client semantics are
    exercised correctly.
    """

    # ASGITransport routes httpx requests directly into the FastAPI app.
    # We do NOT set base_url on the AsyncClient — the asserter constructs
    # full absolute URLs itself, and a duplicate base_url would conflict
    # with the absolute path resolution httpx applies via ASGITransport.
    transport = httpx.ASGITransport(app=portal_app)
    http_client = httpx.AsyncClient(transport=transport)
    asserter = IdentityAsserter(
        portal_base_url="http://testserver",
        internal_secret="contract-test-secret",
        http_client=http_client,
    )
    try:
        yield asserter
    finally:
        await asserter.aclose()
        await http_client.aclose()


# ---------------------------------------------------------------------------
# Allowlist drift guard
# ---------------------------------------------------------------------------


def test_library_and_server_caller_allowlists_match() -> None:
    """The library's KNOWN_CALLER_SERVICES MUST equal the server's.

    Drift in either direction is a silent attack vector: either the library
    rejects a caller the server would accept (consumer broken), or the
    server rejects a caller the library forwards (production 400s).
    """

    from app.services.identity_verifier import KNOWN_CALLER_SERVICES as SERVER_KNOWN_CALLER_SERVICES

    assert LIBRARY_KNOWN_CALLER_SERVICES == SERVER_KNOWN_CALLER_SERVICES


# ---------------------------------------------------------------------------
# Membership-evidence allow (REQ-1.4)
# ---------------------------------------------------------------------------


async def test_library_membership_path_allows_against_real_endpoint(
    asserter_against_app: IdentityAsserter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _patch_internal_secret(monkeypatch, "contract-test-secret"):
        result = await asserter_against_app.verify(
            caller_service="scribe",
            claimed_user_id="u-1",
            claimed_org_id="o-1",
            bearer_jwt=None,
        )

    assert result.verified is True, result
    assert result.evidence == "membership"
    assert result.user_id == "u-1"
    assert result.org_id == "o-1"
    assert result.org_slug == "acme"
    assert result.cached is False  # first call, cache miss


# ---------------------------------------------------------------------------
# JWT-evidence allow (REQ-1.3)
# ---------------------------------------------------------------------------


async def test_library_jwt_path_allows_against_real_endpoint(
    asserter_against_app: IdentityAsserter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.identity_verifier.jwt.decode",
        lambda *_args, **_kwargs: {
            "sub": "u-1",
            "iss": "https://zitadel.example.com",
            "exp": 9999999999,
            "urn:zitadel:iam:user:resourceowner:id": "o-1",
        },
    )

    with _patch_internal_secret(monkeypatch, "contract-test-secret"):
        result = await asserter_against_app.verify(
            caller_service="scribe",
            claimed_user_id="u-1",
            claimed_org_id="o-1",
            bearer_jwt="forwarded.user.jwt",
        )

    assert result.verified is True, result
    assert result.evidence == "jwt"
    assert result.org_slug == "acme"


# ---------------------------------------------------------------------------
# JWT mismatch deny (REQ-1.3 / AC-5b)
# ---------------------------------------------------------------------------


async def test_library_jwt_mismatch_denies_against_real_endpoint(
    asserter_against_app: IdentityAsserter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # JWT belongs to u-A but caller claims to be u-B.
    monkeypatch.setattr(
        "app.services.identity_verifier.jwt.decode",
        lambda *_args, **_kwargs: {
            "sub": "u-A",
            "iss": "https://zitadel.example.com",
            "exp": 9999999999,
            "urn:zitadel:iam:user:resourceowner:id": "o-1",
        },
    )

    with _patch_internal_secret(monkeypatch, "contract-test-secret"):
        result = await asserter_against_app.verify(
            caller_service="scribe",
            claimed_user_id="u-B",
            claimed_org_id="o-1",
            bearer_jwt="any.jwt.value",
        )

    assert result.verified is False, result
    assert result.reason == "jwt_identity_mismatch"
    assert result.user_id is None
    assert result.org_id is None


# ---------------------------------------------------------------------------
# Cache propagation (REQ-1.5 + library REQ-7.2)
# ---------------------------------------------------------------------------


async def test_library_second_call_hits_library_side_cache(
    asserter_against_app: IdentityAsserter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Second identical call MUST not generate a second portal request.

    The library's per-process LRU cache is the first line of defense; this
    test exercises that the cached-flag round-trips correctly through the
    library AFTER one successful portal verify.
    """

    with _patch_internal_secret(monkeypatch, "contract-test-secret"):
        first = await asserter_against_app.verify(
            caller_service="scribe",
            claimed_user_id="u-1",
            claimed_org_id="o-1",
            bearer_jwt=None,
        )
        second = await asserter_against_app.verify(
            caller_service="scribe",
            claimed_user_id="u-1",
            claimed_org_id="o-1",
            bearer_jwt=None,
        )

    assert first.verified is True
    assert first.cached is False
    assert second.verified is True
    assert second.cached is True
    assert second.evidence == first.evidence
