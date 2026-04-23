---
id: SPEC-CONNECTOR-DELETE-RACE-001
version: "1.0"
status: draft
created: 2026-04-23
updated: 2026-04-23
author: Mark Vletter
priority: high
issue_number: 0
---

## HISTORY

| Version | Date | Author | Change |
|---------|------|--------|--------|
| 1.0 | 2026-04-23 | Mark Vletter | Initial draft. Race geobserveerd tijdens Redcactus E2E op voys tenant: Qdrant chunk-count voor een connector die om 09:29 werd gedeleted groeide tot 354 chunks om 10:05 omdat in-flight Procrastinate enrichment-jobs door bleven schrijven met de oude `source_connector_id`. |

---

# SPEC-CONNECTOR-DELETE-RACE-001: In-flight enrichment jobs moeten niet doorschrijven na connector-delete

## Context

Het huidige `delete_connector_route` in `klai-knowledge-ingest/knowledge_ingest/routes/ingest.py:708` is een momentopname:

1. Verwijder Graphiti episodes
2. Verwijder Qdrant chunks met `source_connector_id=X`
3. Verwijder `knowledge.artifacts`/`crawled_pages`/`page_links` rows van connector X

Alles wat op dat moment in de database staat wordt netjes opgeruimd. Het probleem zit in wat er daarna nog aankomt.

### Root cause — async enrichment lees geen existence-flag

`knowledge-ingest` gebruikt Procrastinate om chunks na ingest te verrijken. Een bulk-crawl van 90 pagina's enqueuet 90 jobs in `enrich-bulk` en nog 90 in `graphiti-bulk`. Die jobs verbruiken de artifacts trage kant (~1-2 jobs/min door de LLM-bottleneck in Graphiti) zodat een backlog ontstaat.

Als de gebruiker de connector deletet terwijl de backlog nog 50-80 jobs diep is:

- De huidige `delete_connector_route` ruimt de Qdrant chunks en Postgres rows op (correct).
- De 50-80 jobs die nog in de queue staan lezen hun payload uit `procrastinate_jobs.args` (bevroren bij enqueue). Die bevat `extra_payload["source_connector_id"] = X`.
- Elke job draait `enrich_document_bulk` → `upsert_enriched_chunks` → **nieuwe** Qdrant points met de oude connector_id.
- Netto: de connector "leeft" in Qdrant nog 20-40 minuten door na de delete.

### Impact

Live waargenomen op `voys.getklai.com/app/knowledge/support` tijdens de Redcactus E2E van 2026-04-23:

| Tijd (UTC) | Qdrant chunks Redcactus | Delta |
|---|---|---|
| 09:29:10 (delete bevestigd) | ~875 (vóór cleanup) | — |
| 09:29:30 (+20s) | 15 | cleanup uitgevoerd |
| 09:30:50 (+100s) | 26 | +11 |
| 09:54:00 (+25min) | 325 | +299 |
| 09:54:30 | 339 | +14 |
| 09:55:00 | 339 | +0 |
| 09:55:30 | 354 | +15 |

De race stopt pas wanneer de Procrastinate queue drainaat is. Voor een gebruiker die direct na delete opnieuw een connector met dezelfde URL aanmaakt, resulteert dit in duplicaat-chunks: de nieuwe connector heeft eigen `source_connector_id` maar dezelfde URL, en de oude orphan-chunks zijn onvindbaar via de nieuwe UI (die filtert op connector_id) maar matchen wel op retrieval queries.

### Gekoppeld werk

- **SPEC-CRAWLER-005** heeft `source_connector_id`-threading gefixed — dat is de voorwaarde voor *elke* connector-scoped cleanup.
- **Pitfall `Connector-delete leaves in-flight enrichment jobs behind`** in `.claude/rules/klai/projects/knowledge.md` documenteert het fenomeen maar niet de fix.
- **SPEC-CONNECTOR-CLEANUP-001 REQ-04** (sync_runs FK CASCADE) is een ander data-layer gat; dit SPEC is complementair, niet overlappend.

---

## Scope

### In scope

1. Procrastinate job-cancellation bij connector-delete: alle `todo`-jobs in
   `enrich-bulk` en `graphiti-bulk` waarvan de args een match bevatten op de
   te verwijderen `connector_id`/`artifact_id` worden naar status `cancelled`
   gezet voordat de bestaande cleanup-stappen draaien. Jobs die op dat moment
   `doing` zijn lopen tot ze klaar zijn (kunnen niet veilig worden afgebroken).
2. Existence-check top-of-task: `enrich_document_bulk` en
   `ingest_graphiti_episode` controleren aan het begin of de eigenaar-artifact
   nog bestaat in `knowledge.artifacts`. Is die weg, dan stopt de job direct
   (structured log `skipped_artifact_deleted`, geen Qdrant-write, geen
   LLM-call).
3. Second-pass Qdrant cleanup: na een configureerbaar drain-window (default
   60s) voert `delete_connector_route` opnieuw `qdrant_store.delete_connector`
   uit om chunks te vegen die door `doing`-jobs zijn neergezet tussen step 1
   (cancel) en step 3 (existence-check dempt maar kan niet retroactief
   aangevraagd worden).
4. Unit tests voor alle drie de mechanismen met gemockte Procrastinate pool
   en een gemockte in-flight-job scenario.
