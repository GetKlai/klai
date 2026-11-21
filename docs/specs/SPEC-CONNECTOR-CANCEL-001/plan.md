---
id: SPEC-CONNECTOR-CANCEL-001
version: "1.0"
status: draft
---

# Implementation Plan

Do this as small, revertable phases. The goal is to replace task-specific ownership inference with one connector resource contract.

Branch: `fix/SPEC-CONNECTOR-CANCEL-001`

---

## Fase 1 — Resource key model

**Goal:** one place builds and parses connector resource keys.

**Files:**

- `klai-knowledge-ingest/knowledge_ingest/resource_jobs.py` (new)
- `klai-knowledge-ingest/tests/test_resource_jobs.py` (new)

**Tasks:**

1. Add `connector_resource_key(org_id, kb_slug, connector_id, generation) -> str`.
2. Add parser/validator for `connector:{org_id}:{kb_slug}:{connector_id}:{generation}`.
3. Add `CONNECTOR_WRITER_QUEUES = [CRAWL_JOBS, ENRICH_BULK, GRAPHITI_BULK]`.
4. Add tests for stable key construction, malformed keys and queue-list membership.

**Exit:** tests prove the key is exact-matchable and queues are explicit.

---

## Fase 2 — Enqueue payload threading

**Goal:** all connector-owned jobs carry `resource_key` from enqueue.

**Files:**

- `routes/ingest.py`
- `crawl_tasks.py`
- `enrichment_tasks.py`
- connector/crawl enqueue code as needed
- tests around existing ingest/crawl enqueue flows

**Tasks:**

1. Determine the generation source. v1 decision: caller-supplied at sync start (sync-run id or epoch from the sync orchestrator) — no portal-api schema migration. An explicit portal generation column is a documented follow-up, not part of this SPEC.
2. Thread `resource_key` into `run_crawl` when `connector_id` is present.
3. Store the same `resource_key` on artifact `extra` so enrichment can load it from Postgres.
4. Thread `resource_key` into `enrich_document_bulk` and `ingest_graphiti_episode` args.
5. Keep manual upload paths unchanged unless they are connector-owned.

**Exit:** queued crawl/enrich/graphiti jobs for the same connector generation have the same top-level `args.resource_key`.

---

## Fase 3 — Cancellation helper

**Goal:** delete/rebuild cancels live jobs by exact resource key.

**Files:**

- `resource_jobs.py`
- `connector_cleanup.py`
- `routes/crawl_sync.py` only if shared helper replaces local logic
- tests

**Tasks:**

1. Add `list_live_jobs_by_resource_key(pool, resource_key, queues)`.
2. Add `cancel_jobs_by_resource_key(proc_app, resource_key, queues)` using `cancel_job_by_id_async(..., abort=True, delete_job=False)`.
3. Filter `status IN ('todo', 'doing')` (Procrastinate 3.x: `aborting` is legacy/unused; abort-requested jobs stay `doing`).
4. Replace connector cleanup's artifact-id/json-path cancellation with resource-key cancellation where payloads exist. Note: the current `_cancel_enrichment_jobs` filter on `args->'extra_payload'->>'source_connector_id'` is dead code (enrichment args carry only `artifact_id` since SPEC-INGEST-CONTENT-PG-001) — remove it, don't preserve it.
5. [x] Keep a compatibility fallback for old queued jobs that do not yet carry `resource_key` during rollout: match enrichment/graphiti jobs via `args->>'artifact_id' = ANY(<snapshot ids>)` using the artifact-id snapshot that purge already takes. Removed on 2026-09-01 after production verification found zero live legacy jobs without `resource_key`.

**Exit:** cancellation is exact-match on `args->>'resource_key'`, not `args::text LIKE`.

---

## Fase 4 — Fence checks

**Goal:** stale jobs that survive cancellation cannot write.

**Files:**

- `connector_state.py`
- `resource_jobs.py`
- `crawl_tasks.py`
- `enrichment_tasks.py`
- tests

**Tasks:**

