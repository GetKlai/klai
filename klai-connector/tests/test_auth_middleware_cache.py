"""Unit tests for the LRU token cache in app.middleware.auth.

Covers SPEC-SEC-007 REQ-1 / AC-1.1 — AC-1.5: true LRU semantics via
collections.OrderedDict, replacing the previous insertion-order pseudo-LRU.

The cache is a module-level global, so each test clears it explicitly via
the autouse fixture to avoid cross-test pollution.
"""

from __future__ import annotations

import time
from collections import OrderedDict

import pytest

from app.middleware import auth as auth_module
from app.middleware.auth import (
    _CACHE_MAX_SIZE,
    _CACHE_TTL,
    _cache_get,
    _cache_put,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Reset the module-level cache before and after every test."""
    auth_module._token_cache.clear()
    yield
    auth_module._token_cache.clear()


def _claims(n: int) -> dict[str, object]:
    """Build a distinct claims payload so we can tell entries apart."""
    return {"sub": f"user-{n}", "active": True}


class TestCacheInvariants:
    """Baseline invariants that must not regress."""

    def test_cache_is_an_ordered_dict(self):
        # AC-1.1 relies on OrderedDict semantics (move_to_end / popitem(last=False)).
        assert isinstance(auth_module._token_cache, OrderedDict)

    def test_cache_constants_unchanged(self):
        # AC-1.6 — the size / TTL constants are out-of-scope for this SPEC.
        assert _CACHE_MAX_SIZE == 1000
        assert _CACHE_TTL == 300


class TestCacheGet:
    """Behavior of _cache_get."""

    def test_miss_returns_none(self):
        assert _cache_get("absent") is None

    def test_hit_returns_claims(self):
        _cache_put("k", _claims(1))
        assert _cache_get("k") == _claims(1)

    def test_hit_promotes_to_mru(self):
        # AC-1.2 — a read must move the entry to the MRU end.
        _cache_put("a", _claims(1))
        _cache_put("b", _claims(2))
        _cache_put("c", _claims(3))
        # Order is: a (LRU), b, c (MRU)
        assert list(auth_module._token_cache.keys()) == ["a", "b", "c"]
        _cache_get("a")
        # After the read, 'a' must be the MRU entry.
        assert list(auth_module._token_cache.keys()) == ["b", "c", "a"]

    def test_expired_entry_is_removed_and_not_promoted(self):
        # AC-1.3 — expired hits are popped, return None, and cannot be
        # promoted to MRU (the entry no longer exists).
        _cache_put("stale", _claims(1))
        # Poison the expiry so time.monotonic() reads it as already-expired.
        claims, _expiry = auth_module._token_cache["stale"]
        auth_module._token_cache["stale"] = (claims, time.monotonic() - 1)

        assert _cache_get("stale") is None
        assert "stale" not in auth_module._token_cache


class TestCachePut:
    """Behavior of _cache_put — insertion, overwrite, eviction."""

    def test_new_key_inserts_at_mru(self):
        _cache_put("a", _claims(1))
        _cache_put("b", _claims(2))
        # Most-recently-inserted = MRU end.
        assert list(auth_module._token_cache.keys()) == ["a", "b"]

    def test_overwrite_promotes_and_keeps_size(self):
        # AC-1.4 — re-putting an existing key overwrites in place and
        # promotes to MRU without growing the cache.
        _cache_put("a", _claims(1))
        _cache_put("b", _claims(2))
        _cache_put("c", _claims(3))
        pre_len = len(auth_module._token_cache)

        _cache_put("a", _claims(99))

        # Size unchanged, 'a' at the MRU end, claims overwritten.
        assert len(auth_module._token_cache) == pre_len
        assert list(auth_module._token_cache.keys()) == ["b", "c", "a"]
        assert _cache_get("a") == _claims(99)


class TestLRUEviction:
    """Eviction policy — the core SPEC-SEC-007 REQ-1 behavior."""

    def test_eviction_removes_least_recently_used_not_least_recently_inserted(self, monkeypatch):
        # AC-1.1 + AC-1.5 — fill to _CACHE_MAX_SIZE - 1, read the oldest key
        # (promoting it), then insert two more. The second-oldest (k_2) should
        # be evicted; k_1 survives because it was recently read.
        #
        # Shrink the cache to make the test fast while still exercising the
        # real _cache_put code path.
        monkeypatch.setattr(auth_module, "_CACHE_MAX_SIZE", 5)

        # Seed with 4 entries: k_1 (LRU) .. k_4 (MRU).
        for i in range(1, 5):
            _cache_put(f"k_{i}", _claims(i))
        assert list(auth_module._token_cache.keys()) == ["k_1", "k_2", "k_3", "k_4"]

        # Read the oldest key — this is the test for true LRU. Insertion-order
        # eviction would evict k_1 next; LRU eviction must NOT.
        assert _cache_get("k_1") == _claims(1)
        assert list(auth_module._token_cache.keys()) == ["k_2", "k_3", "k_4", "k_1"]

        # Bring cache up to max (no eviction yet).
        _cache_put("k_new", _claims(10))
        assert len(auth_module._token_cache) == 5
        assert list(auth_module._token_cache.keys()) == ["k_2", "k_3", "k_4", "k_1", "k_new"]

        # Now insert one more — must evict k_2 (the new LRU), NOT k_1.
        _cache_put("k_new2", _claims(11))

        assert len(auth_module._token_cache) == 5
        assert "k_1" in auth_module._token_cache, "recently-read key must survive LRU eviction"
        assert "k_2" not in auth_module._token_cache, "least-recently-used key must be evicted"
        assert "k_new" in auth_module._token_cache
        assert "k_new2" in auth_module._token_cache

    def test_fifo_behavior_when_no_reads(self, monkeypatch):
        # Without any reads, the LRU end IS the least-recently-inserted end —
        # so the behavior collapses to the old FIFO policy. This guards against
        # regressions where a bad move_to_end placement would break FIFO.
        monkeypatch.setattr(auth_module, "_CACHE_MAX_SIZE", 3)

        _cache_put("a", _claims(1))
        _cache_put("b", _claims(2))
        _cache_put("c", _claims(3))
        # Insert one more without reading anything — 'a' should be evicted.
        _cache_put("d", _claims(4))

        assert "a" not in auth_module._token_cache
        assert set(auth_module._token_cache.keys()) == {"b", "c", "d"}

    def test_reput_on_existing_key_does_not_grow_cache(self, monkeypatch):
        # Guard against a regression where overwrite erroneously hit the
        # eviction branch and reduced the cache size.
        monkeypatch.setattr(auth_module, "_CACHE_MAX_SIZE", 3)

        _cache_put("a", _claims(1))
        _cache_put("b", _claims(2))
        _cache_put("c", _claims(3))
        # Re-put existing key — must not evict anything.
        _cache_put("b", _claims(22))

        assert set(auth_module._token_cache.keys()) == {"a", "b", "c"}
        assert _cache_get("b") == _claims(22)
