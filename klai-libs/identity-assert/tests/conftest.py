"""Shared fixtures for klai-identity-assert tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from klai_identity_assert import IdentityAsserter

# Test-only constants. test_client.py mirrors these for the ad-hoc transports
# it builds outside the asserter fixture; keep the two locations in sync.
_PORTAL_URL = "http://portal-api:8000"
_INTERNAL_SECRET = "test-internal-secret"  # noqa: S105


@pytest_asyncio.fixture
async def asserter() -> AsyncIterator[IdentityAsserter]:
    """An IdentityAsserter wired against the canonical test portal URL.

    The httpx.AsyncClient is owned by the asserter and is cleaned up via
    the async-context exit on fixture teardown.
    """

    async with IdentityAsserter(
        portal_base_url=_PORTAL_URL,
        internal_secret=_INTERNAL_SECRET,
        cache_ttl_seconds=60.0,
    ) as a:
        yield a


@pytest.fixture
def fake_user_id() -> str:
    return "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def fake_org_id() -> str:
    return "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def other_user_id() -> str:
    return "33333333-3333-3333-3333-333333333333"


@pytest.fixture
def other_org_id() -> str:
    return "44444444-4444-4444-4444-444444444444"


@pytest.fixture
def fake_jwt() -> str:
    # Not a real JWT — the consumer library never decodes the JWT, it merely
    # forwards it. The portal does the actual JWT validation (REQ-1.3).
    return "header.payload.signature"


@pytest.fixture
def httpx_no_pool() -> httpx.AsyncClient:
    """An httpx.AsyncClient that fails immediately on connect (for fail-closed tests)."""

    transport = httpx.MockTransport(_unreachable)
    return httpx.AsyncClient(transport=transport)


def _unreachable(_request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("simulated portal unreachable")
