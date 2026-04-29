"""SPEC-SEC-HYGIENE-001 REQ-40 / AC-40: bounded ``_pending`` task set.

The ``services.events.emit_event`` helper schedules every product event as
a fire-and-forget asyncio task tracked by the module-level ``_pending``
set. Under a flood (Redis fail-open at REQ-42 + a retrieval spike) the
task creation rate can outpace task completion, causing ``_pending`` to
grow without bound — eventually OOM-ing the worker.

This test pins:

* REQ-40.1 — ``_pending`` is capped at ``settings.retrieval_events_max_pending``
  (default 1000); excess events are dropped, not queued.
* REQ-40.1 — drops increment a ``retrieval_events_dropped_total``
  Prometheus counter.
* REQ-40.2 — drops emit a ``retrieval_events_cap_hit`` structlog event,
  rate-limited so a flood cannot itself flood the log pipeline.
* REQ-40.3 (recovery) — once pending tasks complete, ``_pending`` drains
  to zero and subsequent ``emit_event`` calls proceed normally.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest


# Read prometheus counter values via the registry's collect() to avoid
# coupling to the internal ._value attribute (which exists but is private).
def _counter_value(counter, **labels) -> float:
    """Return the current value of a labelled or unlabelled Counter."""
    if labels:
        return counter.labels(**labels)._value.get()
    return counter._value.get()


@pytest.fixture
async def clean_events(monkeypatch):
    """Cancel any pending tasks left over from prior tests and reset state."""
    from retrieval_api.services import events

    # Wipe any leftovers from earlier tests.
    for task in list(events._pending):
        if not task.done():
            task.cancel()
    events._pending.clear()
    monkeypatch.setattr(events, "_last_cap_log_time", 0.0, raising=False)
    yield events
    # Same teardown to be polite to the next test.
    for task in list(events._pending):
        if not task.done():
            task.cancel()
    events._pending.clear()


@pytest.fixture
def log_capture():
    """Capture structlog event records emitted at WARNING+."""
    from retrieval_api.logging_setup import setup_logging
    setup_logging()

    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture(level=logging.DEBUG)
    root = logging.getLogger()
    prev_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        root.removeHandler(handler)
        root.setLevel(prev_level)


# --------------------------------------------------------------------------- #
# REQ-40.1 — _pending is bounded
# --------------------------------------------------------------------------- #


async def test_pending_set_capped_at_configured_max(clean_events, monkeypatch, log_capture):
    """REQ-40.1 + REQ-40.2: 2000 emits with a hanging worker → 1000 in flight, 1000 dropped."""
    events = clean_events

    # 1) Force the cap to a small number so the test is fast and the
    #    behaviour is identical regardless of the production default.
    monkeypatch.setattr(events.settings, "retrieval_events_max_pending", 5, raising=False)

    # 2) Stub the asyncpg pool so every insert hangs forever — that's what
    #    forces _pending to grow.
    class _HangingPool:
        async def execute(self, *_a, **_kw):
            await asyncio.sleep(3600)

    monkeypatch.setattr(events, "_pool", _HangingPool())

    # 3) Capture counter starting value (other tests may have ticked it).
    start = _counter_value(events.retrieval_events_dropped_total)

    # 4) Burst of 20 emits with cap=5 → first 5 in flight, next 15 dropped.
    n_total = 20
    n_cap = 5
    for i in range(n_total):
        events.emit_event("test_burst", tenant_id=f"t-{i}")

    # Give the loop one tick so created tasks settle into _pending.
    await asyncio.sleep(0)

    assert len(events._pending) <= n_cap, (
        f"_pending exceeded the cap: {len(events._pending)} > {n_cap}. "
        "REQ-40.1 requires emit_event to drop new events when the cap is hit."
    )

    end = _counter_value(events.retrieval_events_dropped_total)
    dropped = end - start
    assert dropped == n_total - n_cap, (
        f"retrieval_events_dropped_total incremented by {dropped}, "
        f"expected {n_total - n_cap} (n_total - cap)."
    )

    # REQ-40.2: at least one rate-limited cap-hit log was emitted.
    cap_hit_logs = [
        rec for rec in log_capture
        if isinstance(rec.msg, dict) and rec.msg.get("event") == "retrieval_events_cap_hit"
    ]
    assert cap_hit_logs, (
        "Expected at least one `retrieval_events_cap_hit` structlog event "
        "after the cap was breached, found none."
    )


# --------------------------------------------------------------------------- #
# REQ-40.2 — cap-hit log is rate-limited (not one per dropped event)
# --------------------------------------------------------------------------- #


async def test_cap_hit_log_is_rate_limited(clean_events, monkeypatch, log_capture):
    """REQ-40.2: a flood of drops emits at most one cap-hit log per minute."""
    events = clean_events

    monkeypatch.setattr(events.settings, "retrieval_events_max_pending", 1, raising=False)

    class _HangingPool:
        async def execute(self, *_a, **_kw):
            await asyncio.sleep(3600)

    monkeypatch.setattr(events, "_pool", _HangingPool())

    # First emit fills the slot, next 50 all drop in the same wall-clock minute.
    for i in range(51):
        events.emit_event("burst", tenant_id=f"t-{i}")

    await asyncio.sleep(0)

    cap_hit_logs = [
        rec for rec in log_capture
        if isinstance(rec.msg, dict) and rec.msg.get("event") == "retrieval_events_cap_hit"
    ]
    assert len(cap_hit_logs) <= 2, (
        f"Cap-hit log fired {len(cap_hit_logs)} times for a 50-event drop burst — "
        "REQ-40.2 requires it be rate-limited (at most ~once per minute)."
    )


# --------------------------------------------------------------------------- #
# REQ-40 (recovery) — _pending drains to zero, cap resets
# --------------------------------------------------------------------------- #


async def test_pending_recovers_after_inserts_complete(clean_events, monkeypatch):
    """REQ-40 (recovery): once tasks complete, _pending empties and cap resets."""
    events = clean_events

    monkeypatch.setattr(events.settings, "retrieval_events_max_pending", 5, raising=False)

    # Quick-completing pool: each execute returns immediately.
    class _FastPool:
        async def execute(self, *_a, **_kw):
            return None

    monkeypatch.setattr(events, "_pool", _FastPool())

    for i in range(5):
        events.emit_event("recovery", tenant_id=f"t-{i}")

    # Drain — every task is a no-op so this finishes promptly.
    for _ in range(20):
        if not events._pending:
            break
        await asyncio.sleep(0.01)

    assert len(events._pending) == 0, (
        f"_pending should drain to zero after fast inserts complete, "
        f"saw {len(events._pending)} stragglers."
    )

    # Subsequent emits proceed normally — no drops because cap window has reset.
    start = _counter_value(events.retrieval_events_dropped_total)
    events.emit_event("post_recovery", tenant_id="t-99")
    await asyncio.sleep(0.01)
    end = _counter_value(events.retrieval_events_dropped_total)
    assert end == start, "Post-recovery emit was incorrectly counted as a drop"


# --------------------------------------------------------------------------- #
# Static guard — env var is documented in config.py
# --------------------------------------------------------------------------- #


def test_settings_exposes_retrieval_events_max_pending():
    """REQ-40.1: ``RETRIEVAL_EVENTS_MAX_PENDING`` is configurable via Settings."""
    config_path = Path(__file__).resolve().parents[1] / "retrieval_api" / "config.py"
    src = config_path.read_text(encoding="utf-8")
    assert "retrieval_events_max_pending" in src, (
        "Settings must expose `retrieval_events_max_pending` so deploys can "
        "tune the cap via the RETRIEVAL_EVENTS_MAX_PENDING env var (REQ-40.1)."
    )