5. Een regressie-integratie-test die uitvoert: enqueue 5 jobs → delete
   connector → assert dat Qdrant count 0 blijft na queue drain.

### Out of scope

- Hergebruik van de oude connector-URL onder een nieuwe connector_id (dat is
  een aparte dedup-concern, niet een delete-race).
- Generieke Procrastinate job-cancellation library (we gebruiken de minimale
  API die nu nodig is: `UPDATE procrastinate_jobs SET status='cancelled'`
  scoped op connector_id in args).
- Refactor van de enrichment queue naar een expliciete artifact-FK relatie
  (zou netter zijn maar is een veel grotere verandering).

---

## EARS Requirements

### REQ-01: Cancel todo jobs at delete time
**Ubiquitous.** When `delete_connector_route` is called, the system shall mark all Procrastinate jobs in `enrich-bulk` and `graphiti-bulk` queues with status `todo` AND args containing the target `connector_id` as `cancelled` BEFORE Qdrant/Postgres deletion runs.

**Acceptance:**
- Before deletion: baseline count of `todo` jobs with matching connector_id recorded.
- After deletion: same count of jobs has status `cancelled`, zero remain `todo`.
- Jobs with status `doing` or `succeeded` are untouched.

### REQ-02: Existence-check top of enrichment task
**Event-driven.** When an `enrich_document_bulk` or `ingest_graphiti_episode` job begins executing, if the `artifact_id` from `extra_payload` does not exist in `knowledge.artifacts`, the job shall return immediately without writing to Qdrant, Graphiti or any downstream system.

**Acceptance:**
- Job args contain `artifact_id=X`.
- `knowledge.artifacts` row for X is absent.
- Job logs `skipped_artifact_deleted` at `info` level with `artifact_id` and `connector_id` fields.
- No Qdrant upsert happens, no LLM call, no Graphiti call. Procrastinate marks job as succeeded (not failed).

### REQ-03: Second-pass Qdrant cleanup
**Event-driven.** When `delete_connector_route` completes REQ-01 and the existing cleanup, the system shall schedule a second `qdrant_store.delete_connector` invocation for the same connector_id after a configurable drain window (default 60s) to catch chunks that `doing`-state jobs wrote during that window.

**Acceptance:**
- Drain window is configurable via `settings.connector_delete_drain_seconds` (default 60).
- Second-pass logs `connector_delete_second_pass` with `chunks_deleted=N` (may be 0).
- If the second pass finds 0 chunks, the delete is considered clean and no alert fires.
- If the second pass finds >0 chunks, a `connector_delete_leak_observed` warning is logged with the count.

### REQ-04: Tests cover cancellation + skip behaviour
**Ubiquitous.** The delete-race fix shall be covered by unit tests for each of the three mechanisms and by one integration test simulating the full flow.

**Acceptance:**
- `test_delete_cancels_todo_jobs`: seeds 3 todo jobs + 1 succeeded job for connector X, calls delete, asserts 3 jobs are cancelled, 1 is succeeded.
- `test_enrichment_skips_deleted_artifact`: mocks `knowledge.artifacts` with artifact absent, runs enrich task, asserts no Qdrant upsert.
- `test_second_pass_cleans_chunks_written_during_drain`: simulates a doing job writing a chunk between first and second pass, asserts chunk is gone after second pass.
- `test_full_delete_race_regression`: enqueue 5 bulk jobs → delete → wait drain → assert Qdrant count == 0.

---

## Verification plan

### Live smoketest (post-deploy)

On `voys.getklai.com/app/knowledge/support`:

1. Create a fresh connector on `https://wiki.redcactus.cloud` with `path_prefix=/nl/`.
2. Wait for sync complete (90 pages, ~875 chunks). **Do not wait for enrichment queue to drain.**
3. Record baseline: `SELECT count(*) FROM procrastinate_jobs WHERE queue_name IN ('enrich-bulk','graphiti-bulk') AND status='todo' AND args::text LIKE '%<connector_id>%'` — expect 80-90 jobs.
4. Delete the connector via UI.
5. Within 5 seconds query the same count — expect 0 todo jobs (all cancelled).
6. Wait 90 seconds (covers the 60s drain window + network latency).
7. Query Qdrant chunk count for the deleted connector_id — expect 0.
8. Query `knowledge.artifacts` — expect 0 rows.
9. Verify logs show: one `connector_deleted`, one `connector_delete_second_pass`. If `connector_delete_leak_observed` fires, investigate.

### Regression test in CI

Add `tests/test_delete_race_regression.py` to `klai-knowledge-ingest`. It must:
- Stand up a real Postgres fixture (testcontainers).
- Enqueue 5 enrichment jobs via `defer_async`.
- Call `delete_connector_route` before any job runs.
- Wait `drain_seconds + 5s`.
- Assert Qdrant has 0 points with the target `source_connector_id`.

---

## Non-goals

- Making enrichment jobs "idempotent against re-delete". If a user deletes a
  connector, then re-creates, then deletes again before the FIRST delete's
  drain window elapses, edge cases may still leak. Document that behaviour
  but do not solve it here.
- Backward cleanup of existing orphan Qdrant chunks from previous delete-race
  incidents. That is a separate data-migration SPEC.
- Frontend "deletion is still in progress" spinner. The race is invisible to
  the UI because the existing cleanup is synchronous from the UI's
  perspective; this SPEC only removes the server-side leak.
