# Runbook: Operator-Triggered KB Rebuild (`rebuild_kb`)

**SPEC:** SPEC-RAG-REBUILD-KB-001  
**Service:** `klai-knowledge-ingest`  
**Queue:** `rebuild-kb` (LLM lane)  
**Author:** operator  

---

## When to run

Run `rebuild_kb` after deploying **pipeline changes that alter how chunks are produced**:

| SPEC | Change | Rebuild needed? |
|------|--------|----------------|
| SPEC-RAG-CONTEXTUAL-001 | Chunk enrichment now uses a per-document `document_summary` instead of the full document body in the context prompt | Yes — existing chunks have no summary-driven prefix |
| SPEC-RAG-PARENT-CHILD-001 | Chunks are now produced as small child + large parent pairs; parents persisted to `knowledge.parent_chunks` | Yes — existing artifacts have no `parent_chunks` rows |
| SPEC-RAG-TAXONOMY-001 | Retrieval changes only, no ingest change | No |

Do **not** run for:
- Retrieval-only changes (reranker tuning, search weight adjustments)
- Portal UI changes
- Connector changes that do not alter chunk content

---

## Pre-flight checklist

Before triggering the rebuild:

1. **Confirm the SPEC migrations have been applied.** The rebuild writes to
   `knowledge.parent_chunks` — this table is created by migration `015_parent_chunks.sql`.
   Check on core-01:
   ```bash
   ssh core-01 "docker exec klai-core-postgres-1 sh -c \
     'psql -U \$POSTGRES_USER -d \$POSTGRES_DB -c \
     \"SELECT to_regclass(\\\"public.knowledge.parent_chunks\\\");\"'"
   ```
   Expected: non-null result. If NULL, the migration has not run — do not proceed.

2. **Confirm knowledge-ingest is on the expected image.**
   ```bash
   ssh core-01 "docker exec klai-core-knowledge-ingest-1 python -c \
     'from knowledge_ingest.rebuild_tasks import rebuild_kb_inline; print(\"OK\")"'
   ```
   Expected output: `OK`. If ImportError, the container is running an old image.

3. **Check the LLM queue backlog.**
   A pre-existing `enrich-bulk` backlog will contend with the rebuild LLM calls.
   Check how many jobs are pending:
   ```bash
   ssh core-01 "docker exec klai-core-postgres-1 sh -c \
     'psql -U \$POSTGRES_USER -d \$POSTGRES_DB -c \
     \"SELECT queue, status, count(*) FROM procrastinate_jobs GROUP BY 1,2 ORDER BY 1,2;\"'"
   ```

4. **Estimate cost before proceeding.** See the cost section below.

---

## How to trigger

### Option A — inline (recommended for operators, visible progress)

```bash
ssh core-01 "docker exec klai-core-knowledge-ingest-1 \
  python -c 'import asyncio; \
             from knowledge_ingest.rebuild_tasks import rebuild_kb_inline; \
             result = asyncio.run(rebuild_kb_inline(\"<org_zitadel_id>\", \"<kb_slug>\")); \
             print(result)'"
```

Replace `<org_zitadel_id>` with the Zitadel organisation ID (found in portal-api logs or the `portal_orgs` table) and `<kb_slug>` with the KB slug (e.g. `voys-support`).

The function runs synchronously and prints a result dict on completion:

```json
{
  "org_id": "...",
  "kb_slug": "...",
  "artifacts_processed": 42,
  "artifacts_skipped": 3,
  "artifacts_failed": 0,
  "duration_ms": 180000
}
```

### Option B — via Procrastinate queue

Enqueues the task on the `rebuild-kb` queue; the worker picks it up within seconds.

```bash
ssh core-01 "docker exec klai-core-knowledge-ingest-1 \
  python -c 'import asyncio, procrastinate, procrastinate.contrib.django; \
             from knowledge_ingest.enrichment_tasks import get_app; \
             app = get_app(); \
             asyncio.run( \
               app.rebuild_kb.configure( \
                 queueing_lock=\"rebuild-kb-<org_id>-<kb_slug>\" \
               ).defer_async(org_id=\"<org_id>\", kb_slug=\"<kb_slug>\") \
             )'"
```

**Note:** If a rebuild is already queued or running for this KB, `AlreadyEnqueued` is raised — do not bypass, wait for the running job to finish.

