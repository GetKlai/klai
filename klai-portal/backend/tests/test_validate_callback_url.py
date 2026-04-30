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


# REQ-20 hardening: adversarial URL property tests ----------------------- #
#
# Adversarial coverage of `urllib.parse.urlparse` edge cases that a
# malicious or malformed callback URL might exploit. Each test pins the
# exact expected behaviour so a future urlparse upgrade or a refactor of
# `_validate_callback_url` cannot silently change semantics.
#
# Decisions captured here:
#   - mixed-case hostnames: urlparse lowercases — accepted as system host
#   - leading whitespace:    Python 3.13+ strips per CVE-2023-24329 fix —
#                            accepted (host is correct)
#   - trailing dot (FQDN):   rejected (host-equality is byte-strict and
#                            Zitadel never emits FQDN form)
#   - userinfo injection:    accepted iff host equals system host (Zitadel
#                            registered the redirect_uri exact-match; userinfo
#                            does not affect the host check; this is
#                            defense-in-depth, NOT primary auth)
#   - encoded-dot in host:   rejected (hostname keeps %2E literal)
#   - IPv6 ::1:              rejected (NOT in our localhost shortcut set;
#                            only the literal strings "localhost" and
#                            "127.0.0.1" pass)
#   - quad-slash + None host:rejected (urlparse returns hostname None)


@pytest.mark.parametrize(
    "url",
    [
        # urlparse normalises hostname to lowercase
        "https://MY.GETKLAI.COM/api/auth/oidc/callback?code=abc",
        "https://My.GetKlai.Com/x",
        # CVE-2023-24329 fix: leading whitespace stripped on Python 3.11+
        "\thttps://my.getklai.com/x",
        " https://my.getklai.com/x",
        # Explicit default port: hostname is still my.getklai.com
        "https://my.getklai.com:443/api/auth/oidc/callback",
        # Userinfo injection: hostname extraction ignores userinfo (Zitadel's
        # exact-match on registered redirect_uri prevents the threat at the
        # layer above; this layer just verifies the HOST)
        "https://attacker@my.getklai.com/x",
        "https://attacker:secret@my.getklai.com/x",
        # Query / fragment can contain anything — host check is unaffected
        "https://my.getklai.com/cb?next=https://evil.com",
        "https://my.getklai.com/cb#@evil.com",
    ],
)
@pytest.mark.asyncio
async def test_adversarial_accepts_when_host_resolves_to_system_host(url: str) -> None:
    """Adversarial accept-cases: hostname normalises to a system host,
    the URL passes regardless of cosmetic noise around it."""
    with (
        patch.object(auth_module.settings, "domain", "getklai.com"),
        patch.object(auth_module.settings, "frontend_url", "https://my.getklai.com"),
    ):
        auth_module._system_callback_hosts.cache_clear()
        with _patch_allowlist(set()):  # empty allowlist — must short-circuit on system host
            result = await auth_module._validate_callback_url(url)
        assert result == url


@pytest.mark.parametrize(
    "url",
    [
        # Trailing dot — host becomes "my.getklai.com." (with dot), not in system set
        "https://my.getklai.com./x",
        # IPv6 loopback — only literal "127.0.0.1" / "localhost" pass the dev shortcut
        "https://[::1]/x",
        # Encoded dot in hostname — urlparse keeps %2E literal, no normalisation
        "https://my.getklai.com%2Eevil.com/x",
        # Quad slash defeats urlparse — hostname is None
        "https:////my.getklai.com",
        # Bare URL — urlparse returns hostname None
        "not-a-url",
        # Empty string — early-exit on the empty hostname branch
        "",
        # Subdomain spoofing the apex but pointing at attacker tld
        "https://getklai.com.attacker.tld/x",
        # Path-traversal of the apex via leading dots — host is the dotty form
        "https://....getklai.com/x",
    ],
)
@pytest.mark.asyncio
async def test_adversarial_rejects_malformed_or_lookalike(url: str) -> None:
    """Adversarial reject-cases: hostnames that LOOK system-like but
    structurally don't match the system-host set, plus malformed URLs
    that urlparse cannot extract a hostname from. Every case must 502
    with the generic body."""
    with (
        patch.object(auth_module.settings, "domain", "getklai.com"),
        patch.object(auth_module.settings, "frontend_url", "https://my.getklai.com"),
    ):
        auth_module._system_callback_hosts.cache_clear()
        with _patch_allowlist({"voys", "getklai"}):
            with pytest.raises(HTTPException) as exc_info:
                await auth_module._validate_callback_url(url)
        assert exc_info.value.status_code == 502
        assert exc_info.value.detail == "Login failed, please try again later"


@pytest.mark.asyncio
async def test_tenant_subdomain_mixed_case_accepted() -> None:
    """REQ-20.1 + adversarial: a tenant subdomain in mixed case still
    matches the slug allowlist after urlparse lowercases the hostname."""
    with (
        patch.object(auth_module.settings, "domain", "getklai.com"),
        patch.object(auth_module.settings, "frontend_url", "https://my.getklai.com"),
    ):
        auth_module._system_callback_hosts.cache_clear()
        with _patch_allowlist({"voys", "getklai"}):
            url = "https://VOYS.GetKlai.com/api/auth/oidc/callback"
            result = await auth_module._validate_callback_url(url)
        assert result == url


@pytest.mark.asyncio
async def test_tenant_subdomain_with_port_accepted() -> None:
    """REQ-20.1 + adversarial: explicit port does not affect the
    first-label slug check. Hostname property strips the port."""
    with (
        patch.object(auth_module.settings, "domain", "getklai.com"),
        patch.object(auth_module.settings, "frontend_url", "https://my.getklai.com"),
    ):
        auth_module._system_callback_hosts.cache_clear()
        with _patch_allowlist({"voys"}):
            url = "https://voys.getklai.com:443/x"
            result = await auth_module._validate_callback_url(url)
        assert result == url


@pytest.mark.asyncio
async def test_idn_punycode_subdomain_rejected() -> None:
    """Adversarial: an IDN homoglyph attack relies on a punycode
    subdomain that visually resembles a real slug. Without an explicit
    allowlist entry for the punycode form, the validator rejects it.

    Confirms IDN normalisation does NOT auto-bridge between unicode and
    punycode forms — the slug allowlist is byte-strict against
    `urlparse(url).hostname`, which preserves whatever encoding was on
    the wire.
    """
    with (
        patch.object(auth_module.settings, "domain", "getklai.com"),
        patch.object(auth_module.settings, "frontend_url", "https://my.getklai.com"),
    ):
        auth_module._system_callback_hosts.cache_clear()
        with _patch_allowlist({"voys"}):
            # Punycode form of an IDN that visually resembles "voys" but
            # uses a non-ASCII codepoint in place of one letter. The
            # exact unicode source does not matter for the test — we just
            # need a valid xn-- label that is NOT in the slug allowlist.
            url = "https://xn--vys-4md.getklai.com/x"
            with pytest.raises(HTTPException) as exc_info:
                await auth_module._validate_callback_url(url)
        assert exc_info.value.status_code == 502
