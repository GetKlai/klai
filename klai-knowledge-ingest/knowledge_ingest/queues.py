"""Single source of truth for procrastinate queue names.

Why this module exists
----------------------

Queue names previously lived as bare string literals scattered across every
task module **and** the worker subscription list in ``app.py``. Each addition
of a new queue required two coordinated edits — one for the task definition,
one for the worker — and the failure mode of forgetting either is silent:

* Forget the task ``queue="..."`` → task lands on the wrong queue.
* Forget the ``app.py`` ``queues=[...]`` list → worker never picks the task up,
  jobs pile up in ``status='todo'`` forever, the user-visible feature looks
  stuck with no error.

We have been bitten by this twice in 2026:

* SPEC-CONNECTOR-DELETE-LIFECYCLE-001 PR #253 — ``connector-purge`` task was
  added but the worker list update was missed in PR-A. User saw "deleting"
  state hang for hours.
* SPEC-INGEST-QUEUE-SEPARATION-001 (this SPEC) — opportunity to prevent any
  recurrence by collapsing both lists into one constants module.

Convention
----------

Every procrastinate task in this service MUST reference its queue via a
constant from this module. The worker MUST subscribe via ``ALL_QUEUES``.
A new queue is added by:

1. Adding a constant here.
2. Appending it to ``ALL_QUEUES``.
3. Importing the constant from the task module.

That's it. Worker subscription updates automatically.
"""

from __future__ import annotations

# --- Queue names ------------------------------------------------------------

CRAWL_JOBS = "crawl-jobs"
"""Web crawl orchestration (SPEC-INGEST-QUEUE-SEPARATION-001)."""

CONNECTOR_PURGE = "connector-purge"
"""Connector-delete orchestration (SPEC-CONNECTOR-DELETE-LIFECYCLE-001)."""

ENRICH_BULK = "enrich-bulk"
"""Bulk LLM enrichment for crawled/imported pages."""

ENRICH_INTERACTIVE = "enrich-interactive"
"""Single-document enrichment, foreground (drains first)."""

GRAPHITI_BULK = "graphiti-bulk"
"""LLM relation building → FalkorDB knowledge graph."""

INGEST_KB = "ingest-kb"
"""KB document ingest from external sources."""

RAG_EVAL = "rag-eval"
"""Nightly RAGAS evaluation harness (SPEC-RAG-EVAL-001). LLM-bound via judge calls."""

TAXONOMY_BACKFILL = "taxonomy-backfill"
"""One-shot backfill jobs (clustering, taxonomy)."""


# --- Worker lanes -----------------------------------------------------------
#
# SPEC-WORKER-LANES-001. Procrastinate has no per-queue fairness — when one
# worker subscribes to all queues at concurrency=N, FIFO across queues means
# a backlog of slow LLM jobs blocks fast I/O jobs even at high concurrency.
# We split queues into two lanes and run a dedicated worker process per lane:
#
# * I/O lane — HTTP, file ops, DB. Bursty user-triggered work, sub-second
#   per-task. Concurrency tuned for parallel HTTP fan-out.
# * LLM lane — Mistral calls via LiteLLM, 5-60 s per task. Concurrency
#   bounded by upstream rate limit (token bucket in ``graph.py``).
#
# Adding a new queue: pick the lane, append the constant to that lane's list.
# ``ALL_QUEUES`` is computed; the lane invariant is enforced by
# ``tests/test_queues_constants.py``.

IO_QUEUES: list[str] = [
    INGEST_KB,
    CONNECTOR_PURGE,
    CRAWL_JOBS,
]
"""Latency-sensitive I/O work — HTTP, file ops, DB. No LLM calls."""

LLM_QUEUES: list[str] = [
    ENRICH_INTERACTIVE,
    ENRICH_BULK,
    GRAPHITI_BULK,
    RAG_EVAL,
    TAXONOMY_BACKFILL,
]
"""Throughput-bound LLM work — bounded by upstream rate limit."""

ALL_QUEUES: list[str] = IO_QUEUES + LLM_QUEUES
"""Union of both lanes. Tests pin that every declared constant is in exactly
one lane and that ALL_QUEUES contains every constant."""
