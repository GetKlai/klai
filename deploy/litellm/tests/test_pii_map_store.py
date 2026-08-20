"""Tests for klai_pii_map_store.py (SPEC-PRIVACY-MISTRAL-PII-001 REQ-11)."""

from __future__ import annotations

import pytest

from klai_pii_map_store import PiiMapStore


def test_put_then_get_round_trips():
    store = PiiMapStore()
    store.put("call-1", {"<PERSON_1>": "Jan de Vries"})
    assert store.get("call-1") == {"<PERSON_1>": "Jan de Vries"}


def test_get_missing_call_id_returns_none():
    store = PiiMapStore()
    assert store.get("does-not-exist") is None


def test_get_is_non_destructive_until_discard():
    store = PiiMapStore()
    store.put("call-1", {"<PERSON_1>": "Jan de Vries"})
    store.get("call-1")
    store.get("call-1")
    assert store.get("call-1") == {"<PERSON_1>": "Jan de Vries"}


def test_discard_removes_entry():
    store = PiiMapStore()
    store.put("call-1", {"<PERSON_1>": "Jan de Vries"})
    store.discard("call-1")
    assert store.get("call-1") is None


def test_discard_on_success_path():
    """REQ-11: entry deleted when the stream ends on the success path."""
    store = PiiMapStore()
    store.put("call-1", {"<PERSON_1>": "Jan de Vries"})
    # Simulate: hook restores using the map, then discards at stream end.
    restore_map = store.get("call-1")
    assert restore_map is not None
    store.discard("call-1")
    assert "call-1" not in store


def test_discard_on_error_path_too():
    """REQ-11: entry deleted on the error path alike."""
    store = PiiMapStore()
    store.put("call-1", {"<PERSON_1>": "Jan de Vries"})
    try:
        raise RuntimeError("upstream failure mid-stream")
    except RuntimeError:
        store.discard("call-1")
    assert store.get("call-1") is None


def test_discard_missing_call_id_is_a_no_op():
    store = PiiMapStore()
    store.discard("never-existed")  # must not raise


def test_returned_mapping_is_a_copy_not_the_live_dict():
    store = PiiMapStore()
    original = {"<PERSON_1>": "Jan de Vries"}
    store.put("call-1", original)
    fetched = store.get("call-1")
    fetched["<PERSON_1>"] = "tampered"
    assert store.get("call-1") == {"<PERSON_1>": "Jan de Vries"}


# ---------------------------------------------------------------------------
# TTL sweep — a client disconnecting mid-stream must not leak forever
# ---------------------------------------------------------------------------
def test_ttl_sweep_drops_a_stale_entry():
    clock = {"now": 0.0}
    store = PiiMapStore(ttl_seconds=10.0, clock=lambda: clock["now"])
    store.put("call-1", {"<PERSON_1>": "Jan de Vries"})

    clock["now"] = 5.0
    assert store.get("call-1") is not None  # still fresh

    clock["now"] = 20.0  # past the 10s TTL
    assert store.get("call-1") is None  # swept lazily on access


def test_ttl_sweep_runs_on_put_too():
    clock = {"now": 0.0}
    store = PiiMapStore(ttl_seconds=10.0, clock=lambda: clock["now"])
    store.put("call-1", {"<PERSON_1>": "Jan de Vries"})

    clock["now"] = 20.0
    store.put("call-2", {"<PERSON_1>": "Marieke Bakker"})  # triggers a sweep

    assert len(store) == 1
    assert "call-1" not in store
    assert store.get("call-2") == {"<PERSON_1>": "Marieke Bakker"}


def test_explicit_sweep_reports_count_removed():
    clock = {"now": 0.0}
    store = PiiMapStore(ttl_seconds=10.0, clock=lambda: clock["now"])
    store.put("call-1", {})
    store.put("call-2", {})
    clock["now"] = 20.0
    removed = store.sweep()
    assert removed == 2
    assert len(store) == 0


def test_fresh_entries_survive_a_sweep_that_finds_nothing_stale():
    clock = {"now": 0.0}
    store = PiiMapStore(ttl_seconds=10.0, clock=lambda: clock["now"])
    store.put("call-1", {"<PERSON_1>": "Jan de Vries"})
    clock["now"] = 1.0
    assert store.sweep() == 0
    assert store.get("call-1") is not None


# ---------------------------------------------------------------------------
# Bounded size — oldest-first eviction (REQ-11)
# ---------------------------------------------------------------------------
def test_store_is_bounded_and_evicts_oldest_first():
    store = PiiMapStore(max_entries=2, ttl_seconds=10_000.0)
    store.put("call-1", {"a": "1"})
    store.put("call-2", {"b": "2"})
    store.put("call-3", {"c": "3"})  # over capacity -> evict call-1 (oldest)

    assert len(store) == 2
    assert store.get("call-1") is None
    assert store.get("call-2") == {"b": "2"}
    assert store.get("call-3") == {"c": "3"}


def test_eviction_keeps_evicting_until_room_exists():
    store = PiiMapStore(max_entries=1, ttl_seconds=10_000.0)
    store.put("call-1", {"a": "1"})
    store.put("call-2", {"b": "2"})
    store.put("call-3", {"c": "3"})

    assert len(store) == 1
    assert store.get("call-3") == {"c": "3"}
    assert store.get("call-1") is None
    assert store.get("call-2") is None


def test_replacing_an_existing_call_id_does_not_count_as_growth():
    store = PiiMapStore(max_entries=2, ttl_seconds=10_000.0)
    store.put("call-1", {"a": "1"})
    store.put("call-2", {"b": "2"})
    store.put("call-1", {"a": "1-updated"})  # replace, not a 3rd entry

    assert len(store) == 2
    assert store.get("call-1") == {"a": "1-updated"}
    assert store.get("call-2") == {"b": "2"}


def test_never_grows_without_limit_under_sustained_traffic():
    store = PiiMapStore(max_entries=50, ttl_seconds=10_000.0)
    for i in range(1000):
        store.put(f"call-{i}", {"x": str(i)})
    assert len(store) <= 50


# ---------------------------------------------------------------------------
# Construction guards
# ---------------------------------------------------------------------------
def test_max_entries_must_be_positive():
    with pytest.raises(ValueError):
        PiiMapStore(max_entries=0)


def test_ttl_seconds_must_be_positive():
    with pytest.raises(ValueError):
        PiiMapStore(ttl_seconds=0)


def test_put_requires_non_empty_call_id():
    store = PiiMapStore()
    with pytest.raises(ValueError):
        store.put("", {"a": "1"})


# ---------------------------------------------------------------------------
# No serialization surface at all (REQ-11: never written to Redis/PG/disk/log)
# ---------------------------------------------------------------------------
def test_store_exposes_no_serialization_method():
    store = PiiMapStore()
    for forbidden in ("to_json", "to_dict", "serialize", "dump", "save"):
        assert not hasattr(store, forbidden)
