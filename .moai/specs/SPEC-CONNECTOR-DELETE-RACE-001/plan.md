---
id: SPEC-CONNECTOR-DELETE-RACE-001
version: "1.0"
status: draft
---

# Implementation plan

Vijf fases. Fase 1-3 zijn de fix zelf, Fase 4-5 zijn verificatie. Eén branch: `fix/SPEC-CONNECTOR-DELETE-RACE-001`.

---

## Fase 1 — Job-cancellation helper

**Files:**
- `klai-knowledge-ingest/knowledge_ingest/pg_store.py` — add `cancel_connector_jobs(connector_id: str, pool) -> int`
- `klai-knowledge-ingest/tests/test_pg_store_cancel_jobs.py` (new)

**Implementation:**
```python
async def cancel_connector_jobs(connector_id: str, pool: asyncpg.Pool) -> int:
    """Mark all todo-state enrich/graphiti jobs for this connector as cancelled.

    Uses args::text LIKE match because Procrastinate stores args as jsonb but
    we need to scan nested keys (`extra_payload.source_connector_id`).
    Returns the number of jobs cancelled.
    """
    row = await pool.fetchrow(
        """
        UPDATE procrastinate_jobs
        SET status = 'cancelled'
        WHERE queue_name IN ('enrich-bulk', 'graphiti-bulk')
          AND status = 'todo'
          AND args::text LIKE $1
        RETURNING (SELECT count(*) FROM procrastinate_jobs
                   WHERE queue_name IN ('enrich-bulk','graphiti-bulk')
                   AND status = 'cancelled'
                   AND args::text LIKE $1) AS cancelled_count
        """,
        f"%{connector_id}%",
    )
    return row["cancelled_count"] if row else 0
```

**Tests (RED):**
- `test_cancel_connector_jobs_marks_todo_only` — seed 3 todo + 1 doing + 1 succeeded, assert only 3 cancelled.
- `test_cancel_connector_jobs_scoped_to_connector_id` — seed jobs for connector A + B, assert only A cancelled.
- `test_cancel_connector_jobs_returns_count` — assert return value equals cancelled row count.

Fase 1 exit: 3 RED tests pass, `pg_store.cancel_connector_jobs` committed.

---

## Fase 2 — Existence-check top-of-task

**Files:**
- `klai-knowledge-ingest/knowledge_ingest/enrichment_tasks.py` — add artifact-check at start of `enrich_document_bulk` and `ingest_graphiti_episode`
- `klai-knowledge-ingest/knowledge_ingest/pg_store.py` — `artifact_exists(artifact_id: str, pool) -> bool` helper
- `klai-knowledge-ingest/tests/test_enrichment_skip_deleted.py` (new)

**Implementation:**
```python
# In enrichment_tasks.py
async def enrich_document_bulk(..., artifact_id: str, ...):
    pool = await get_pool()
    if not await pg_store.artifact_exists(artifact_id, pool):
        logger.info(
            "skipped_artifact_deleted",
            artifact_id=artifact_id,
            connector_id=extra_payload.get("source_connector_id"),
            task="enrich_document_bulk",
        )
        return  # succeeded status, no downstream calls

    # ... rest unchanged
```

Same pattern voor `ingest_graphiti_episode`.

**Tests (RED):**
- `test_enrich_document_bulk_skips_deleted_artifact` — mock `artifact_exists=False`, call task, assert no Qdrant mock invocations.
- `test_enrich_document_bulk_runs_when_artifact_exists` — mock `artifact_exists=True`, assert normal flow.
- `test_ingest_graphiti_episode_skips_deleted_artifact` — same for Graphiti.

Fase 2 exit: 3 RED tests pass, top-of-task guards committed.

---

## Fase 3 — Second-pass in delete_connector_route

**Files:**
- `klai-knowledge-ingest/knowledge_ingest/routes/ingest.py` — extend `delete_connector_route`
- `klai-knowledge-ingest/knowledge_ingest/config.py` — add `connector_delete_drain_seconds: int = 60`
- `klai-knowledge-ingest/tests/test_delete_connector_second_pass.py` (new)

**Implementation:**
```python
# In routes/ingest.py
async def delete_connector_route(...):
    _verify_internal_secret(request)
    pool = await get_pool()

    # NEW: cancel todo jobs BEFORE any cleanup
    cancelled = await pg_store.cancel_connector_jobs(connector_id, pool)
    logger.info("connector_jobs_cancelled",
                connector_id=connector_id, count=cancelled)

    # Existing cleanup unchanged
    episode_ids = await pg_store.get_connector_episode_ids(...)
    await graph_module.delete_kb_episodes(org_id, episode_ids)
    await qdrant_store.delete_connector(org_id, kb_slug, connector_id)
    artifacts_deleted = await pg_store.delete_connector_artifacts(...)

    # NEW: schedule second-pass
    asyncio.create_task(
        _second_pass_cleanup(
            org_id, kb_slug, connector_id,
            delay=settings.connector_delete_drain_seconds,
        )
    )

    return {"status": "ok", ...}


async def _second_pass_cleanup(org_id, kb_slug, connector_id, delay):
    await asyncio.sleep(delay)
    leaked = await qdrant_store.count_connector_chunks(org_id, kb_slug, connector_id)
    if leaked > 0:
        logger.warning("connector_delete_leak_observed",
                       connector_id=connector_id, count=leaked)
        await qdrant_store.delete_connector(org_id, kb_slug, connector_id)
    logger.info("connector_delete_second_pass",
                connector_id=connector_id, chunks_deleted=leaked)
```

