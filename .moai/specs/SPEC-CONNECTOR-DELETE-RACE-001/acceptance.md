---
id: SPEC-CONNECTOR-DELETE-RACE-001
version: "1.0"
status: draft
---

# Acceptance criteria

## Per-requirement

### REQ-01 — Cancel todo jobs at delete time

- [ ] `pg_store.cancel_connector_jobs(connector_id, pool)` exists and returns an int.
- [ ] Helper marks only `status='todo'` rows; leaves `doing`, `succeeded`, `failed`, `cancelled` untouched.
- [ ] Helper is scoped to `queue_name IN ('enrich-bulk','graphiti-bulk')`.
- [ ] Helper filters by connector_id via `args::text LIKE '%<uuid>%'`; jobs for other connectors are untouched.
- [ ] `delete_connector_route` calls `cancel_connector_jobs` before any Qdrant/Postgres cleanup.
- [ ] Structured log `connector_jobs_cancelled` fires with `connector_id` and `count` fields.

### REQ-02 — Existence-check top of enrichment task

- [ ] `pg_store.artifact_exists(artifact_id, pool)` exists; returns bool.
- [ ] `enrich_document_bulk` returns immediately when `artifact_exists()` is False.
- [ ] `ingest_graphiti_episode` returns immediately when `artifact_exists()` is False.
- [ ] Skip path logs `skipped_artifact_deleted` at `info` level.
- [ ] Skipped jobs do NOT call `upsert_enriched_chunks`, `graphiti.add_episode`, TEI, or sparse embedding.
- [ ] Skipped jobs succeed from Procrastinate's perspective (not retried, not failed).

### REQ-03 — Second-pass Qdrant cleanup

- [ ] `settings.connector_delete_drain_seconds` defaults to 60.
- [ ] `delete_connector_route` schedules `_second_pass_cleanup` via `asyncio.create_task` after primary cleanup.
- [ ] `_second_pass_cleanup` sleeps `drain_seconds`, counts chunks, deletes if >0.
- [ ] Second pass logs `connector_delete_second_pass` with `chunks_deleted` field (may be 0).
- [ ] If second pass finds >0 chunks, `connector_delete_leak_observed` warning is emitted with count.
- [ ] Second-pass task does not block the HTTP response.

### REQ-04 — Tests

Unit tests (klai-knowledge-ingest/tests/):
- [ ] `test_pg_store_cancel_jobs.py::test_cancel_connector_jobs_marks_todo_only`
- [ ] `test_pg_store_cancel_jobs.py::test_cancel_connector_jobs_scoped_to_connector_id`
- [ ] `test_pg_store_cancel_jobs.py::test_cancel_connector_jobs_returns_count`
- [ ] `test_enrichment_skip_deleted.py::test_enrich_document_bulk_skips_deleted_artifact`
- [ ] `test_enrichment_skip_deleted.py::test_enrich_document_bulk_runs_when_artifact_exists`
- [ ] `test_enrichment_skip_deleted.py::test_ingest_graphiti_episode_skips_deleted_artifact`
- [ ] `test_delete_connector_second_pass.py::test_delete_connector_cancels_jobs_before_cleanup`
- [ ] `test_delete_connector_second_pass.py::test_delete_connector_schedules_second_pass`
- [ ] `test_delete_connector_second_pass.py::test_second_pass_deletes_leaked_chunks`

Regression test (pool-mock):
- [ ] `test_delete_race_regression.py::test_delete_connector_cancels_pending_jobs` — mock pool with 5 todo rows, assert cancel SQL issued + second-pass scheduled.
- [ ] `test_delete_race_regression.py::test_delete_connector_call_order` — assert cancel-before-Qdrant-delete-before-second-pass ordering.

## Definition of Done

- [ ] All 10 unit/integration tests pass in CI.
- [ ] Pyright clean on touched files.
- [ ] Ruff clean on touched files.
- [ ] Live smoketest on voys tenant: delete of a 90-page connector results in Qdrant chunk count == 0 after 90-second wait.
- [ ] Pitfall entry in `.claude/rules/klai/projects/knowledge.md` is updated: change from "Documented, not fixed" to "Fixed in SPEC-CONNECTOR-DELETE-RACE-001, see …".
- [ ] `progress.md` records Fase 5 evidence (log lines + Qdrant count before/after).

## Out-of-scope (explicit)

- Hergebruik van connector-URL onder nieuwe connector_id → separate dedup-SPEC.
- Backfill cleanup van bestaande orphan chunks uit eerdere delete-races → separate data-migratie.
- Frontend delete-in-progress indicator.

## Regression watch list

Na deploy blijven deze endpoints stabiel:

- `POST /ingest/v1/crawl/sync` (hoofdpad — enqueuet enrichment)
- `POST /ingest/v1/ingest` (single-document ingest — zelfde enrichment queue)
- `DELETE /ingest/v1/kb` (whole-KB delete — deelt enrichment task signatures)
- Retrieval-api search op connector die net is gesync'd (mag geen duplicaten tonen)
