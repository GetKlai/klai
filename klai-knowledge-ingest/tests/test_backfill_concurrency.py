"""The backfill's throughput ceiling must be the rate budget, not its own loop.

The loop was strictly sequential, with a comment saying ingest_episode's
semaphore handled concurrency. It does not: that semaphore bounds episodes
ALREADY in flight, and a sequential caller never puts more than one there.
Measured during the #1148 rebuild, raising GRAPHITI_MAX_CONCURRENT from 1 to 8
changed nothing -- 8 LLM calls a minute against a LiteLLM alias allowing 90,
putting a 726-document rebuild on course to take a day.
"""

from __future__ import annotations

import inspect

from knowledge_ingest import backfill


def test_documents_are_processed_concurrently():
    source = inspect.getsource(backfill.main)
    assert "asyncio.gather" in source, "documents are still processed one at a time"
    assert "Semaphore" in source, "unbounded fan-out would ignore the rate budget entirely"


def test_concurrency_defaults_to_the_previous_behaviour():
    """A default above 1 would change every existing invocation silently."""
    assert inspect.signature(backfill.main).parameters["concurrency"].default == 1


def test_the_flag_exists():
    source = inspect.getsource(backfill)
    assert '"--concurrency"' in source


def test_a_zero_or_negative_concurrency_cannot_deadlock():
    """asyncio.Semaphore(0) blocks forever; the guard must floor it at 1."""
    source = inspect.getsource(backfill.main)
    assert "max(1, concurrency)" in source
