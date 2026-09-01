---
id: SPEC-CONNECTOR-CANCEL-001
version: "1.0"
status: draft
created: 2026-05-08
updated: 2026-05-08
author: Mark Vletter
priority: high
issue_number: 0
---

## HISTORY

| Version | Date | Author | Change |
|---------|------|--------|--------|
| 1.0 | 2026-05-08 | Mark Vletter | Initial draft. Generalise the connector-delete race fixes into one resource-key + cancellation + fencing contract for connector-scoped async jobs. |

---

# SPEC-CONNECTOR-CANCEL-001: Resource-key cancellation and fencing for connector-scoped async jobs

## Context

Klai's connector delete/rebuild path is no longer a simple row delete. One connector sync can enqueue work across multiple lanes:

- `crawl-jobs` on the I/O lane.
- `enrich-bulk` on the LLM lane.
- `graphiti-bulk` on the LLM lane.
- `connector-purge` on the I/O lane.

The correctness risk is not queue throughput. The risk is ownership drift: work that was valid when enqueued can become invalid before it writes.

The current code already contains targeted guards from earlier specs:

- `connector_cleanup.purge_connector` snapshots artifact ids, attempts to cancel selected enrichment/graphiti jobs, deletes artifacts, Graphiti episodes, Qdrant chunks and Garage keys. **However, `_cancel_enrichment_jobs` is currently dead code**: it filters on `args->'extra_payload'->>'source_connector_id'`, but since SPEC-INGEST-CONTENT-PG-001 enrichment jobs are deferred with only `artifact_id` — the WHERE clause never matches a live row, so enrichment-job cancellation on connector delete silently returns 0. This strengthens the case for this SPEC: the "targeted guard" for enrichment is already broken by an unrelated refactor, exactly the drift class this contract prevents.
- `enrichment_tasks._load_and_enrich` re-reads the artifact by `artifact_id` instead of carrying stale document payload in Procrastinate args.
- `ingest_graphiti_episode` checks artifact existence before writing to Graphiti.
- `_enrich_document` checks `connector_is_active(source_connector_id)` before doing LLM and Qdrant work.
- `crawl_sync_cancel` can cancel one `run_crawl` job by `knowledge.crawl_jobs.id`.

Those are good repairs, but they are still ad hoc. Some jobs can be cancelled by connector id, some by artifact id, some by crawl job id, and monitoring queries have to reverse-engineer ownership from task-specific JSON paths. A future connector-scoped task can accidentally skip the guard and reintroduce the same regrow bug.

## Problem Statement

Deleting or rebuilding connector `C` must establish a fence: no async job that belongs to the old generation of `C` may write new artifacts, Qdrant points, Graphiti episodes or image references after the fence.

Today the fence is implicit and spread across task-specific logic:

- Enrichment jobs now take only `artifact_id`, so queued job args no longer include `source_connector_id`.
- Graphiti jobs include `artifact_id`, but not `source_connector_id`.
- Crawl jobs include `connector_id`, but are cancelled through a crawl-job route by `job_id`.
- Procrastinate's native `queueing_lock` prevents duplicates, but it is not an ownership index and is not a sufficient cancellation filter.
- `connector_cleanup` has to discover artifact ids before delete and then use separate JSON filters for enrichment and Graphiti.

That makes delete/rebuild correctness depend on every future task remembering the same local convention.

## Design Summary

Introduce a small connector-scoped job contract:

1. Every connector-scoped Procrastinate job carries a canonical `resource_key`.
2. The resource key identifies the mutable resource and generation the job is allowed to write to.
3. Deleting/rebuilding a connector cancels live jobs by `resource_key` before data cleanup.
4. Every write-side task checks a fence immediately before expensive work and again immediately before irreversible writes.
5. Cleanup remains idempotent and is allowed to run more than once.
6. Monitoring counts jobs by resource key and status using the same semantics as the cancellation code.

The first resource family is connector-scoped:

```text
connector:{org_id}:{kb_slug}:{connector_id}:{generation}
```

`generation` is a monotonically increasing value tied to the portal connector row. It can be `sync_epoch`, `delete_epoch`, `updated_at` converted to an integer, or a new explicit column. The exact storage choice is implementation detail; the contract is that old jobs can be fenced out when the connector is deleted or rebuilt.

For jobs that are artifact-scoped but produced by a connector, the payload still carries the connector resource key:

```json
{
  "artifact_id": "...",
  "resource_key": "connector:org:kb:connector:generation"
}
```

The artifact id remains the work item. The resource key is the authority/fence.

## Requirements

### REQ-01: Canonical resource_key on connector-scoped jobs

**Ubiquitous.** Every new Procrastinate job that can write connector-owned knowledge data shall carry `resource_key` as a top-level task argument or in a shared metadata envelope.

Applies to:

- `run_crawl` when `connector_id` is present.
- `enrich_document_bulk` for artifacts whose `extra.source_connector_id` is present.
- `ingest_graphiti_episode` for artifacts whose `extra.source_connector_id` is present.
- future connector-scoped backfills that write Qdrant, Graphiti, `knowledge.artifacts`, `knowledge.crawled_pages`, `knowledge.page_links`, `knowledge.parent_chunks` or Garage image refs.

