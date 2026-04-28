"""Unit tests for the per-process TTL cache."""

from __future__ import annotations

import pytest

from klai_identity_assert import VerifyResult
from klai_identity_assert.cache import IdentityCache, _fingerprint_jwt  # pyright: ignore[reportPrivateUsage]


def _verified() -> VerifyResult:
    return VerifyResult.allow(user_id="u-1", org_id="o-1", org_slug="acme", evidence="jwt")


def test_get_returns_none_on_miss() -> None:
    cache = IdentityCache()

    assert cache.get(
        caller_service="scribe",
        claimed_user_id="u-1",
        claimed_org_id="o-1",
        bearer_jwt=None,
    ) is None


def test_put_then_get_returns_cached_with_cached_flag_true() -> None:
    cache = IdentityCache()
    cache.put(
        caller_service="scribe",
        claimed_user_id="u-1",
        claimed_org_id="o-1",
        bearer_jwt=None,
        result=_verified(),
        now=0.0,
    )

    cached = cache.get(
        caller_service="scribe",
        claimed_user_id="u-1",
        claimed_org_id="o-1",
        bearer_jwt=None,
        now=30.0,
    )

    assert cached is not None
    assert cached.verified is True
    assert cached.cached is True
    assert cached.user_id == "u-1"
    assert cached.org_slug == "acme"
    assert cached.evidence == "jwt"


def test_get_returns_none_after_ttl_elapsed() -> None:
    cache = IdentityCache(ttl_seconds=60.0)
    cache.put(
        caller_service="scribe",
        claimed_user_id="u-1",
        claimed_org_id="o-1",
        bearer_jwt=None,
        result=_verified(),
        now=0.0,
    )

    expired = cache.get(
        caller_service="scribe",
        claimed_user_id="u-1",
        claimed_org_id="o-1",
        bearer_jwt=None,
        now=61.0,
    )

    assert expired is None


def test_put_does_not_cache_denied_results() -> None:
    cache = IdentityCache()
    cache.put(
        caller_service="scribe",
        claimed_user_id="u-1",
        claimed_org_id="o-1",
        bearer_jwt=None,
        result=VerifyResult.deny("no_membership"),
    )

    assert len(cache) == 0
    assert cache.get(
        caller_service="scribe",
        claimed_user_id="u-1",
        claimed_org_id="o-1",
        bearer_jwt=None,
    ) is None


def test_jwt_fingerprint_distinguishes_callers() -> None:
    cache = IdentityCache()
    cache.put(
        caller_service="scribe",
        claimed_user_id="u-1",
        claimed_org_id="o-1",
        bearer_jwt="jwt-A",
        result=_verified(),
        now=0.0,
    )

    # A different JWT for the same (service, user, org) tuple is a CACHE MISS.
    miss = cache.get(
        caller_service="scribe",
        claimed_user_id="u-1",
        claimed_org_id="o-1",
        bearer_jwt="jwt-B",
        now=1.0,
    )
    assert miss is None

    # Same JWT is a hit.
    hit = cache.get(
        caller_service="scribe",
        claimed_user_id="u-1",
        claimed_org_id="o-1",
        bearer_jwt="jwt-A",
        now=1.0,
    )
    assert hit is not None


def test_jwt_fingerprint_helper_is_deterministic_and_short() -> None:
    a = _fingerprint_jwt("the-token")
    b = _fingerprint_jwt("the-token")
    c = _fingerprint_jwt("different-token")

    assert a == b
    assert a != c
    assert _fingerprint_jwt(None) == "none"
    # Truncation: 16 hex chars (64 bits).
    assert len(a) == 16


def test_lru_eviction_drops_oldest_when_capacity_exceeded() -> None:
    cache = IdentityCache(max_entries=2)
    cache.put(
        caller_service="scribe",
        claimed_user_id="u-A",
        claimed_org_id="o-1",
        bearer_jwt=None,
        result=_verified(),
        now=0.0,
    )
    cache.put(
        caller_service="scribe",
        claimed_user_id="u-B",
        claimed_org_id="o-1",
        bearer_jwt=None,
        result=_verified(),
        now=1.0,
    )
    # Touch u-A so u-B becomes the oldest.
    cache.get(
        caller_service="scribe",
        claimed_user_id="u-A",
        claimed_org_id="o-1",
        bearer_jwt=None,
        now=2.0,
    )
    cache.put(
        caller_service="scribe",
        claimed_user_id="u-C",
        claimed_org_id="o-1",
        bearer_jwt=None,
        result=_verified(),
        now=3.0,
    )

    assert cache.get(
        caller_service="scribe",
        claimed_user_id="u-B",
        claimed_org_id="o-1",
        bearer_jwt=None,
        now=4.0,
    ) is None
    assert cache.get(
        caller_service="scribe",
        claimed_user_id="u-A",
        claimed_org_id="o-1",
        bearer_jwt=None,
        now=4.0,
    ) is not None


def test_init_rejects_invalid_ttl() -> None:
    with pytest.raises(ValueError, match="ttl_seconds"):
        IdentityCache(ttl_seconds=0)
    with pytest.raises(ValueError, match="ttl_seconds"):
        IdentityCache(ttl_seconds=-1.0)


def test_init_rejects_invalid_max_entries() -> None:
    with pytest.raises(ValueError, match="max_entries"):
        IdentityCache(max_entries=0)
