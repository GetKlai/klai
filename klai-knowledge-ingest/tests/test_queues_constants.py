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


# --- SPEC-WORKER-LANES-001 invariants ---------------------------------------


def test_lanes_partition_all_queues():
    """Every queue MUST belong to exactly one lane (I/O xor interactive xor LLM).

    A queue that escapes all lanes silently disables itself: no worker
    subscribes to it. A queue that lands in two lanes is double-handled
    by competing worker processes and the lane SLAs collapse. Both are
    silent failures — pin the partition mechanically.
    """
    io = set(queues.IO_QUEUES)
    interactive = set(queues.INTERACTIVE_QUEUES)
    llm = set(queues.LLM_QUEUES)
    all_q = set(queues.ALL_QUEUES)

    assert io & llm == set(), f"queue belongs to both I/O and LLM lanes: {io & llm}"
    assert io & interactive == set(), f"queue in both I/O and interactive: {io & interactive}"
    assert interactive & llm == set(), f"queue in both interactive and LLM: {interactive & llm}"
    assert io | interactive | llm == all_q, (
        f"lanes do not cover ALL_QUEUES.\n"
        f"  in ALL_QUEUES but not in any lane: {all_q - (io | interactive | llm)}\n"
        f"  in lanes but not in ALL_QUEUES: {(io | interactive | llm) - all_q}"
    )


def test_all_queues_is_lanes_concatenated_in_order():
    """``ALL_QUEUES = IO_QUEUES + INTERACTIVE_QUEUES + LLM_QUEUES`` (order
    matters: latency-sensitive lanes first reflects the architecture).

    Order matters because ``test_all_queues_contains_every_string_constant``
    + downstream tests rely on the union being deterministic.
    """
    assert queues.ALL_QUEUES == queues.IO_QUEUES + queues.INTERACTIVE_QUEUES + queues.LLM_QUEUES


def test_enrich_interactive_has_its_own_lane():
    """User-triggered re-syncs must not share a lane with bulk work.

    Procrastinate fetches the oldest todo across a worker's whole queue
    set — with ENRICH_INTERACTIVE inside the LLM lane, a bulk crawl's
    backlog of hundreds of enrich-bulk/graphiti-bulk jobs made a single
    user-requested reindex wait for hours (intermedia.com incident,
    2026-08-14).
    """
    assert queues.INTERACTIVE_QUEUES == [queues.ENRICH_INTERACTIVE]
    assert queues.ENRICH_INTERACTIVE not in queues.LLM_QUEUES
    assert queues.ENRICH_INTERACTIVE not in queues.IO_QUEUES


def test_crawl_jobs_lives_in_io_lane():
    """SPEC-WORKER-LANES-001 REQ-1: crawl orchestration is I/O-bound."""
    assert queues.CRAWL_JOBS in queues.IO_QUEUES
    assert queues.CRAWL_JOBS not in queues.LLM_QUEUES


def test_graphiti_bulk_lives_in_llm_lane():
    """SPEC-WORKER-LANES-001 REQ-1: graphiti episode ingest IS LLM-bound."""
    assert queues.GRAPHITI_BULK in queues.LLM_QUEUES
    assert queues.GRAPHITI_BULK not in queues.IO_QUEUES


def test_connector_purge_lives_in_io_lane():
    """SPEC-WORKER-LANES-001 REQ-1: purge orchestration is DB/HTTP only."""
    assert queues.CONNECTOR_PURGE in queues.IO_QUEUES
    assert queues.CONNECTOR_PURGE not in queues.LLM_QUEUES
