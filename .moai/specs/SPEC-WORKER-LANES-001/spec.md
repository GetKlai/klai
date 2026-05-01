---
id: SPEC-WORKER-LANES-001
version: "1.0"
status: draft
created: 2026-05-01
updated: 2026-05-01
author: Mark Vletter
priority: high
issue_number: 0
supersedes: SPEC-INGEST-QUEUE-SEPARATION-001 (in spirit; constants module + crawl-jobs queue retained)
---

## HISTORY

| Version | Date | Author | Change |
|---------|------|--------|--------|
| 1.0 | 2026-05-01 | Mark Vletter | Consolidates a week of partial fixes into one architectural fix. SPEC-INGEST-QUEUE-SEPARATION-001 split queues but missed concurrency. The concurrency=4 patch raised parallelism but missed per-queue fairness. Both attempts were symptoms of mixing two unrelated workloads on one worker. This SPEC fixes the root cause by giving I/O and LLM work their own worker processes within the same container. |

---

# SPEC-WORKER-LANES-001: Separate I/O and LLM worker lanes

## Context

`klai-knowledge-ingest` runs a single procrastinate worker subscribed to all
seven queues:

```
[ingest-kb, enrich-interactive, enrich-bulk, graphiti-bulk,
 taxonomy-backfill, connector-purge, crawl-jobs]
```

These queues back two fundamentally different workloads:

| Workload | Queues | Per-task latency | Sensitivity |
|---|---|---|---|
| **I/O** | `ingest-kb`, `connector-purge`, `crawl-jobs` | sub-second to ~30s | latency-sensitive (user-triggered) |
| **LLM** | `enrich-interactive`, `enrich-bulk`, `graphiti-bulk`, `taxonomy-backfill` | 5-60s per call, rate-limited | throughput-sensitive |

Mixing them in one worker creates two failure modes that are both
silent and user-visible:

1. **No per-queue fairness inside a single worker.** Procrastinate fetches
   the oldest `todo` across the worker's full queue set when a slot frees.
   A backlog of slow LLM jobs delays every I/O job until the LLM lane
   drains. Verified during 2026-05-01 Voys e2e: a 50-LLM-job backlog
   pushed user-triggered crawls 10+ minutes behind schedule even at
   `concurrency=4`.

2. **One concurrency value is wrong for both lanes.** I/O is HTTP-bound and
   benefits from parallelism (concurrency 8+ is fine). LLM is bounded by
   the upstream Mistral rate limit (token bucket in `graph.py` enforces
   ~1 req/s for graphiti). One-size-fits-all means either I/O is
   under-served or LLM violates its rate budget.

Plus a downstream consequence on the klai-connector side:

3. **`sync_engine` poll timeout did not cancel the remote procrastinate
   task.** When klai-connector hit its 30-min poll timeout, it marked the
   `sync_run` as FAILED, but the still-running `run_crawl` task on
   knowledge-ingest kept writing artifacts. Result: data state
   (knowledge.artifacts grew) diverged from sync_run state (FAILED),
   confusing both users and observability dashboards.

This SPEC fixes all three at once.

## Why a single SPEC

A week of partial fixes (SPEC-CONNECTOR-DELETE-LIFECYCLE-001 PR #253,
SPEC-PROCRASTINATE-ZOMBIE-001, SPEC-INGEST-QUEUE-SEPARATION-001, the
concurrency=4 patch) all chased symptoms of the same architectural error:
running two workloads in one queue lane. Each fix removed one symptom and
revealed the next. The lesson is that worker lanes are an architectural
concept that has to be designed, not a series of knobs to tune.

---

## Scope

In scope:

- `klai-knowledge-ingest/knowledge_ingest/queues.py` — split into
  `IO_QUEUES` + `LLM_QUEUES` lanes; `ALL_QUEUES = IO_QUEUES + LLM_QUEUES`.
- `klai-knowledge-ingest/knowledge_ingest/worker.py` — `WorkerLifecycle`
  starts two procrastinate workers, one per lane, each with its own
  concurrency and queue subscription.
- `klai-knowledge-ingest/knowledge_ingest/routes/crawl_sync.py` — new
  `POST /ingest/v1/crawl/sync/{job_id}/cancel` endpoint.
- `klai-connector/app/clients/knowledge_ingest.py` —
  `crawl_sync_cancel(job_id)` HTTP method.
- `klai-connector/app/services/sync_engine.py` — call
  `crawl_sync_cancel` on poll timeout before marking FAILED.
- Tests: lane partition invariants, two-worker startup, concurrency
  values, zombie recovery still runs.

Out of scope:

- Replacing procrastinate. Two-worker pattern is the cheapest fix.
- Per-queue priority within a lane. Procrastinate's FIFO inside a lane is
  acceptable because tasks within a lane have similar latency profiles.
- Increasing the 30-min poll timeout in sync_engine. The cancel-on-timeout
  fix removes the data-state divergence; raising the timeout is a separate
  decision tied to user expectations.
- Migrating `connector-purge` or `ingest-kb` semantics — they only move
  lanes, the task code is unchanged.

---

## Requirements (EARS)

### REQ-1 (Lane partition)

`knowledge_ingest.queues` SHALL declare `IO_QUEUES` and `LLM_QUEUES` such
that:

- Every queue constant in the module belongs to exactly one lane.
- `IO_QUEUES = [INGEST_KB, CONNECTOR_PURGE, CRAWL_JOBS]`.
- `LLM_QUEUES = [ENRICH_INTERACTIVE, ENRICH_BULK, GRAPHITI_BULK, TAXONOMY_BACKFILL]`.
- `ALL_QUEUES = IO_QUEUES + LLM_QUEUES` (deterministic order).

