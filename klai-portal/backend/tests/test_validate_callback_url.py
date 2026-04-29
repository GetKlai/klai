"""SPEC-SEC-HYGIENE-001 REQ-20 / AC-20: callback URL subdomain allowlist.

The pre-fix `_validate_callback_url` accepted ANY hostname that ended
in `.{settings.domain}` (e.g. `.getklai.com`). An attacker who controlled
any past, present, or future subdomain (e.g. via a dangling DNS record
or a compromised tenant) could direct OIDC callbacks to themselves.

This test exercises the active-tenant allowlist gate. Zitadel's own
`redirect_uri` validation is the primary defence — this is the
defense-in-depth tenant-explicit layer.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api import auth as auth_module


@pytest.fixture(autouse=True)
def _reset_cache() -> object:
    """Each test starts with a fresh tenant-slug cache, then RESTORES the
    conftest-populated cache after the test so unrelated tests that ran
    later (e.g. test_auth_security login flows) still see the populated
    allowlist they expect.
    """
    saved_cache = auth_module._tenant_slug_cache
    saved_expiry = auth_module._tenant_slug_cache_expiry
    auth_module.invalidate_tenant_slug_cache()
    yield
    auth_module._tenant_slug_cache = saved_cache
    auth_module._tenant_slug_cache_expiry = saved_expiry


def _patch_allowlist(slugs: set[str]) -> patch:
    """Patch the slug-allowlist getter to return a fixed set."""
    return patch.object(
        auth_module,
        "_get_tenant_slug_allowlist",
        AsyncMock(return_value=slugs),
    )


@pytest.mark.asyncio
async def test_known_tenant_subdomain_passes() -> None:
    """REQ-20.1: a hostname whose first label is in the allowlist passes."""
    with _patch_allowlist({"voys", "getklai"}):
        url = "https://voys.getklai.com/x"
        result = await auth_module._validate_callback_url(url)
    assert result == url


@pytest.mark.asyncio
async def test_unknown_subdomain_raises_502() -> None:
    """REQ-20.1: a hostname whose first label is NOT in the allowlist
    must raise HTTPException(502) with the generic error message.
    """
    with _patch_allowlist({"voys", "getklai"}):
        with pytest.raises(HTTPException) as exc_info:
            await auth_module._validate_callback_url("https://dangling.getklai.com/x")
    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "Login failed, please try again later"


@pytest.mark.asyncio
async def test_bare_domain_passes() -> None:
    """REQ-20.1: hostname == settings.domain (bare apex) is allowed unchanged."""
    with _patch_allowlist(set()):
        # Even with empty allowlist, the apex passes.
        url = f"https://{auth_module.settings.domain}/x"
        result = await auth_module._validate_callback_url(url)
    assert result == url


@pytest.mark.asyncio
async def test_localhost_short_circuit_preserved() -> None:
    """REQ-20.3: localhost / 127.0.0.1 short-circuits MUST be preserved
    unchanged. They never hit the allowlist lookup.
    """
    # Empty allowlist — if the short-circuit failed, this would 502.
    with _patch_allowlist(set()):
        url1 = "http://localhost:3000/x"
        url2 = "http://127.0.0.1:3000/x"
        assert await auth_module._validate_callback_url(url1) == url1
        assert await auth_module._validate_callback_url(url2) == url2


@pytest.mark.asyncio
async def test_external_host_raises_502() -> None:
    """A non-getklai.com host is rejected before the allowlist check fires."""
    with _patch_allowlist({"voys"}):
        with pytest.raises(HTTPException) as exc_info:
            await auth_module._validate_callback_url("https://evil.com/x")
    assert exc_info.value.status_code == 502


# REQ-20.2: cache TTL behaviour ------------------------------------------- #


@pytest.mark.asyncio
async def test_cache_repeats_within_ttl_do_not_emit_miss() -> None:
    """REQ-20.2: cache miss is logged once; repeated lookups inside 60s
    do NOT re-emit the cache_miss metric (we hit the cache).

    Test technique: count calls to the underlying loader. The public
    helper `_get_tenant_slug_allowlist` uses the loader on miss only.
    """
    call_count = {"n": 0}

    async def fake_loader() -> set[str]:
        call_count["n"] += 1
        return {"voys"}

    with patch.object(auth_module, "_load_tenant_slugs_from_db", fake_loader):
        s1 = await auth_module._get_tenant_slug_allowlist()
        s2 = await auth_module._get_tenant_slug_allowlist()
    assert s1 == s2 == {"voys"}
    assert call_count["n"] == 1, "Second call within TTL must use cached result."


@pytest.mark.asyncio
async def test_invalidate_clears_cache() -> None:
    """REQ-20.2: invalidate_tenant_slug_cache() forces the next call to refresh."""
    call_count = {"n": 0}

    async def fake_loader() -> set[str]:
        call_count["n"] += 1
        return {"voys"} if call_count["n"] == 1 else {"voys", "newtenant"}

    with patch.object(auth_module, "_load_tenant_slugs_from_db", fake_loader):
        s1 = await auth_module._get_tenant_slug_allowlist()
        auth_module.invalidate_tenant_slug_cache()
        s2 = await auth_module._get_tenant_slug_allowlist()
    assert s1 == {"voys"}
    assert s2 == {"voys", "newtenant"}
    assert call_count["n"] == 2
