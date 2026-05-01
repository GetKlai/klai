---
id: SPEC-PROCRASTINATE-ZOMBIE-001
version: "1.0"
status: completed
created: 2026-05-01
updated: 2026-05-01
synced: 2026-05-01
author: Mark Vletter
priority: high
issue_number: 0
related: SPEC-WORKER-LANES-001
---

> **COMPLETED 2026-05-01.** Implementation in commit `e4e9294a` (PR #260),
> merged to main + deployed at 09:46 UTC. The recovery mechanism is unchanged
> by the later SPEC-WORKER-LANES-001 refactor — `recover_zombie_jobs` still
> runs ONCE at startup, but now before BOTH lane workers start.
> Future-proofed for any future lane additions by being lane-agnostic.

## HISTORY

| Version | Date | Author | Change |
|---------|------|--------|--------|
| 1.0 | 2026-05-01 | Mark Vletter | Initial draft. 21 zombie procrastinate jobs found in production accumulated over 25 days (5–30 April), saturating worker concurrency on `enrich-bulk` + `graphiti-bulk` queues. Discovered while validating the connector-delete e2e cycle on Voys (SPEC-CONNECTOR-DELETE-LIFECYCLE-001). |

---

# SPEC-PROCRASTINATE-ZOMBIE-001: Recover orphaned procrastinate jobs after worker crash

## Context

`klai-knowledge-ingest` runs the procrastinate worker as a background asyncio task inside the FastAPI lifespan. Each deploy recreates the container; Docker sends SIGTERM and waits 10 seconds (the daemon default) before SIGKILL.

Two enrichment task types regularly exceed that 10-second window:

- `enrich_document_bulk` — embeddings + LLM rerank (5–30s)
- `ingest_graphiti_episode` — Mistral entity + relation extraction via LiteLLM (30–60s)

When SIGKILL fires mid-task:

1. The worker process dies with no chance to write a final job status to PostgreSQL.
2. The next worker startup calls procrastinate's built-in `prune_stalled_workers_v1` (heartbeat older than `stalled_worker_timeout`, default 30 s). That **deletes the worker row** but the FK `procrastinate_jobs.worker_id → procrastinate_workers.id ON DELETE SET NULL` only nulls out `worker_id`. The job's `status` stays `doing` forever.
3. Procrastinate v3.7.2 has `get_stalled_jobs(seconds_since_heartbeat=…)` as a read API but ships **no built-in retry path** for the rows it returns — every consumer is expected to wire that itself.

Each surviving zombie permanently consumes a worker concurrency slot. After enough deploys, enrichment throughput collapses.

### Production evidence (2026-04-30, before fix)

```sql
SELECT count(*) FROM procrastinate_jobs WHERE status = 'doing';
-- 21 (across 5 April .. 30 April, all worker_id IS NULL)
```

Distribution:

- `graphiti-bulk` × 19 (Mistral LLM calls killed mid-extraction)
- `enrich-bulk` × 2

All 21 align with previous deploy timestamps. The `connector-purge` queue did still pick up new work because each queue has its own slot, but the heavy enrichment lanes were partially blocked.

---

## Scope

In scope:

- `klai-knowledge-ingest` worker lifespan in `klai-knowledge-ingest/knowledge_ingest/app.py`
- A new module `knowledge_ingest/zombie_recovery.py`
- `deploy/docker-compose.yml` — add `stop_grace_period: 90s` on the `knowledge-ingest` service

Out of scope (separate follow-ups):

- Other services that may use procrastinate in the future. Today only `knowledge-ingest` does (verified 2026-05-01 grep across the monorepo).
- Procrastinate upstream contribution to make the recovery automatic.
- Alembic migration changes — none needed.

---

## Requirements (EARS)

### REQ-1 (Recovery on worker startup)

**WHEN** the `knowledge-ingest` lifespan starts and `enrichment_enabled` is true,
**THE SYSTEM SHALL** call `recover_zombie_jobs(proc_app)` BEFORE starting the procrastinate worker (`proc_app.run_worker_async`).

### REQ-2 (Stalled worker pruning)

`recover_zombie_jobs` SHALL call `proc_app.job_manager.prune_stalled_workers(STALLED_WORKER_TIMEOUT_SECONDS)` with `STALLED_WORKER_TIMEOUT_SECONDS = 120` to remove dead worker rows.

The 120-second window is conservative relative to procrastinate's 10-second heartbeat interval — it will not prune the live worker that is about to start.

### REQ-3 (Retry orphan jobs)

After pruning workers, `recover_zombie_jobs` SHALL select every row matching `status = 'doing' AND worker_id IS NULL` and call `proc_app.job_manager.retry_job_by_id_async(job_id, retry_at=now())` for each.

The retry API resets `status = 'todo'`, increments `attempts`, and clears the worker reference. Every queue task in this service is idempotent (Episode UUID dedup, content_hash dedup, connector_purge_task is idempotent by construction), so re-execution is safe.

### REQ-4 (Best-effort)

Recovery SHALL be best-effort. If `prune_stalled_workers`, the SELECT, or any individual `retry_job_by_id_async` raises, the lifespan SHALL log the exception and continue — the worker still starts and processes new jobs.

### REQ-5 (Observability)

Recovery SHALL emit one structured log event per outcome:

- `procrastinate_pruned_stalled_workers` with `count` (only if any were pruned)
- `procrastinate_zombie_recovery_clean` if no orphan jobs found
- `procrastinate_zombies_retried` with `workers_pruned`, `jobs_retried`, `jobs_total` if any retries happened
- `procrastinate_zombie_retry_failed` per failing retry (with traceback)
- `procrastinate_zombie_recovery_failed` if the wrapping try/except triggers

### REQ-6 (Reduce zombie creation rate)

`deploy/docker-compose.yml` SHALL set `stop_grace_period: 90s` on the `knowledge-ingest` service.

90 s exceeds the 99th-percentile observed graphiti task duration (~60 s) so most LLM calls finish before SIGKILL. The recovery loop in REQ-1..REQ-5 covers the residual cases.

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Recovery races with a still-alive worker (false positive) | `STALLED_WORKER_TIMEOUT_SECONDS = 120` is 12× the 10s heartbeat — a live worker cannot be 120 s stale. |
| Idempotency assumption wrong for some task | Audited 2026-05-01: every registered task in `enrichment_tasks.py` and `connector_purge_tasks.py` is idempotent. New tasks must remain idempotent (existing project convention). |
| Recovery itself fails and blocks startup | REQ-4 guarantees the lifespan continues even on recovery failure. |
| `stop_grace_period: 90s` slows down deploys noticeably | True, but only when an enrichment task is actively running at deploy time. In practice deploys finish in 15-30 s; the longer window only kicks in for the rare overlap. |
| New procrastinate-using service skips this hook | Documented in `.claude/rules/klai/projects/knowledge.md` (added in this SPEC). |

---

## Success Criteria

- After deploy: `SELECT count(*) FROM procrastinate_jobs WHERE status = 'doing' AND worker_id IS NULL` reaches 0 within 5 seconds of new container startup.
- One-shot cleanup of the existing 21 zombies happens automatically on first deploy of this PR (no manual SQL needed).
- Container shutdown logs show `procrastinate_zombies_retried` with the correct counts when zombies were present.
- Subsequent deploys do not introduce new zombies as long as graphiti tasks finish within 90 s — verified by re-querying `count(*) WHERE status='doing' AND worker_id IS NULL` 5 minutes after each deploy for 1 week.
- Pre-flight check in PR body: `grep -c stop_grace_period deploy/docker-compose.yml` ≥ 1.

---

## Out of scope (explicit non-goals)

- Replacing procrastinate.
- Custom heartbeat protocol.
- Retroactive cleanup of `succeeded` / `failed` jobs (covered by `delete_old_jobs` already).
- Changing task signatures or semantics. Only the worker lifecycle + recovery is touched.