Acceptance:

- Procrastinate args for connector-owned jobs include `resource_key`.
- Manual uploads and Gitea jobs without `source_connector_id` may omit `resource_key`.
- Tests assert that connector sync enqueues crawl/enrich/graphiti work with the same resource key.

### REQ-02: resource_key is the cancellation index

**Event-driven.** When connector delete or rebuild starts, the system shall cancel all live Procrastinate jobs for the connector's current resource key before deleting data stores.

Live means `status IN ('todo', 'doing')`. Note on Procrastinate 3.x semantics: the `aborting` status is legacy and unused in 3.x; abort is a request flag on a `doing` job (`abort_requested`), and a job that cooperates with abort ends in terminal status `aborted`. `todo` jobs that are cancelled end in terminal status `cancelled`.

Cancellation rules:

- Use Procrastinate's job manager (`cancel_job_by_id_async`) for each discovered job.
- `abort=True` for `doing` jobs (sets `abort_requested`; delivery relies on the task cooperating or on the fence check — REQ-03 is the guarantee, abort is best-effort).
- `delete_job=False` by default so monitoring and audit can see `cancelled`/`aborted`.
- Scope to queues that can write connector-owned knowledge data: `crawl-jobs`, `enrich-bulk`, `graphiti-bulk`, and any future connector-scoped writer queue.
- Do not cancel `connector-purge` itself.

Acceptance:

- Delete/rebuild logs `connector_resource_jobs_cancel_requested` with `resource_key`, `jobs_found`, `jobs_cancelled`, `jobs_failed_to_cancel`.
- Jobs for another connector id or another generation are untouched.
- A missing or already terminal job is a no-op.

### REQ-03: Fencing table/check prevents stale writes

**State-driven.** Before a connector-owned task performs expensive work or writes, it shall verify that its `resource_key` is still current and active.

The fence check returns one of:

- `active`: task may continue.
- `stale_generation`: connector exists, but this job belongs to an old generation.
- `deleting`: connector exists but is being deleted.
- `deleted`: connector no longer exists.
- `unknown_error`: lookup failed; write-side tasks fail closed.

Acceptance:

- `run_crawl` checks before each per-page ingest/write loop.
- `enrich_document_bulk` checks after loading the artifact and before LLM/embedding calls.
- `_enrich_document` checks again immediately before Qdrant upsert.
- `ingest_graphiti_episode` checks before Graphiti ingest and before persisting `graphiti_episode_id`.
- `unknown_error` aborts the write path and logs at warning or error level.

### REQ-04: Artifact existence remains a secondary guard

**Ubiquitous.** Artifact existence checks remain in place for artifact-scoped jobs, but they are not the primary connector fence.

Why: during rebuild, an artifact can still exist while its connector generation is stale. Artifact existence alone cannot distinguish "old connector generation" from "current connector generation".

Acceptance:

- Enrichment and Graphiti skip if the artifact row is gone.
- If the artifact exists but `resource_key` is stale/deleting/deleted, the job skips before LLM, embedding, Qdrant or Graphiti writes.
- Skip logs include both `artifact_id` and `resource_key` when available.

### REQ-05: Idempotent cleanup and rebuild semantics

**Ubiquitous.** Connector purge and rebuild cleanup shall be safe to run repeatedly.

Delete/rebuild order:

1. Mark connector generation fenced (`deleting`, `rebuilding`, or generation increment).
2. Cancel live jobs by old resource key.
3. Snapshot artifact ids and episode ids still associated with the old generation.
4. Delete Postgres artifacts/crawl rows, Graphiti episodes, Qdrant chunks and Garage refs.
5. Run janitor sweeps for late Graphiti/Garage orphans.
6. For rebuild, enqueue new work with a new resource key.

Acceptance:

- Running cleanup twice returns zero or lower counts on the second run and does not raise.
- A rebuild never reuses the old resource key.
- Old jobs that survive cancellation skip because their resource key is stale.

### REQ-06: Monitoring query uses one status vocabulary

**Ubiquitous.** Add one helper/query that reports connector-scoped job counts by `resource_key` and Procrastinate status.

It must count at least (Procrastinate 3.x status vocabulary):

- `todo`
- `doing`
- `cancelled`
- `aborted`
- `failed`
- `succeeded`

And expose derived buckets:

- `pending = todo`
- `running = doing` (including abort-requested jobs, which stay `doing` until they cooperate)
- `terminal = cancelled + aborted + failed + succeeded`
- `failed_visible = failed` only; `cancelled` and `aborted` are not failures for delete/rebuild.

Acceptance:

- The query filters by top-level `args->>'resource_key' = $1`.
- It does not use `args::text LIKE`.
- Existing taxonomy/backfill status mapping is not silently reused for connector purge monitoring because that mapping treats `cancelled` as `failed`.
- Tests seed all statuses and assert exact counts.

### REQ-07: Queue lane interaction is explicit