---

## How to monitor

### VictoriaLogs queries

```
# Overall progress
service:knowledge-ingest AND event:rebuild_kb_*

# Single artifact outcomes
service:knowledge-ingest AND event:rebuild_artifact_processed AND kb_slug:<slug>
service:knowledge-ingest AND event:rebuild_skip_no_text AND kb_slug:<slug>
service:knowledge-ingest AND event:rebuild_artifact_failed AND kb_slug:<slug>

# Completion summary
service:knowledge-ingest AND event:rebuild_kb_completed AND kb_slug:<slug>
```

### Expected log sequence

1. `rebuild_kb_started` — task begins
2. `rebuild_kb_artifacts_found` — total artifact count
3. Per artifact: `rebuild_artifact_processed` / `rebuild_skip_no_text` / `rebuild_artifact_failed`
4. `rebuild_kb_completed` — summary with counts and duration

### Procrastinate job status (Option B only)

```bash
ssh core-01 "docker exec klai-core-postgres-1 sh -c \
  'psql -U \$POSTGRES_USER -d \$POSTGRES_DB -c \
  \"SELECT id, status, queueing_lock, attempts, errors \
    FROM procrastinate_jobs WHERE queue = \\\"rebuild-kb\\\" ORDER BY id DESC LIMIT 5;\"'"
```

---

## Cost estimate

Per artifact cost breakdown (approximate):

| Step | Model | Cost/artifact |
|------|-------|--------------|
| document_summary (SPEC-RAG-CONTEXTUAL-001) | `klai-medium` | ~€0.0002 |
| chunk enrichment (context prefix + HyPE questions) | `klai-fast` | ~€0.0005 |
| **Total** | | **~€0.0007/artifact** |

For Voys's 501 active artifacts: **~€0.35 per full rebuild**.

Costs are approximate and depend on chunk count per document and the LiteLLM proxy cache hit rate. Repeated rebuilds with unchanged content benefit from cache hits on document_summary generation.

---

## Skipped artifacts

Artifacts without `extra.document_text` are skipped silently with a `rebuild_skip_no_text` log event. This is expected behaviour in v1.

**Why this happens:** The default ingest pipeline does not currently write `document_text` to `knowledge.artifacts.extra`. See the OPEN QUESTION in `knowledge_ingest/rebuild_tasks.py` docstring. A follow-up SPEC will address this by either:
- storing `document_text` in `extra` during initial ingest, or
- adding per-connector re-fetch adapters.

Until that follow-up ships, only artifacts where a connector explicitly wrote `document_text` into `extra` (e.g. direct KB uploads via the portal) will be rebuilt.

---

## Failure handling

- **Fail-open per artifact:** One failed artifact does not abort the rebuild. The error is logged as `rebuild_artifact_failed` and counted in `artifacts_failed`.
- **Re-running is safe:** The rebuild is fully idempotent. Trigger it again after fixing the root cause of failures.
- **Failures-only re-run:** Not supported in v1 — a full KB re-run is always performed. Track in follow-up SPEC.

### Common failure causes

| Symptom | Cause | Fix |
|---------|-------|-----|
| `rebuild_artifact_failed` with `EnrichmentError` | LiteLLM rate limit or model unavailable | Wait, re-run |
| `rebuild_artifact_failed` with `UndefinedColumnError` | `knowledge.parent_chunks` table missing | Apply migration 015, re-run |
| All artifacts skipped | `document_text` not stored on `extra` | See "Skipped artifacts" above |
| `AlreadyEnqueued` | Rebuild already queued for this KB | Wait for running job, re-run after |

---

## Rollback

The rebuild is non-destructive in the sense that Qdrant chunks are replaced atomically per-path (delete-then-insert), so rolling back is another rebuild with the previous code.

If the pipeline version that introduced an issue needs to be rolled back:

1. Roll back the knowledge-ingest image to the previous tag (see `version-management.md`).
2. Re-run `rebuild_kb` against the rolled-back image.

---

## Related

- `docs/runbooks/rag-quality.md` — general RAG quality debugging
- SPEC-RAG-CONTEXTUAL-001 — document_summary enrichment
- SPEC-RAG-PARENT-CHILD-001 — parent-child chunking
- `knowledge_ingest/rebuild_tasks.py` — source code with full design notes