The partition is enforced by `tests/test_queues_constants.py`.

### REQ-2 (Two workers, one per lane)

`WorkerLifecycle.__aenter__` SHALL start two procrastinate workers via
`run_worker_async`:

- I/O worker: `queues=IO_QUEUES, concurrency=8`.
- LLM worker: `queues=LLM_QUEUES, concurrency=4`.

Both share the same `proc_app` and connector pool. Both register
independent `procrastinate_workers` rows with their own heartbeats and
concurrency semaphores. A single worker subscribed to `ALL_QUEUES` is
explicitly disallowed by the lifecycle implementation and by tests.

### REQ-3 (Cancel-on-timeout for sync_engine)

When `sync_engine._run_web_crawler_delegation` hits its poll timeout, it
SHALL:

1. Call `crawl_sync_cancel(remote_job_id)` on knowledge-ingest.
2. Log `web_crawler_remote_cancel_sent` with `connector_id` + `remote_job_id`.
3. Continue marking `sync_run.status = FAILED` regardless of the cancel
   result (cancel is best-effort).

`POST /ingest/v1/crawl/sync/{job_id}/cancel` on knowledge-ingest SHALL:

- Look up the matching procrastinate `run_crawl` task for the given
  `crawl_jobs.id`.
- Call `proc_app.job_manager.cancel_job_by_id_async(proc_id, abort=True, delete_job=False)`.
- Return `204 No Content` whether the task was running, already finished,
  or never existed (idempotent — caller's intent is "stop new work" and
  that intent is satisfied as long as the task is no longer active).
- Return `404 Not Found` only when the `crawl_jobs.id` itself is unknown.

### REQ-4 (Graceful two-worker shutdown)

`WorkerLifecycle.__aexit__` SHALL cancel both workers in parallel
(`asyncio.gather` with `return_exceptions=True`) and continue with
shutdown even if one of them raises during cancellation. Logging
distinguishes expected `CancelledError` from unexpected failures.

### REQ-5 (Zombie recovery still runs)

The existing zombie recovery (SPEC-PROCRASTINATE-ZOMBIE-001) SHALL run
once at startup BEFORE either worker starts. It is lane-agnostic: it
retries every orphan `doing` job regardless of which lane it belonged
to.

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Two workers contending on the same connector pool | Procrastinate's `PsycopgConnector` is async + share-safe across workers within one App. Verified by procrastinate's own multi-worker docs. |
| Crash in one lane brings down the other | `asyncio.gather(return_exceptions=True)` in shutdown isolates failures. The two `asyncio.Task`s are independent; cancellation of one does not cascade. |
| LLM concurrency 4 too high → rate-limit violations | The token-bucket in `knowledge_ingest.graph._TokenBucketLimiter` enforces 1 req/s upstream regardless of worker concurrency. 4 in-flight jobs is safe because the limiter blocks them inside the LLM call, not at the worker level. |
| Cancel endpoint fires AFTER the task already finished | Idempotent by design: `cancel_job_by_id_async` on a finished job is a no-op or 404 from procrastinate, both swallowed. The 204 contract holds. |
| Forgotten lane assignment for a new queue | `tests/test_queues_constants.py::test_io_and_llm_lanes_partition_all_queues` fails when a queue lands in neither lane. CI catches it. |

---

## Success Criteria

After deploy:

1. `ssh core-01 "docker logs klai-core-knowledge-ingest-1 --since 1m | grep procrastinate_workers_started"`
   shows `io_concurrency=8, io_queues=[ingest-kb, connector-purge, crawl-jobs], llm_concurrency=4, llm_queues=[...]`.

2. `SELECT id FROM procrastinate_workers ORDER BY id DESC LIMIT 4` shows
   two workers per knowledge-ingest restart (one per lane).

3. Trigger a connector sync that produces a 50-job LLM backlog, then
   trigger a crawl-job: the crawl runs within seconds (not minutes),
   verified by `procrastinate_jobs.status='doing' WHERE queue_name='crawl-jobs'`
   appearing within 10 seconds of enqueue regardless of LLM backlog.

4. Manually verify cancel-on-timeout: trigger a slow crawl, force the
   30-min poll timeout in sync_engine. Verify
   `procrastinate_jobs.abort_requested = true` for the matching task and
   the task transitions to `cancelled` status.

5. 30 unit tests passing (existing 25 + 5 new lane invariant tests).
   `tests/test_queues_constants.py` partition test fails on any drift.

---

## Out of scope (explicit non-goals)

- Replacing the procrastinate task framework.
- Adding per-queue priority levels (procrastinate has `priority:` per job
  but we don't need it inside a single lane).
- Webhook-based completion notification instead of polling. Polling is
  fine; we just need to bound staleness via cancel-on-timeout.
- Multi-process worker instead of multi-task. Asyncio task isolation is
  sufficient for our workload.

---

## Lessons (for the changelog)

1. Queue separation without concurrency is a symptom-fix.
2. Concurrency without per-lane scheduling is also a symptom-fix.
3. The right unit of design is the **worker lane**, not the queue. A lane
   is `(queues, concurrency, worker_process)` together.
4. Architecture should be informed by workload latency profile, not queue
   names. Two queues with the same SLA can share a worker; two queues
   with wildly different SLAs cannot, no matter how nicely they are named.
5. Data-state divergence (artifact writes after sync_run failure) is a
   separate problem class from queue scheduling. Solving one doesn't
   solve the other; both must be in scope.
