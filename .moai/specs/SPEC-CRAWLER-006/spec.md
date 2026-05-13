---
id: SPEC-CRAWLER-006
version: "0.2.0"
status: implemented
created: 2026-05-05
updated: 2026-05-05
author: Mark Vletter
priority: high
related:
  - SPEC-CRAWLER-004 (introduces the delegation poll pattern this SPEC replaces)
  - SPEC-WORKER-LANES-001 (introduces best-effort cancel — superseded here)
roadmap: docs/architecture/knowledge-ingest-flow.md
---

## HISTORY

| Version | Date | Author | Change |
|---------|------|--------|--------|
| 0.1.0 | 2026-05-05 | Mark Vletter | Initial draft after Voys/Redcactus 1-mei incident analysis. |
| 0.2.0 | 2026-05-05 | Mark Vletter | Implemented REQ-01..06 + REQ-07 (one-shot SQL on Voys/Redcactus). REQ-08 frontend deferred. Pushed direct to main, deployed to core-01. Confirmed live: `sync_run_reaper_started` + `Cleaned up stuck RUNNING sync_runs on startup (delegated runs preserved)` events at startup. |

# SPEC-CRAWLER-006: Fire-and-forget web_crawler delegation

## Context

SPEC-CRAWLER-004 Fase D delegated web_crawler execution from `klai-connector`
to `knowledge-ingest`. The delegation kept a synchronous polling loop on the
connector side (`sync_engine.py:579-643`):

1. Submit job → store `remote_job_id` → status `RUNNING`.
2. Poll `/crawl/sync/{job_id}/status` every 5s up to 30 min.
3. On terminal status: copy `pages_done` / `pages_total` / `error` into
   `connector.sync_runs` and close the row.
4. On timeout: mark `sync_run` as `FAILED` with
   `error.error = 'web_crawler_poll_timeout'` and best-effort `POST /cancel`
   the procrastinate task.

The pattern fails on any crawl that takes longer than 30 min. SPEC-WORKER-LANES-001
acknowledged this divergence and added the cancel call as a workaround. The cancel
is best-effort and does not abort an already-running procrastinate task — only
prevents new ones. Production incident on 2026-05-01:

| Time UTC | Event |
|---|---|
| 12:03:28 | User triggers `Sync now` on Voys/`support`/Redcactus (`fdde0c1e`). klai-connector enqueues job `4adc7afd`, starts polling. |
| 12:33:36 | Poll timeout fires. `sync_runs.status = 'failed'`, `error = 'web_crawler_poll_timeout'`. Best-effort cancel sent. |
| 12:48:38 | knowledge-ingest's crawler emits `crawl_job_complete` with `pages_done: 368, pages_failed: 0`. All 368 artifacts persisted, all 3,548 Qdrant chunks ingested. |

The data lands. The UI lies. The user sees `failed` for 4+ days while the KB serves
the data correctly in chat.

## Why the current architecture is the problem

There are two writers, both claiming authority over "did the crawl succeed":

- `connector.sync_runs.status` — written by klai-connector's poll loop.
- `knowledge.crawl_jobs.status` — written by knowledge-ingest's procrastinate task.

These are coupled by polling and a fixed deadline. Whenever the deadline differs
from the actual job duration — which is most of the time, because crawl duration
depends on page count, rate-limit, and target server latency, none of which the
connector knows — the two states diverge. Cancel is structurally unable to fix it
because procrastinate cannot abort a running task.

## Goal

Eliminate the divergence by making `knowledge.crawl_jobs` the single source of
truth for web_crawler sync status. `connector.sync_runs` becomes a derived view:
its `status`, `documents_ok`, and `documents_total` are resolved on read by
querying knowledge-ingest, not by writing locally on poll completion.

## Scope

### In scope

- klai-connector: replace poll-and-timeout pattern with fire-and-forget enqueue.
- klai-connector: live status resolution for `running` web_crawler sync_runs at
  read time (UI list, UI detail).
- klai-connector: background reaper that finalizes long-running web_crawler
  sync_runs (>24h since enqueue) by polling knowledge-ingest one final time.
- klai-connector: alembic migration that backfills sync_runs failed by
  `web_crawler_poll_timeout` since SPEC-CRAWLER-004 deployment, by querying
  knowledge.crawl_jobs for the truth.
- portal-api: pass-through endpoint that exposes the live status to the
  frontend (no schema change).
- klai-portal/frontend: render live status for `running` runs, no behaviour
  change for terminal runs.
- Removal of `crawl_sync_cancel` callsite in sync_engine. Endpoint stays in
  knowledge-ingest for future use, but klai-connector no longer calls it.

### Out of scope

- Other connector types (notion, github, etc.). Their adapters run inside
  klai-connector itself and do not have this divergence.
- Webhook callbacks from knowledge-ingest. Considered and rejected — adds an
  inter-service contract with auth + retry overhead for no extra correctness
  beyond what lazy-resolve provides.
- Adaptive (heartbeat-based) polling timeouts. Considered and rejected — keeps
  two writers, just with a smarter timeout heuristic.

## Requirements (EARS)

### REQ-CRAWLER-006-01 — Fire-and-forget enqueue

When `_run_web_crawler_delegation` is invoked and the enqueue to
knowledge-ingest succeeds, klai-connector MUST persist the
`remote_job_id` on `sync_run.cursor_state` and set `sync_run.status =
RUNNING`, then return without polling.

### REQ-CRAWLER-006-02 — No synchronous wait

klai-connector MUST NOT call `crawl_sync_status` during
`_run_web_crawler_delegation`. The poll loop in
`sync_engine.py:579-603` MUST be removed.

### REQ-CRAWLER-006-03 — No cancel on timeout path