**Tests (RED):**
- `test_delete_connector_cancels_jobs_before_cleanup` — spy on call order, assert cancel-before-delete.
- `test_delete_connector_schedules_second_pass` — assert `asyncio.create_task` called.
- `test_second_pass_deletes_leaked_chunks` — integration: seed 1 chunk after first delete, run second pass, assert chunk gone.

Fase 3 exit: 6 tests pass (3 new + regression of Fase 1-2), route committed.

---

## Fase 4 — Regression coverage (no testcontainers)

**Context:** klai-knowledge-ingest has no testcontainers or pytest-docker
setup; existing integration tests rely on AsyncMock pools. A pure
testcontainers fixture is out of scope here — we keep the harness simple.

**Two-part approach:**

### 4a — Pool-mock integration test
`klai-knowledge-ingest/tests/test_delete_race_regression.py` uses the same
`_make_pool` helper pattern as `test_crawl_sync_endpoint.py`:

- Seed the mock pool with 5 `procrastinate_jobs` rows in `todo` for the
  target connector_id.
- Seed `knowledge.artifacts` mock with 5 corresponding rows.
- Call `delete_connector_route`.
- Assert `UPDATE procrastinate_jobs SET status='cancelled'` was issued with
  correct WHERE clause (via pool.execute call history).
- Assert `qdrant_store.delete_connector` was called twice (primary + second
  pass).
- Assert `_second_pass_cleanup` task was scheduled.

This gives us the call-order contract without needing a real database.

### 4b — Live smoketest checklist (Fase 5)
The real integration proof is the Fase 5 live run on voys tenant. Fase 4b
adds a structured checklist to `progress.md` that captures:

- Pre-delete: `todo` job count, Qdrant chunk count.
- Immediate post-delete: same two counts (expect 0 todo, small Qdrant
  count equal to `doing`-state chunks).
- Post drain window (60s): Qdrant count must be 0.
- Log lines observed: `connector_jobs_cancelled`, `connector_delete_second_pass`.

Fase 4 exit: mock-based regression test green, Fase 5 checklist documented
in `progress.md`.

---

## Fase 5 — Live E2E op voys tenant

Handmatig (Playwright MCP) na deploy naar core-01. Follow verification plan in `spec.md`:
1. Create fresh Redcactus connector.
2. Sync 90 pages.
3. Record `todo` job count (expect 80+).
4. Delete via UI.
5. Immediate check: `todo=0`, all `cancelled`.
6. Wait 90s.
7. Check Qdrant count for connector_id = 0.
8. Check logs: `connector_jobs_cancelled`, `connector_delete_second_pass`, no `connector_delete_leak_observed` (or if fired, inspect count).

Fase 5 exit: screenshot + log lines pasted into `progress.md`, SPEC status → `implemented`.

---

## Commit strategy

- Één branch `fix/SPEC-CONNECTOR-DELETE-RACE-001`.
- Per fase een commit: `feat(spec): SPEC-CONNECTOR-DELETE-RACE-001 Fase N — <scope>`.
- Cherry-pick elke fase naar main na CI-groen, zoals SPEC-CRAWLER-005 deed.
- Geen squash — de afzonderlijke fasen willen we terugkunnen vinden.

---

## Risico's

| Risico | Mitigatie |
|---|---|
| `UPDATE procrastinate_jobs` lockt de tabel | Filter op `status='todo'` + `queue_name IN (...)` houdt de scope klein; Procrastinate gebruikt row-level locks, niet tabel. |
| `args::text LIKE` match is te breed (connector_id als substring in andere data) | UUID's zijn uniek genoeg dat false positives zeldzaam zijn. Fallback: parse de jsonb `args->'extra_payload'->>'source_connector_id'` voor exact match. |
| `asyncio.create_task` laat de task verloren gaan als de response wordt verzonden voordat de task klaar is | FastAPI's event loop blijft draaien; `create_task` heeft genoeg lifetime. Als extra veiligheid kunnen we een weak reference opslaan (`app.state.second_pass_tasks`). |
| Existence-check voegt 1 SELECT per enrichment job toe | Primary key lookup op een geïndexeerde UUID = <1ms. Acceptabel. |
| Drain window van 60s is te kort als Graphiti-jobs langer dan 60s duren | Configureerbaar via `connector_delete_drain_seconds`. Default kan later omhoog. |

---

## Niet meegenomen

- Hergebruik van de oude connector-URL onder een nieuwe connector — separate dedup-SPEC.
- Bulk-backfill van reeds-lekke orphan chunks uit eerdere delete-races — separate data-migratie.
- Frontend "delete nog bezig" spinner — race is server-side, UI ziet niks.
