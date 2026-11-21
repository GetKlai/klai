---
id: SPEC-CONNECTOR-CANCEL-001
version: "1.0"
status: draft
---

# Acceptance Criteria

## REQ-CONNECTOR-CANCEL-001-01 — Resource key on jobs

```gherkin
Given a connector sync for org O, kb K, connector C, generation G
When the system enqueues crawl, enrichment and graphiti work
Then every connector-owned procrastinate job has args.resource_key = "connector:O:K:C:G"
  And manual upload jobs without source_connector_id may omit resource_key
  And no connector-owned writer job depends on queueing_lock as its ownership key
```

## REQ-CONNECTOR-CANCEL-001-02 — Cancel by exact resource key

```gherkin
Given procrastinate_jobs contains live jobs for resource_key R
  And live jobs for resource_key R2
When connector delete for R starts
Then jobs for R with status todo or doing receive a cancellation request
  And jobs for R2 are untouched
  And connector-purge jobs are untouched
  And the cancellation query uses args->>'resource_key' = R
  And the cancellation query does not use args::text LIKE
```

## REQ-CONNECTOR-CANCEL-001-03 — Stale generation fence

```gherkin
Given connector C has current generation G2
  And an old enrichment job has resource_key generation G1
  And the artifact row still exists
When the old enrichment job starts
Then the fence returns stale_generation
  And the job logs connector_resource_job_skipped
  And no LLM call is made
  And no embedding call is made
  And no Qdrant upsert is made
```

## REQ-CONNECTOR-CANCEL-001-04 — Deleted connector fence

```gherkin
Given connector C has been deleted
  And a graphiti job for C is still doing
When the job reaches the pre-write checkpoint
Then the fence returns deleted
  And no Graphiti episode is written
  And knowledge.artifacts.extra is not patched with graphiti_episode_id
```

## REQ-CONNECTOR-CANCEL-001-05 — Artifact guard remains

```gherkin
Given an artifact-scoped job has artifact_id A and resource_key R
  And artifact A no longer exists
When the job starts
Then the job skips successfully
  And the skip log includes artifact_id A
  And the skip log includes resource_key R when available
```

## REQ-CONNECTOR-CANCEL-001-06 — Cleanup is idempotent

```gherkin
Given connector C has already been purged once
When purge_connector runs again for the same resource
Then it returns successfully
  And deletion counts are zero or lower than the first run
  And no Qdrant, Graphiti, Postgres or Garage delete step raises because rows are already gone
```

## REQ-CONNECTOR-CANCEL-001-07 — Rebuild uses new generation

```gherkin
Given connector C generation G1 has queued jobs
When connector C is rebuilt
Then generation G1 is fenced
  And live G1 jobs are cancelled
  And rebuild enqueues new jobs with generation G2
  And G1 jobs that survive cancellation skip before writes
  And G2 jobs can write normally
```

## REQ-CONNECTOR-CANCEL-001-08 — Monitoring counts statuses correctly

```gherkin
Given procrastinate_jobs contains one job for resource_key R in each status:
  | status    |
  | todo      |
  | doing     |
  | cancelled |
  | aborted   |
  | failed    |
  | succeeded |
When get_resource_job_counts(R) runs
Then raw counts are:
  | status    | count |
  | todo      | 1     |
  | doing     | 1     |
  | cancelled | 1     |
  | aborted   | 1     |
  | failed    | 1     |
  | succeeded | 1     |
  And pending = 1
  And running = 1
  And terminal = 4
  And failed_visible = 1
```

## REQ-CONNECTOR-CANCEL-001-09 — Queue lane contract

```gherkin
Given queues.py defines IO_QUEUES and LLM_QUEUES
When connector writer queues are validated
Then crawl-jobs is included as a cancellable connector writer queue
  And enrich-bulk is included as a cancellable connector writer queue
  And graphiti-bulk is included as a cancellable connector writer queue
  And connector-purge is not cancelled as a child writer job
  And IO_QUEUES and LLM_QUEUES remain disjoint
```

## Definition of Done

- [ ] `resource_jobs.py` or equivalent central helper exists.
- [ ] Connector-owned Procrastinate jobs carry top-level `resource_key`.
- [ ] Delete/rebuild cancellation filters exact `args->>'resource_key'`.
- [ ] Fence checks run before expensive work and before writes.
- [ ] Artifact existence checks remain in enrichment and Graphiti paths.
- [ ] Cleanup remains idempotent under repeated purge.
- [ ] Monitoring returns raw and derived status counts.
- [ ] Tests cover resource key construction, cancellation, fencing, monitoring and rebuild generation.
- [ ] `tests/test_queues_constants.py` or a dedicated test prevents connector writer queues from being omitted.
- [ ] Live smoke proves old-generation jobs do not regrow Qdrant/Graphiti after delete or rebuild.

## Regression Watch List

- `POST /ingest/v1/document` connector path.
- `POST /ingest/v1/crawl/sync` and `POST /ingest/v1/crawl/sync/{job_id}/cancel`.
- `connector_cleanup.purge_connector`.
- `connector_purge_task` retries.
- `enrich-bulk` and `graphiti-bulk` backlog drain after delete.
- Rebuild jobs that enqueue while old generation is still cancelling.
