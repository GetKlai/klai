# Runbook: PG/Qdrant Reconciliation Alert

Alerts:

- `pg_qdrant_reconcile_failed`
- `pg_qdrant_reconcile_missing`

## Meaning

The alert fires on two distinct statuses of the nightly read-only consistency job:

- `status=failed` — at least one mismatch between active synced rows in
  `knowledge.artifacts` and distinct Qdrant chunk payloads (drift).
- `status=error` — the job itself crashed (Qdrant/PG unreachable, timeout after
  15 minutes). The same log event carries the exception traceback.

This job does not repair or delete anything. Treat `failed` as a drift detector,
`error` as a job failure, and `missing` as a dead-man signal that the job did
not report at all.

### Tolerances and blind spots

- **15-minute race window**: artifacts created or closed within 15 minutes of
  the run are excluded from the diff, so ordinary in-flight ingest at 03:30 UTC
  does not flap the alert. Residual risk: a re-enrichment of an *old* artifact
  (delete-then-upsert with no PG timestamp change) caught mid-flight can still
  produce a one-night false `missing_in_qdrant`; if the count clears the next
  night, that was the cause.
- **Artifact-identity scope only**: the v1 check compares distinct
  `(org_id, kb_slug, path, artifact_id)` keys. It does not verify chunk text,
  chunk count, metadata parity, or vector freshness.
- **First-run baseline**: if this alert fires immediately after deployment,
  inspect the counts before repairing anything. Historical pending/superseded
  artifacts can reveal old drift; that baseline is useful signal, not proof that
  the latest deploy caused the issue.

## Triage

1. In Grafana Explore, query VictoriaLogs:

   ```text
   service:knowledge-ingest AND event:pg_qdrant_reconcile AND (status:failed OR status:error)
   | sort by(_time) desc
   | limit 5
   ```

   For `status=error`: fix the infrastructure cause (Qdrant/PG availability,
   timeout, or schema/runtime crash) and wait for the next nightly run. Steps
   below apply to drift (`status=failed`).

2. Check these fields on the newest event:

   - `missing_in_qdrant`: active synced PG artifacts with no Qdrant payload.
   - `orphaned_in_qdrant`: Qdrant artifact payloads without an active synced PG row.
   - `missing_sample` / `orphaned_sample`: sample artifact keys.

3. If the samples point to a recent ingest failure, inspect neighboring
   `level:error` logs for the same `org_id`, `kb_slug`, or `artifact_id`.

4. Pick the repair path deliberately:

   - Missing in Qdrant: reindex/rebuild that KB or artifact from PG/source.
   - Orphaned in Qdrant: verify the PG row is truly superseded/deleted before
     deleting Qdrant points.

Do not silence the alert by manually deleting rows without confirming source of
truth. The full transactional outbox is tracked separately as `GAP-SYNC-01` H2.

## Missing Run Alert

`pg_qdrant_reconcile_missing` fires when no `pg_qdrant_reconcile` event appeared
in the last 25 hours.

1. Check whether the knowledge-ingest worker is running and subscribed to the
   `rag-eval` queue.
2. Check worker logs for `pg_qdrant_reconcile_periodic_fired`.
3. If the worker is healthy, verify that the Procrastinate periodic deferrer is
   active and that `register_consistency_reconcile_task()` is registered during
   `enrichment_tasks.init_app()`.
4. Once fixed, run or wait for the next reconcile. The missing-run alert
   resolves after any `pg_qdrant_reconcile` event is logged.
