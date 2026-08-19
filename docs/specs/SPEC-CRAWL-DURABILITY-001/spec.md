---
id: SPEC-CRAWL-DURABILITY-001
version: "0.1.0"
status: accepted
created: 2026-08-19
updated: 2026-08-19
author: Mark Vletter
priority: critical
related:
  - SPEC-PROCRASTINATE-ZOMBIE-001
  - SPEC-INGEST-RECONCILE-001
  - SPEC-CRAWLER-004
  - PR #1080
---

# Durable crawls across deploys

## 1. Problem and production evidence

`knowledge-ingest` serves HTTP and runs all Procrastinate worker lanes in one
Uvicorn process. A main-branch deployment replaces that single container.
Procrastinate currently stops taking new jobs and waits without a timeout for
running work, while Docker sends `SIGKILL` after the Compose grace period of 90
seconds. A crawl can take tens of minutes, so the process is killed before it
can persist a retry.

The Ascend crawl `983798da-7fac-45cb-985e-8c026a8b7f4f` was interrupted by the
2026-08-19 `knowledge-ingest` deployment and subsequently surfaced as
`crawl_worker_lost`. The crawl frontier, fetched results, and outcomes existed
only in process memory, so even a queue retry would have restarted discovery
from the seed URL.

Startup recovery has a second race: Klai runs a one-shot prune/retry pass with
a 120-second threshold before starting workers, while each Procrastinate worker
then prunes with its own 30-second default. A job can therefore become
ownerless only after the one-shot recovery has already completed. The status
GET currently turns that recoverable state into a permanent failure.

## 2. Contract

A crawl is a durable, at-least-once workflow identified by its existing stable
`crawl_jobs.id`.

1. A normal deployment shall stop queue intake, give running work a short
   bounded drain window, and persist/requeue any crawl that does not finish
   inside that window before Docker can kill the process.
2. A hard process loss shall be detected from the Procrastinate worker
   heartbeat and recovered automatically without a status request or operator
   action.
3. Fetch progress shall be checkpointed after every completed frontier batch.
   Recovery may repeat the interrupted batch, but shall not refetch an earlier
   committed batch.
4. A single logical crawl shall have at most one attempt authorised to persist
   progress. Every attempt uses a generation fence; stale attempts must stop
   before another network batch or state mutation.
5. Retryable ownership loss shall remain non-terminal to polling clients. A
   status read shall never decide recovery policy or write `crawl_worker_lost`.
6. Operator cancellation shall remain durable across restart and shall never
   be converted into a deploy retry.
7. Crawl-wide side effects shall be idempotent per logical crawl. In particular,
   the domain AIMD update shall be applied at most once, stale-artifact cleanup
   shall run only after a complete frontier, and terminal `completed` shall be
   written only after finalisation succeeds.

## 3. State and ownership model

The public statuses remain compatible: `pending`, `running`, `completed`,
`failed`, `failed_partial`, and `cancelled`. Recovery is represented internally
while the public status remains `running`.

`knowledge.crawl_jobs` gains:

- `execution_generation bigint NOT NULL DEFAULT 0` — monotonically increasing
  write fence, advanced atomically by PostgreSQL (`generation + 1`), never from
  a worker wall clock;
- `checkpoint_sequence bigint NOT NULL DEFAULT 0` — committed fetch batches;
- `checkpoint_updated_at timestamptz` — last durable forward progress;
- `runtime_checkpoint jsonb` — small versioned crawl-loop state;
- `recovery_count integer NOT NULL DEFAULT 0` — observable restart count;
- `rate_limit_effect_applied boolean NOT NULL DEFAULT false` — logical-crawl
  idempotency marker.

`knowledge.crawl_job_frontier` stores one row per canonical URL and job:

- tenant and ownership: `job_id`, `org_id`;
- deterministic frontier state: URL, depth, discovery source, priority, order,
  and queued/fetched/omitted state;
- the fetch outcome and normalised `CrawlResult`, both JSONB;
- crawl scope (`primary` or `discovery_seed`), with primary key
  `(job_id, crawl_scope, canonical_url)` and a composite `(job_id, org_id)`
  foreign key that binds a frontier row to the owning tenant;
- forced tenant RLS matching `knowledge.crawl_jobs`.

The checkpoint transaction is deliberately short: network work happens before
the transaction; the transaction only upserts frontier rows changed since the
previous committed batch and advances the parent job sequence if the execution
generation still matches. Each fetched page payload is stored once so a page
whose fetch committed but whose ingest did not can resume without another
request; prior batches are not serialised or rewritten on every checkpoint.

Database connections are leased per bounded database phase. No pool connection
is held across crawl4ai network fetches or between checkpoint batches. With the
configured pool of ten and eight crawl workers, long crawls therefore do not
reserve eight connections for their full runtime.

