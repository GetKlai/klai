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
   - `orphaned_in_qdrant`: Qdrant artifact payloads without an active PG row. An
     active row with `index_status=failed` (enrichment failed) keeps its raw
     chunks by design and does not count as an orphan.
   - `missing_sample` / `orphaned_sample`: sample artifact keys.

3. If the samples point to a recent ingest failure, inspect neighboring
   `level:error` logs for the same `org_id`, `kb_slug`, or `artifact_id`.

4. Pick the repair path deliberately:

   - Missing in Qdrant: reindex/rebuild that KB or artifact from PG/source.
   - Orphaned in Qdrant: verify the PG row is truly superseded/deleted before
     deleting Qdrant points.
   - `graph_lost_extractions` (per org in `graph_lost_by_org`, ids in
     `graph_lost_sample`): active documents whose finished graph extraction
     records an episode the graph does not hold. FalkorDB persists by RDB
     snapshot only, so a crash or a kill during the shutdown save loses the
     writes since the last snapshot while Postgres already says "complete";
     the 60 s `stop_grace_period` on the `falkordb` service exists for that.
     Rows created within 24 hours are left out (an extraction may still be
     running). Repair one document with
     `python -m scripts.reextract_document_graph --org <org> --artifact <id>`
     (dry run first, then `--execute`, which needs `INGEST_OK=1`). `graph_check=error` means the graph
     could not be read: that is a job failure, not a count. One unrepaired
     loss keeps the nightly run at `status=failed`, and this alert firing,
     every night until the repair command has been run for it; the same
     holds for the older PG/Qdrant discrepancies. `status` covers both
     checks, so read `discrepancies_total` (Qdrant) and
     `graph_lost_extractions` (graph) to see which one failed.

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

## Points Without A Sparse Vector

`qdrant_points_without_sparse` fires when the nightly `pg_qdrant_reconcile`
event carries `points_without_sparse` above zero: points in `klai_knowledge`
that hybrid search finds through the dense vector only.

Ingest refuses to write such a point: an unavailable sparse sidecar fails the
base ingest request before anything is written, and fails an enrichment job,
which retries every 2 minutes for 16 minutes and then records the document
`failed (sparse_unavailable)`. A count above zero is therefore a point written
before that guard, or a writer that bypasses `qdrant_store._replace_points`.

1. Count per knowledge base, read-only (inside the knowledge-ingest container):
   `python -m knowledge_ingest.scripts.sparse_backfill --org <org_id>`
   without `--execute`. It prints chunks with and without a sparse vector.
2. Find the writer: the points' `artifact_id` and the time they were written.
   A new writer that bypasses `_replace_points` is the bug to fix first.
3. The repair writes only the missing `vector_sparse` of those points
   (`--execute`, per org and optionally `--kb`); it never rebuilds a knowledge
   base. It rewrites production knowledge data, so it runs only after the
   owner approved that one run, with the dry-run counts in front of them.
