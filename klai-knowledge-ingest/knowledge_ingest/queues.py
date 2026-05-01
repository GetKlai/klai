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

INGEST_KB = "ingest-kb"
"""KB document ingest from external sources."""

ENRICH_INTERACTIVE = "enrich-interactive"
"""Single-document enrichment, foreground (drains first)."""

ENRICH_BULK = "enrich-bulk"
"""Bulk LLM enrichment for crawled/imported pages."""

GRAPHITI_BULK = "graphiti-bulk"
"""LLM relation building → FalkorDB knowledge graph."""

TAXONOMY_BACKFILL = "taxonomy-backfill"
"""One-shot backfill jobs (clustering, taxonomy)."""

CONNECTOR_PURGE = "connector-purge"
"""Connector-delete orchestration (SPEC-CONNECTOR-DELETE-LIFECYCLE-001)."""

CRAWL_JOBS = "crawl-jobs"
"""Web crawl orchestration (SPEC-INGEST-QUEUE-SEPARATION-001)."""


# --- Worker subscription ----------------------------------------------------

ALL_QUEUES: list[str] = [
    INGEST_KB,
    ENRICH_INTERACTIVE,
    ENRICH_BULK,
    GRAPHITI_BULK,
    TAXONOMY_BACKFILL,
    CONNECTOR_PURGE,
    CRAWL_JOBS,
]
"""All queues this service's worker must subscribe to.

Used by ``knowledge_ingest.app.lifespan`` when starting the procrastinate
worker. Adding a queue constant above without appending it here is a bug
caught by ``tests/test_queues_constants.py``.
"""