Adapter-side effects use a session-level PostgreSQL advisory lock derived from
the crawl ID. Both claim and guarded side effects use the non-blocking
`pg_try_advisory_lock`; contention raises a retryable `CrawlExecutionBusy`
instead of waiting while occupying a pool connection. This prevents a new
attempt from overtaking an old attempt halfway through page ingest, progress
persistence, or finalisation without wrapping network/Qdrant work in a long
database transaction. PostgreSQL also releases the lock automatically when a
hard-killed worker loses its connection.

## 4. Recovery rules

### Controlled shutdown

- Workers use `shutdown_graceful_timeout=20` seconds.
- Compose retains its 90-second grace period, leaving time for cancellation,
  Procrastinate retry persistence, pool closure, and container replacement.
- `run_crawl` retries shutdown cancellation, but functional crawl failures keep
  their existing terminal semantics.

### Hard loss

- Worker heartbeat update, worker pruning, and recovery all use the same
  120-second stalled threshold.
- A periodic recovery task uses Procrastinate's heartbeat-aware
  `get_stalled_jobs` and a queueing lock on a dedicated maintenance lane.
- Every new crawl attempt atomically advances `execution_generation` in the
  database before network work; a retry from `running` also advances
  `recovery_count`.
- A retry restores the last frontier checkpoint under the new generation.
- Recovery failure is loud and observable; it is not silently converted into
  success.

### Polling

The status endpoint is read-only with respect to recovery. `doing` with a null
worker remains recoverable. Queue-terminal state may only become a crawl
failure after the recovery controller has explicitly exhausted its policy.

## 5. Checkpoint compatibility and retention

`runtime_checkpoint.version` starts at `1`. Code shall reject an unknown future
version loudly. A missing checkpoint is the backward-compatible state for jobs
created before this migration and starts from the seed URL.

Checkpoint rows are retained with the crawl job for audit while it is active.
Terminal jobs may be cleaned by a later retention task; retention is not part
of this SPEC and no eager deletion is required for correctness.

## 6. Acceptance criteria

### AC-1 — graceful deployment resumes

Given a multi-batch crawl is running, when the worker receives shutdown, then
the active attempt stops within the configured worker timeout, the queue job is
retryable before the Compose deadline, and the same crawl ID resumes.

### AC-2 — hard loss resumes

Given a worker disappears without unregistering, when its heartbeat becomes
stalled, then exactly one recovery pass retries the crawl without a status GET.

### AC-3 — committed batches are not refetched

Given two frontier batches are committed and the third attempt is interrupted,
when the crawl resumes, then URLs from batches one and two are restored from
Postgres and are not sent to crawl4ai again.

### AC-4 — interrupted batch is safe to repeat

Given a process dies after a network response but before its checkpoint commits,
when the crawl resumes, then only that uncommitted batch may be requested again.

### AC-5 — stale attempt is fenced

Given recovery advances the execution generation, when an old attempt tries to
checkpoint, start a new batch, ingest a page, update progress, or finalise, then
it is rejected and performs no further state mutation. Recovery cannot advance
the generation halfway through a guarded side-effect block.

### AC-6 — polling does not destroy recovery

Given the Procrastinate row is `doing` with `worker_id=NULL`, when status is
polled, then the crawl remains non-terminal and no `crawl_worker_lost` write is
performed.

### AC-7 — cancellation survives restart

Given `cancel_requested=true`, when a recovered attempt starts, then it performs
no seed fetch and terminates as `cancelled`.

### AC-8 — crawl-wide effects execute once

Given an attempt dies after applying the domain-rate observation, when the crawl
resumes and completes, then that observation is not applied a second time.

### AC-9 — repeated deploys cannot starve progress

Given a checkpoint is committed between interruptions, when five deployments
interrupt one crawl, then the same crawl eventually completes and its checkpoint
sequence increases monotonically.

### AC-10 — migration integrity

Alembic has exactly one head; the frontier foreign key is indexed; JSONB and
state constraints reject malformed rows; RLS is enabled and forced.

## 7. Verification

Required gates:

```bash
cd klai-knowledge-ingest
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/ -q
uv run alembic heads
```

Fault-injection tests shall cover controlled cancellation, hard-loss recovery,
checkpoint restoration, generation fencing, polling during ownerless recovery,
durable cancellation, and at-most-once AIMD application.

The checkpoint restoration and generation/lock contracts also run against a
fresh PostgreSQL service in CI. Unit fakes remain useful for branch coverage,
but are not accepted as the only proof for AC-3, AC-4, AC-5, or AC-9.

Production proof after merge is a controlled Ascend crawl interrupted by a
`knowledge-ingest` deployment. Intermedia is not the first durability canary,
because its Cloudflare behaviour is a separate variable.

## 8. Deliberately not included

- No Temporal, Redis, or new queue product: Postgres and Procrastinate already
  provide durable queue state and heartbeats.
- No separate worker container yet: separation reduces unnecessary
  interruptions but does not replace checkpointing or fencing.
- No horizontal crawl-worker scaling: the host pacing gate is process-local.
  Scaling requires a distributed per-host pacing/lease contract first.
