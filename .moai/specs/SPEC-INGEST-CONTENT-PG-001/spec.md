---
id: SPEC-INGEST-CONTENT-PG-001
version: "0.1.0"
status: draft
created: 2026-05-06
updated: 2026-05-06
author: Mark Vletter
priority: high
related:
  - audit-2026-05-06 finding 1 (parent)
  - SPEC-INGEST-UNIQUE-ARTIFACT-001 (companion: closes the active-row race)
  - SPEC-RAG-REBUILD-KB-001 (precedent: re-derive chunks from PG body)
---

# SPEC-INGEST-CONTENT-PG-001: Pass `artifact_id` only to enrichment task

## Summary

The Procrastinate enrichment task currently freezes the document body and
all derived metadata into its job arguments at `defer_async` time. A
second direct-POST that arrives within the (typically seconds-long)
worker pickup window writes new raw vectors to Qdrant — but the worker
still processes the older content from its frozen args, overwriting the
new vectors with stale enrichment.

This SPEC moves the canonical state to PostgreSQL: the enrichment task
takes only `artifact_id`, and the worker reads `extra_payload` (including
`document_text`) from `knowledge.artifacts.extra` at execution time.

This is the same pattern that the Gitea-webhook flow already uses
correctly (`ingest_from_gitea` re-fetches from Gitea per-execution) and
matches LlamaIndex's docstore + worker model.

## Motivation

Audit 2026-05-06 finding 1 (independently verified):

- `defer_async(document_text=req.content, ...)` in `routes/ingest.py`
  serialises the body into the job row.
- `_enrich_document` reads `document_text` directly from its parameter,
  never re-reads from `pg_store`.
- The race window is the period between Phase-1 completion and worker
  pickup of the enrichment task — typically seconds, longer under
  enrich-bulk queue saturation.

Affected callers (direct-POST flow):
- `klai-knowledge-mcp` `save_personal_knowledge`
- `klai-connector` `sync_engine` (per-document POST)
- `klai-portal` `partner_knowledge`, `knowledge_ingest_client`
- `klai-scribe` `knowledge_adapter`

## Scope

### In scope

1. **New `pg_store.read_artifact_for_enrichment(artifact_id)`** — returns
   the artifact row + parsed `extra` JSONB, or `None` if the artifact has
   been deleted between enqueue and dequeue (connector-purge race).

2. **`enrichment_tasks._load_and_enrich(artifact_id)`** — single shim
   that:
   - Reads the artifact via `read_artifact_for_enrichment`.
   - Soft-skips if the artifact is missing or has no `document_text`
     on `extra` (legacy rows go through `rebuild_kb`).
   - Re-derives `chunks` + `parents` from the *current*
     `extra.document_text` via `chunker.chunk_markdown_with_parents`.
   - Delegates to the unchanged `_enrich_document` body with the
     PG-sourced kwargs.

3. **Task signature simplification** — both `enrich_document_interactive`
   and `enrich_document_bulk` accept only `artifact_id: str`. All other
   parameters disappear from the Procrastinate job row.

4. **`routes/ingest.py` writes `extra_payload` to PG** before deferring
   — `pg_store.update_artifact_extra(artifact_id, extra_payload)` after
   the existing `extra_payload` build, then `defer_async(artifact_id=...)`.
   JSONB merge semantics so the existing `pg_extra` from `create_artifact`
   stays intact.

5. **Test coverage**:
   - `read_artifact_for_enrichment`: missing id, missing artifact, dict
     extra, JSON-string extra.
   - `_load_and_enrich`: soft-skip on missing artifact, soft-skip on
     missing `document_text`, full-state delegation to `_enrich_document`.
   - Signature contract: `_load_and_enrich(artifact_id)` is one-arg;
     anything else is a regression.
   - Updated existing tests to mock `update_artifact_extra` and disable
     the graphiti enqueue branch.

### Out of scope

- Backwards compatibility for in-flight tasks: the system has no
  production load yet, re-ingest of existing data is the recovery path
  if any test data needs catch-up.
- Schema migration for `knowledge.artifacts.extra` — no new columns
  needed; existing JSONB shape is already a superset.
- Refactoring `_enrich_document`'s body — it still accepts the same
  kwargs; only the *source* of those kwargs moves from task-args to PG.
- klai-retrieval-api `embed_single`/`embed_batch` (own retry-budget
  finding, separate scope).

## Acceptance criteria

1. `tests/test_enrichment_loads_from_pg.py`: 8 tests pass covering
   `read_artifact_for_enrichment` + `_load_and_enrich` contracts.
2. Existing regression tests pass after mock additions:
   - `test_ingest_content_hash_dedup.py`
   - `test_ingest_enrichment_dedup.py`
   - `test_enrichment_dedup.py`
3. ruff check + ruff format --check clean on all modified files.
4. `_load_and_enrich` signature has exactly one parameter
   (`artifact_id: str`).
5. End-to-end smoke (manual, post-deploy): two rapid POSTs to
   `/ingest/v1/document` for the same path with different content;
   eventual Qdrant payload reflects the second content's enrichment,
   not the first's.

## Risks

| Risk | Mitigation |
|---|---|
| Worker re-chunks with different boundaries than Phase-1, leading to mismatched raw vs. enriched chunk counts | Phase-1 and Phase-2 use the same `chunker.chunk_markdown_with_parents` defaults. Both Phase-1 and Phase-2 do delete-then-insert keyed on `(org_id, kb_slug, path)`, so chunk-count drift is self-healing within a single ingest cycle. |
| Procrastinate retries replay an old job-row whose `artifact_id` no longer exists (connector-purge in flight) | `_load_and_enrich` returns silently when `read_artifact_for_enrichment` returns `None` — same soft-skip pattern as `ingest_graphiti_episode` already uses. |
| Legacy artifact rows without `document_text` on `extra` cannot be enriched via this path | Logged as `enrichment_aborted_no_document_text`. Operator runs `rebuild_kb` for those KBs (existing tool, SPEC-RAG-REBUILD-KB-001). |
| Companion SPEC-INGEST-UNIQUE-ARTIFACT-001 must land before this PR is exercised heavily | Both can land independently; their interaction is benign (the UNIQUE constraint catches concurrent inserts; this SPEC catches stale content). |

## References

- `docs/audit-ingest-pipeline-2026-05-06/findings.md` § Finding 1
- `docs/audit-ingest-pipeline-2026-05-06/research/finding-1.md`
  (LlamaIndex docstore pattern, Procrastinate queueing_lock semantics,
  Inngest debounce-and-re-fetch)
- `klai-knowledge-ingest/knowledge_ingest/ingest_tasks.py::ingest_from_gitea`
  — the Gitea-flow precedent that already implements this correctly
- `klai-knowledge-ingest/knowledge_ingest/rebuild_tasks.py` — the
  re-derive-from-PG-body pattern that this SPEC adopts for the
  enrichment worker
