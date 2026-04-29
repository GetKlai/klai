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
    """Each test starts with a fresh tenant-slug cache AND a fresh
    `_system_callback_hosts` lru_cache, then RESTORES the conftest-populated
    tenant-slug cache after the test so unrelated tests that run later
    (e.g. test_auth_security login flows, test_idp_callback_provision)
    still see the populated allowlist they expect.

    The lru_cache on `_system_callback_hosts` is also cleared on entry AND
    exit because tests in this file monkeypatch `settings.frontend_url`;
    leaving a stale cached set after teardown would let the patched value
    leak into later tests that import the same module.
    """
    saved_cache = auth_module._tenant_slug_cache
    saved_expiry = auth_module._tenant_slug_cache_expiry
    auth_module.invalidate_tenant_slug_cache()
    auth_module._system_callback_hosts.cache_clear()
    yield
    auth_module._tenant_slug_cache = saved_cache
    auth_module._tenant_slug_cache_expiry = saved_expiry
    auth_module._system_callback_hosts.cache_clear()


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


# REQ-20.4: system-host bypass (FRONTEND_URL host) ------------------------- #
#
# The callback-URL allowlist must enumerate every legitimate hostname class
# that a Zitadel-issued callback can resolve to. The 2026-04-29 prod outage
# (see SPEC v0.7.1 HISTORY) was caused by REQ-20.1 covering only
# tenant-slug + apex + localhost, missing the canonical login domain
# (FRONTEND_URL host). REQ-20.4 introduces `_system_callback_hosts()` so the
# bare-apex AND the login-domain pass before the slug allowlist is even
# consulted.


class TestSystemCallbackHosts:
    """REQ-20.4: composition of the trusted-non-tenant host set."""

    def test_includes_bare_apex(self) -> None:
        with (
            patch.object(auth_module.settings, "domain", "getklai.com"),
            patch.object(auth_module.settings, "frontend_url", "https://my.getklai.com"),
        ):
            auth_module._system_callback_hosts.cache_clear()
            assert "getklai.com" in auth_module._system_callback_hosts()

    def test_includes_frontend_url_host(self) -> None:
        with (
            patch.object(auth_module.settings, "domain", "getklai.com"),
            patch.object(auth_module.settings, "frontend_url", "https://my.getklai.com"),
        ):
            auth_module._system_callback_hosts.cache_clear()
            assert "my.getklai.com" in auth_module._system_callback_hosts()

    def test_returns_frozenset(self) -> None:
        """Immutable so callers cannot accidentally mutate the cache."""
        with (
            patch.object(auth_module.settings, "domain", "getklai.com"),
            patch.object(auth_module.settings, "frontend_url", "https://my.getklai.com"),
        ):
            auth_module._system_callback_hosts.cache_clear()
            assert isinstance(auth_module._system_callback_hosts(), frozenset)

    def test_handles_empty_frontend_url(self) -> None:
        """If FRONTEND_URL is unset (dev), only the bare apex is in the set."""
        with (
            patch.object(auth_module.settings, "domain", "getklai.com"),
            patch.object(auth_module.settings, "frontend_url", ""),
        ):
            auth_module._system_callback_hosts.cache_clear()
            assert auth_module._system_callback_hosts() == frozenset({"getklai.com"})


@pytest.mark.asyncio
async def test_frontend_url_host_passes() -> None:
    """REQ-20.4 regression: ``my.getklai.com`` (FRONTEND_URL host) is the
    canonical login domain and must always pass even though ``my`` is not a
    tenant slug.

    Without this case, every TOTP-completing OIDC login fails 502 because
    Zitadel always redirects through the FRONTEND_URL host first per
    SPEC-AUTH-008. Originally triggered the 2026-04-29 prod outage.
    """
    with (
        patch.object(auth_module.settings, "domain", "getklai.com"),
        patch.object(auth_module.settings, "frontend_url", "https://my.getklai.com"),
    ):
        auth_module._system_callback_hosts.cache_clear()
        # Empty slug allowlist on purpose: REQ-20.4 must short-circuit before
        # the slug check fires. If the system-host bypass regresses, this 502s.
        with _patch_allowlist(set()):
            url = "https://my.getklai.com/api/auth/oidc/callback?code=abc"
            result = await auth_module._validate_callback_url(url)
        assert result == url


@pytest.mark.asyncio
async def test_frontend_url_host_passes_even_when_label_overlaps_tenant_slug() -> None:
    """REQ-20.4 invariant: FRONTEND_URL host bypass is host-equality, not
    label-overlap. Even if a tenant slug ``my`` existed, the bypass would
    still work — and conversely, a tenant slug must not be able to spoof
    the login host (``my.foreign.tld`` would still 502 because the host
    is not in `_system_callback_hosts()`).
    """
    with (
        patch.object(auth_module.settings, "domain", "getklai.com"),
        patch.object(auth_module.settings, "frontend_url", "https://my.getklai.com"),
    ):
        auth_module._system_callback_hosts.cache_clear()
        # Tenant slug "my" exists — irrelevant for system-host check.
        with _patch_allowlist({"my", "voys"}):
            url = "https://my.getklai.com/x"
            result = await auth_module._validate_callback_url(url)
        assert result == url


@pytest.mark.asyncio
async def test_apex_lookalike_still_rejected() -> None:
    """Adversarial: ``getklai.com.attacker.tld`` must not pass even though
    the bare apex appears as a substring in the hostname."""
    with (
        patch.object(auth_module.settings, "domain", "getklai.com"),
        patch.object(auth_module.settings, "frontend_url", "https://my.getklai.com"),
    ):
        auth_module._system_callback_hosts.cache_clear()
        with _patch_allowlist({"voys"}):
            with pytest.raises(HTTPException) as exc_info:
                await auth_module._validate_callback_url("https://getklai.com.attacker.tld/x")
        assert exc_info.value.status_code == 502