klai-connector MUST NOT call `crawl_sync_cancel` from
`_run_web_crawler_delegation`. The timeout path MUST be removed entirely
because there is no longer a timeout.

### REQ-CRAWLER-006-04 — Live status resolution

When portal-api requests sync_run state for a web_crawler run with
`status = RUNNING`, klai-connector MUST resolve the live state by
calling knowledge-ingest's `/crawl/sync/{remote_job_id}/status` and
return the merged shape:
- If knowledge-ingest returns `status = running`: derived
  `pages_done` and `pages_total` from the live response. sync_run row
  unchanged in DB.
- If knowledge-ingest returns `status = completed` or `failed`:
  klai-connector MUST update the local sync_run row to match (status,
  documents_ok, documents_total, error_details) and return the
  finalized shape.

### REQ-CRAWLER-006-05 — Live resolution caching

The live-resolution call from REQ-04 MUST be cached for 30 seconds per
`remote_job_id` to avoid request amplification on UI list views that
poll every 5s. Cache invalidation on terminal status transition is
allowed but not required (the next request after 30s catches the
terminal state).

### REQ-CRAWLER-006-06 — Reaper for orphan running rows

A background task in klai-connector MUST run every 5 minutes and
finalize any web_crawler sync_run with `status = RUNNING` and
`started_at` older than 24 hours by calling `crawl_sync_status` once.
- If knowledge-ingest returns terminal: write final state to sync_run.
- If knowledge-ingest returns 404 (`job_not_found`): mark
  sync_run as `FAILED` with `error = 'remote_job_lost'`.
- If knowledge-ingest returns running: leave the sync_run row
  unchanged; reaper retries on next tick. After 7 days, force-fail with
  `error = 'remote_job_stuck'`.

### REQ-CRAWLER-006-07 — Backfill of historical timeouts

An alembic migration MUST scan `connector.sync_runs` for rows where
`status = 'failed'` AND `error_details::jsonb @> '[{"error":
"web_crawler_poll_timeout"}]'`, then:
- Extract `cursor_state.remote_job_id`.
- Query knowledge.crawl_jobs in the same DB (cross-schema).
- If `crawl_jobs.status = completed`: rewrite the sync_run with
  `status = 'completed'`, `documents_ok = pages_done`, clear
  error_details. Add an audit row to a new
  `connector.sync_run_corrections` table.
- If `crawl_jobs.status = failed` or row missing: leave sync_run as-is
  but log the discrepancy.

### REQ-CRAWLER-006-08 — Frontend renders live status

The connector edit page (`/app/knowledge/<kb>/edit-connector/<id>`)
and the connector list page MUST render the live status for runs in
`running` state, including `pages_done / pages_total` when both are
populated. Format: `Bezig — <done>/<total> pagina's` for crawler
specifically; existing renderers untouched for other connector types.

## Non-requirements

- The reaper MUST NOT call `crawl_sync_cancel`. We trust knowledge-ingest's
  own lifecycle to terminate jobs eventually.
- klai-connector MUST NOT change `connector.sync_runs` schema. The
  state-derivation is computed at read time, not stored.
- portal-api MUST NOT cache the live status. Caching is a klai-connector
  responsibility (REQ-05) so all consumers benefit uniformly.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| UI list view rendering N sync_runs in `running` triggers N HTTP calls per render. | REQ-05 caches per `remote_job_id` for 30s. UI poll interval is 5s, so cache hit rate ≥ 5/6 in steady state. |
| knowledge-ingest is down → live resolve fails → UI shows nothing. | Fallback: if `crawl_sync_status` fails, return the stale local sync_run row with a `live_resolution_failed: true` flag. UI shows "Bezig (status onbekend)" instead of crashing. |
| Reaper marks healthy long-running crawls as `remote_job_stuck` after 7d. | 7d is well above any legitimate crawl duration we observe (longest in last 90d: 47 min). Threshold is settings-tunable. |
| Backfill migration corrects a sync_run that the user has already manually retried. | Migration uses `INSERT ... ON CONFLICT DO NOTHING` on the audit table and only updates sync_runs whose `error_details::jsonb @> '[{"error": "web_crawler_poll_timeout"}]'` AND no terminal sync_run exists for the same connector with later `started_at`. |

## Out-of-band cleanup

The 1 incident on Voys/Redcactus (`fdde0c1e-7a31-4810-9906-d3e032b3a815`,
sync_run from 2026-05-01) is the canonical case the backfill migration
addresses. After deploy, verify:

```sql
SELECT status, documents_ok, error_details
FROM connector.sync_runs
WHERE id IN (
  SELECT id FROM connector.sync_runs
  WHERE connector_id = 'fdde0c1e-7a31-4810-9906-d3e032b3a815'
);
```

Expected after migration: `status = completed`, `documents_ok = 368`,
`error_details = NULL`.

## Verification

End-to-end Playwright test against Voys/`support`:
1. Trigger a fresh `Sync now` on the Redcactus connector.
2. Assert `Bezig — N/M pagina's` appears within 60s.
3. Assert N is monotonically non-decreasing every poll.
4. Assert terminal `Voltooid (368 pagina's)` once
   knowledge-ingest finishes (≤ 60 min).
5. Assert no row in `connector.sync_runs` ever has
   `error = 'web_crawler_poll_timeout'` for runs started after deploy.

## Open questions

- Should the `RUNNING → COMPLETED` transition write a `product_event`?
  Currently the transition happens lazily on read-time resolution, so
  the natural emit point is the resolver, not a poll loop. Decision:
  **yes**, emit on first resolver-triggered transition; debounce by
  sync_run id to avoid duplicate events from concurrent reads.
- Does the reaper run as an in-process FastAPI task or as a separate
  procrastinate worker? Decision deferred to plan phase.
