"""Invariants for ``knowledge_ingest.queues``.

SPEC-INGEST-QUEUE-SEPARATION-001 REQ-2 + REQ-4. The whole point of the
queues module is to make "task added but worker subscription forgotten"
mechanically impossible. These tests guard the contract.
"""

from __future__ import annotations

from knowledge_ingest import queues


def test_all_queues_contains_every_string_constant():
    """Every uppercase str constant in queues.py MUST be in ALL_QUEUES.

    Catches the "added a queue, forgot to append it to ALL_QUEUES" bug.
    """
    declared = {
        value for name, value in vars(queues).items() if name.isupper() and isinstance(value, str)
    }
    assert declared == set(queues.ALL_QUEUES), (
        f"Constants drift from ALL_QUEUES.\n"
        f"  declared but not in ALL_QUEUES: {declared - set(queues.ALL_QUEUES)}\n"
        f"  in ALL_QUEUES but not declared: {set(queues.ALL_QUEUES) - declared}"
    )


def test_no_duplicate_queue_names():
    """Each queue name must be unique.

    Two constants pointing to the same string are a copy-paste bug.
    """
    assert len(queues.ALL_QUEUES) == len(set(queues.ALL_QUEUES))


def test_crawl_jobs_queue_is_separate():
    """SPEC-INGEST-QUEUE-SEPARATION-001 REQ-1: crawl-jobs is its own queue.

    Pin the constant value so a future refactor cannot silently move
    crawl orchestration back onto enrich-bulk and re-introduce the
    head-of-line blocking bug we just fixed.
    """
    assert queues.CRAWL_JOBS == "crawl-jobs"
    assert queues.CRAWL_JOBS != queues.ENRICH_BULK
    assert queues.CRAWL_JOBS != queues.ENRICH_INTERACTIVE
    assert queues.CRAWL_JOBS != queues.GRAPHITI_BULK


def test_connector_purge_queue_remains_distinct():
    """SPEC-CONNECTOR-DELETE-LIFECYCLE-001: connector-purge is its own queue."""
    assert queues.CONNECTOR_PURGE == "connector-purge"
    assert queues.CONNECTOR_PURGE not in (
        queues.ENRICH_BULK,
        queues.ENRICH_INTERACTIVE,
        queues.GRAPHITI_BULK,
        queues.CRAWL_JOBS,
    )


def test_all_queue_values_use_kebab_case():
    """Procrastinate convention + dashboards: kebab-case only."""
    for q in queues.ALL_QUEUES:
        assert q == q.lower(), f"queue name not lowercase: {q!r}"
        assert "_" not in q, f"queue name uses underscore instead of hyphen: {q!r}"
        assert " " not in q, f"queue name contains whitespace: {q!r}"
