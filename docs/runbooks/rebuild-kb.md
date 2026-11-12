# Runbook: Operator-Triggered KB Rebuild (`rebuild_kb`)

> Commands are given as they run on core-01, from the deploy directory (the one holding `docker-compose.yml`). See [How public runbooks write commands](../contributing/infrastructure-boundary.md#how-public-runbooks-write-commands).

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
- A changed KB setting that derives from stored text (see below)

---

## Before a rebuild: is a cheap-layer patch enough?

A rebuild redoes the model work of every document: the document summary, the
context sentence and the HyPE questions, tens of thousands of LLM calls for a
large knowledge base. Most setting changes do not touch that work. The alias
groups and product rules of a knowledge base only feed the cheap layer of a
chunk (the "Also known as" line, the `product` label, the `text_fingerprint`,
and the dense and sparse vectors of the embedded text), which
`knowledge_ingest/scripts/kb_patch.py` recomputes with zero model calls,
re-embedding only the chunks whose embedded text changed. Use that, via
[kb-patch.md](kb-patch.md), whenever the change is to `aliases` or
`product_rules`. A rebuild is for a change to how chunks are cut or how the
model work is done, as in the table above.

---

## Approval before a run on production

A rebuild on production follows the order in `AGENTS.md` ("production knowledge
data is rewritten only with approval per run"): dry-run counts first, the
owner's yes for that one run, one knowledge base as a trial, then the rest. The
code has no dry run of its own (no flag, no preview), so the counts come from
the active artifacts of the knowledge base, which is the set the rebuild
iterates (`_list_active_artifacts` in `knowledge_ingest/rebuild_tasks.py`):

```sql
SELECT count(*) FROM knowledge.artifacts
 WHERE org_id = '<org_zitadel_id>' AND kb_slug = '<kb_slug>'
   AND belief_time_end = 253402300800;
```

Each of those artifacts costs one summary call plus one model call per chunk,
all on the `klai-ingest` alias.

---

## Pre-flight checklist

Before triggering the rebuild:

1. **Confirm the SPEC migrations have been applied.** The rebuild writes to
   `knowledge.parent_chunks` — this table is created by migration `015_parent_chunks.sql`.
   Check on core-01:
   ```bash
   docker exec klai-core-postgres-1 sh -c \
     'psql -U $POSTGRES_USER -d $POSTGRES_DB -c \
     "SELECT to_regclass('\''knowledge.parent_chunks'\'');"'
   ```
   Expected: non-null result. If NULL, the migration has not run — do not proceed.

2. **Confirm knowledge-ingest is on the expected image.**
   ```bash
   docker exec klai-core-knowledge-ingest-1 python -c \
     'from knowledge_ingest.rebuild_tasks import rebuild_kb_inline; print(rebuild_kb_inline.__name__)'
   ```
   Expected output: `rebuild_kb_inline`. If ImportError, the container is running an old image.

3. **Check the LLM queue backlog.**
   A pre-existing `enrich-bulk` backlog will contend with the rebuild LLM calls.
   Check how many jobs are pending:
   ```bash
   docker exec klai-core-postgres-1 sh -c \
     'psql -U $POSTGRES_USER -d $POSTGRES_DB -c \
     "SELECT queue, status, count(*) FROM procrastinate_jobs GROUP BY 1,2 ORDER BY 1,2;"'
   ```

4. **Look at the cost.** See the cost section below.

---

## How to trigger

### Inline (visible progress)

```bash
docker exec klai-core-knowledge-ingest-1 \
  python -c 'import asyncio; from knowledge_ingest.rebuild_tasks import rebuild_kb_inline; result = asyncio.run(rebuild_kb_inline("<org_zitadel_id>", "<kb_slug>")); print(result)'
```

Replace `<org_zitadel_id>` with the Zitadel organisation ID (found in portal-api logs or the `portal_orgs` table) and `<kb_slug>` with the KB slug.

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

### Queue task

`rebuild_kb` is also registered as a Procrastinate task on the `rebuild-kb`
queue, but no route or script defers it, and `get_app()` raises in a fresh
`docker exec` process, because the app exists only inside the running service.
The inline function above is the way to run a rebuild. The task and the inline
function share one core, and neither sets a `queueing_lock` (the task has
`max_attempts=1`), so two runs for one knowledge base can overlap: start a
second one only after the first has printed its result.

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
3. Per artifact: `rebuild_artifact_processed` / `rebuild_skip_no_text` / `rebuild_skip_no_chunks` / `rebuild_artifact_failed`; `rebuild_text_reconstructed_from_qdrant` marks an artifact whose text came from Qdrant
4. `rebuild_kb_completed` — summary with counts and duration

---

## Cost

Not measured here. Every call of a rebuild goes to the `klai-ingest` alias: one
document summary per artifact and one enrichment call per chunk (the model
behind the alias is in `deploy/litellm/config.yaml`). Read the spend per alias
in the LiteLLM spend logs before and after a trial knowledge base and
extrapolate from that. The calls share the ingest limiter of 0.6 requests per
second with all other enrichment, graph and taxonomy work.

---

## Where the text comes from

`document_text` is written into `knowledge.artifacts.extra` at every ingest
(`knowledge_ingest/routes/ingest.py`), so a rebuild reads it from there. An
artifact without it (an older row) is rebuilt from the text of its existing
Qdrant chunks, concatenated in chunk order; that text lacks the frontmatter
(`rebuild_text_reconstructed_from_qdrant`). Only an artifact with neither source
is skipped, with `rebuild_skip_no_text`. For an upload converted by Docling,
`document_text` is the stored preview of the document, at most 450,000
characters (see [kb-file-upload.md](kb-file-upload.md)).

---

## Failure handling

- **Fail-open per artifact:** One failed artifact does not abort the rebuild. The error is logged as `rebuild_artifact_failed` and counted in `artifacts_failed`.
- **Re-running replaces again:** each run writes the new points of every document and then deletes the previous ones. Trigger it again after fixing the root cause of failures.
- **Failures-only re-run:** not supported; a run always covers every active artifact of the KB.

### Common failure causes

| Symptom | Cause | Fix |
|---------|-------|-----|
| `rebuild_artifact_failed` with `EnrichmentError` | LiteLLM rate limit or model unavailable | Wait, re-run |
| `rebuild_artifact_failed` with `UndefinedTableError` | `knowledge.parent_chunks` table missing | Apply migration 015, re-run |
| `rebuild_artifact_failed` with a connection or HTTP error from the embedding service or Qdrant | that service failed for the artifact; the error is raised, not swallowed | Check the services, re-run |
| All artifacts skipped | neither `extra.document_text` nor Qdrant chunks | See "Where the text comes from" above |

---

## Rollback

A document's new points are written before its previous points are deleted, so a rebuild that stops halfway leaves every document searchable. Rolling back is another rebuild with the previous code.

If the pipeline version that introduced an issue needs to be rolled back:

1. Roll back the knowledge-ingest image to the previous tag (see `version-management.md`).
2. Re-run `rebuild_kb` against the rolled-back image.

---

## Related

- `docs/runbooks/rag-quality.md` — general RAG quality debugging
- SPEC-RAG-CONTEXTUAL-001 — document_summary enrichment
- SPEC-RAG-PARENT-CHILD-001 — parent-child chunking
- `knowledge_ingest/rebuild_tasks.py` — source code with full design notes
