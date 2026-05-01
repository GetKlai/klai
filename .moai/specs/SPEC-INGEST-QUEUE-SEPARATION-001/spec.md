---
id: SPEC-INGEST-QUEUE-SEPARATION-001
version: "1.0"
status: draft
created: 2026-05-01
updated: 2026-05-01
author: Mark Vletter
priority: high
issue_number: 0
---

## HISTORY

| Version | Date | Author | Change |
|---------|------|--------|--------|
| 1.0 | 2026-05-01 | Mark Vletter | Initial draft. Observed during Voys-tenant e2e test on 2026-05-01: a Notion sync (120 pages → 120 LLM enrichment jobs on `enrich-bulk`) blocked a subsequent Redcactus crawl for 20+ minutes because `crawl_tasks.run_crawl` shared the same queue. |

---

# SPEC-INGEST-QUEUE-SEPARATION-001: Dedicate `crawl-jobs` queue for the crawl orchestration task

## Context

Procrastinate-based async work in `klai-knowledge-ingest` runs on five queues today:

| Queue | Workload | Latency per job |
|---|---|---|
| `ingest-kb` | KB document ingest | seconds |
| `enrich-interactive` | single-doc enrichment, foreground | 5-30 s |
| `enrich-bulk` | bulk enrichment **AND** the crawl orchestrator | 5-60 s |
| `graphiti-bulk` | LLM relation building → FalkorDB | 30-60 s |
| `taxonomy-backfill` | one-shot backfills | varies |
| `connector-purge` | SPEC-CONNECTOR-DELETE-LIFECYCLE-001 | seconds |

`crawl_tasks.run_crawl` was registered with `queue="enrich-bulk"`. That task is
I/O-bound (httpx + crawl4ai) and finishes in ~30-60 s for 20 pages. Mixing
it with LLM-bound enrichment that runs at 30-60 s per **single** task means
any subsequent crawl waits behind the entire LLM backlog.

Concrete repro from 2026-05-01 (Voys e2e):

1. 09:57 UTC — Notion connector sync triggered, ingests 120 pages, enqueues 120 `enrich_document_bulk` jobs onto `enrich-bulk`.
2. 09:59 UTC — Redcactus connector sync triggered. The new `run_crawl` job lands on `enrich-bulk` as #58 in line.
3. 10:14 UTC — Redcactus selector fix re-trigger. The new crawl job lands as #58 again (queue still draining).
4. ~30 minutes pass before the worker reaches the crawl job.

The first time this trips a user, the natural reaction is "the sync is broken".
Diagnosis is non-obvious: sync_run shows `running`, no errors, just silence.

## Scope

In scope:

- `klai-knowledge-ingest/knowledge_ingest/crawl_tasks.py` — change `run_crawl` task queue to `crawl-jobs`.
- `klai-knowledge-ingest/knowledge_ingest/app.py` — subscribe the worker to the new queue.
- `klai-knowledge-ingest/knowledge_ingest/enrichment_tasks.py` — module docstring update to reflect the split.
- Unit test that pins the queue name, so a future refactor cannot silently regress this.

Out of scope:

- Reworking the entire queue topology. Other queues stay as they are.
- Per-queue concurrency tuning. Default procrastinate concurrency stays.
- klai-connector / portal-api changes. The HTTP API surface (`POST /ingest/v1/crawl/sync`) is unchanged — this is a worker-internal routing change.

---

## Requirements (EARS)

### REQ-1 (Dedicated queue for crawl orchestration)

**WHEN** the procrastinate task `knowledge_ingest.crawl_tasks.run_crawl` is registered,
**THE SYSTEM SHALL** register it with `queue="crawl-jobs"`.

### REQ-2 (Worker subscription)

`klai-knowledge-ingest` worker SHALL subscribe to `"crawl-jobs"` in addition to its existing queue list (`ingest-kb`, `enrich-interactive`, `enrich-bulk`, `graphiti-bulk`, `taxonomy-backfill`, `connector-purge`).

### REQ-3 (No queue argument leakage)

The two known call sites (`routes/crawl_sync.py::enqueue_crawl_sync` and `routes/knowledge.py::trigger_bulk_crawl`) SHALL continue calling `proc_app.run_crawl.defer_async(...)` without an explicit `queue=` argument. The queue default flows from the task definition — call sites stay queue-agnostic.

### REQ-4 (Test pin)

A unit test SHALL assert that `run_crawl` is registered with queue name `"crawl-jobs"`. A future refactor that accidentally moves the task back onto `enrich-bulk` (or onto any other queue) MUST fail this test.

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Existing queued `run_crawl` jobs on `enrich-bulk` get stranded | Worker still subscribes to `enrich-bulk`. Stranded jobs continue to run there until drained; new jobs land on `crawl-jobs`. No re-routing of in-flight rows needed. |
| Worker concurrency limits split badly | Procrastinate defaults are per-worker, not per-queue. Adding a queue does not reduce throughput on the existing ones. Empirically: worker handles all 6 queues today; adding a 7th has no measurable cost. |
| Renaming a queue breaks observability dashboards | Dashboards filter by queue name. Adding `crawl-jobs` may need a Grafana panel update later — tracked as a follow-up, not in this SPEC. |

---

## Success Criteria

- After deploy: `SELECT queue_name FROM procrastinate_jobs WHERE task_name = 'knowledge_ingest.crawl_tasks.run_crawl' AND status = 'todo' AND id > <pre-deploy-max-id>` returns rows with `queue_name = 'crawl-jobs'` only.
- Re-trigger the Voys Redcactus sync after deploy: the new job lands on `crawl-jobs` and runs within seconds, not waiting on the `enrich-bulk` backlog.
- Unit test in `tests/test_crawl_tasks_queue.py` (or merged into existing crawl-task tests) passes.
- ruff check + ruff format clean on modified files.

---

## Out of scope (explicit non-goals)

- Reusing this pattern for klai-connector or other services — that's a separate queue topology decision.
- Adding per-queue worker concurrency knobs — current defaults are fine.
- Changing the `connector-purge` queue (already separated by SPEC-CONNECTOR-DELETE-LIFECYCLE-001).
- Changing the Procrastinate library version.