**Ubiquitous.** The implementation shall keep queue lane semantics intact.

Rules:

- Cancellation helpers know the writer queues explicitly or import them from `queues.py`.
- `connector-purge` remains in `IO_QUEUES`.
- `crawl-jobs` remains in `IO_QUEUES`.
- `enrich-bulk` and `graphiti-bulk` remain in `LLM_QUEUES`.
- Adding a new connector-scoped writer queue requires updating the resource-key cancellation queue list and `tests/test_queues_constants.py` or a dedicated resource-job queue test.

Acceptance:

- Tests fail if a connector-scoped writer queue is added without being included in cancellation monitoring.
- No worker lane is merged back into `ALL_QUEUES` single-worker mode.

### REQ-08: Tests cover cancellation, fencing and status reporting

**Ubiquitous.** The implementation shall include focused tests for the contract and one regression scenario that reproduces the old regrow shape.

Minimum test coverage:

- Resource key construction.
- Enqueue payloads for crawl/enrich/graphiti.
- Cancellation filters exact `resource_key`.
- Fencing skips stale generation after artifact still exists.
- Fencing skips deleted connector after artifact missing.
- Pre-write guard prevents Qdrant upsert.
- Pre-write guard prevents Graphiti ingest.
- Monitoring counts `todo`, `doing`, `cancelled`, `aborted`, `failed`, `succeeded` correctly.
- Full delete/rebuild regression: old jobs do not write after the new connector generation starts.

## Files

Likely touched implementation files:

- `klai-knowledge-ingest/knowledge_ingest/queues.py`
- `klai-knowledge-ingest/knowledge_ingest/worker.py`
- `klai-knowledge-ingest/knowledge_ingest/enrichment_tasks.py`
- `klai-knowledge-ingest/knowledge_ingest/connector_cleanup.py`
- `klai-knowledge-ingest/knowledge_ingest/connector_purge_tasks.py`
- `klai-knowledge-ingest/knowledge_ingest/crawl_tasks.py`
- `klai-knowledge-ingest/knowledge_ingest/routes/ingest.py`
- `klai-knowledge-ingest/knowledge_ingest/routes/crawl_sync.py`
- `klai-knowledge-ingest/knowledge_ingest/connector_state.py`
- `klai-knowledge-ingest/knowledge_ingest/pg_store.py`

Likely new files:

- `klai-knowledge-ingest/knowledge_ingest/resource_jobs.py`
- `klai-knowledge-ingest/tests/test_resource_jobs.py`
- `klai-knowledge-ingest/tests/test_connector_resource_fencing.py`
- `klai-knowledge-ingest/tests/test_connector_resource_monitoring.py`

Portal/connector files may be touched only if the chosen generation storage lives in `portal_connectors` or klai-connector's rebuild orchestration.

## Non-goals

- A generic workflow engine for all Procrastinate jobs.
- Retrofitting unrelated taxonomy, rag-eval or clustering jobs unless they become connector-scoped writers.
- Backfilling historical orphan Qdrant/Graphiti data from old production incidents.
- Changing LLM lane concurrency or queue prioritisation.
- Hiding cancelled jobs from Procrastinate tables; cancellation is observable state.

## Constraints

- No `args::text LIKE` for the new cancellation path.
- No reliance on `queueing_lock` as the ownership key.
- Fail closed on connector-state lookup errors before writes.
- Fence lookups must not add a DB roundtrip per crawled page. A short-lived in-process cache (max ~5s) or per-batch fence check is acceptable for the "before expensive work" checkpoint; the "immediately before irreversible write" checkpoint must be fresh (uncached) or accept the ≤5s window as documented residual risk.
- v1 does not add a portal-api schema migration for `generation`. The generation value is caller-supplied at sync start (e.g. sync-run id or epoch provided by the orchestrator that triggers the sync). An explicit portal column can be a follow-up if the caller-supplied value proves fragile.
- Keep cleanup idempotent; retries must be safe.
- Keep existing artifact existence checks as defence-in-depth.
- Do not delete Procrastinate job rows unless there is a specific retention reason.

## References

- `.claude/rules/klai/projects/knowledge.md` — connector-delete cleanup, in-flight jobs, worker lanes.
- `.moai/specs/SPEC-CONNECTOR-DELETE-RACE-001/` — original observed race and targeted mitigation.
- `.moai/specs/SPEC-CONNECTOR-CLEANUP-001/` — connector cleanup architecture and cross-store cleanup context.
- `klai-knowledge-ingest/knowledge_ingest/queues.py` — `IO_QUEUES` / `LLM_QUEUES`.
- `klai-knowledge-ingest/knowledge_ingest/worker.py` — lane workers.
- `klai-knowledge-ingest/knowledge_ingest/enrichment_tasks.py` — enrichment and Graphiti tasks.
- `klai-knowledge-ingest/knowledge_ingest/connector_cleanup.py` — current purge orchestration.
- `klai-knowledge-ingest/knowledge_ingest/routes/crawl_sync.py` — single crawl-job cancellation shape.