1. Add `check_connector_resource_fence(resource_key) -> FenceState`.
2. Implement `active`, `stale_generation`, `deleting`, `deleted`, `unknown_error`.
3. Call the fence before each crawl per-page ingest/write loop.
4. Call the fence after artifact load and before enrichment LLM/embedding work.
5. Call the fence immediately before Qdrant upsert.
6. Call the fence before Graphiti ingest and before `graphiti_episode_id` persistence.
7. Preserve artifact existence checks.

**Exit:** a stale resource key skips with structured logs and performs no writes.

---

## Fase 5 — Monitoring query

**Goal:** one status report for connector-scoped jobs.

**Files:**

- `resource_jobs.py`
- `tests/test_connector_resource_monitoring.py`

**Tasks:**

1. Add `get_resource_job_counts(pool, resource_key)`. Helper + runbook query only — no new route or admin endpoint in v1 (YAGNI; add one when a UI consumer exists).
2. Count raw statuses: `todo`, `doing`, `cancelled`, `aborted`, `failed`, `succeeded` (3.x vocabulary; no `aborting`).
3. Add derived buckets: `pending`, `running`, `terminal`, `failed_visible`.
4. Ensure `cancelled` and `aborted` do not appear as user-visible failures for delete/rebuild.

**Exit:** seeded status tests pass and the query uses `args->>'resource_key' = $1`.

---

## Fase 6 — Delete/rebuild integration

**Goal:** connector delete and rebuild establish the fence before cleanup and before new work.

**Files:**

- `connector_cleanup.py`
- `connector_purge_tasks.py`
- portal/connector rebuild orchestration as needed
- regression tests

**Tasks:**

1. Mark old generation fenced before cancellation.
2. Cancel by old resource key.
3. Run idempotent cleanup.
4. For rebuild, create/increment generation and enqueue new work with the new resource key.
5. Add regression: old queued enrichment/graphiti jobs skip while new generation writes normally.

**Exit:** delete/rebuild no longer depends on artifact-id snapshots as the primary guard.

---

## Fase 7 — Docs and operational checks

**Goal:** future connector-scoped jobs cannot miss the contract.

**Tasks:**

1. Update `.claude/rules/klai/projects/knowledge.md` with a new rule: connector-scoped async jobs must carry `resource_key` and fence before writes.
2. Add a short runbook query for resource-key job counts.
3. Update relevant spec status/progress if implementation lands.

**Exit:** new workers have a clear rule and a monitoring query.

---

## Verification

Run focused tests:

```bash
cd klai-knowledge-ingest
uv run pytest tests/test_resource_jobs.py tests/test_connector_resource_fencing.py tests/test_connector_resource_monitoring.py
```

Run existing relevant tests:

```bash
cd klai-knowledge-ingest
uv run pytest tests/test_connector_cleanup.py tests/test_worker_lifecycle.py tests/test_queues_constants.py tests/test_rebuild_tasks.py
```

Live smoke after deploy:

1. Start a connector sync with enough pages to create `enrich-bulk` and `graphiti-bulk` backlog.
2. Record `resource_key`.
3. Delete or rebuild the connector before the queues drain.
4. Verify live job counts move from `todo/doing` to `cancelled` or terminal skip.
5. Verify Qdrant and Graphiti counts for the old resource key stay at zero after cleanup.
6. Verify the new generation, if rebuild, writes only under the new connector generation.

---

## Risks

| Risk | Mitigation |
|---|---|
| Existing queued jobs do not have `resource_key` during deploy | Keep compatibility fallback for one deploy window, then remove in a follow-up. |
| Generation source is ambiguous | Prefer explicit connector generation column over timestamps. If using timestamps, document precision and monotonicity. |
| `doing` jobs ignore cancellation until after a long LLM call | Fence immediately before writes, not only before expensive calls. |
| Monitoring treats cancelled as failed | Add dedicated derived bucket where `cancelled` is terminal but not `failed_visible`. |
| New connector-scoped queue is added later without cancellation | Test `CONNECTOR_WRITER_QUEUES` against queue constants and require explicit opt-in. |
