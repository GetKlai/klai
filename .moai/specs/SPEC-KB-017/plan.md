# Implementation Plan: SPEC-KB-017

## Title
Recursive Delete for Knowledge Pages (Qdrant + Knowledge Graph)

## GitHub Issue
https://github.com/GetKlai/klai/issues/80

## Problem Statement
When a knowledge page is deleted via the klai-docs UI, only the Gitea file, Qdrant chunks, and PostgreSQL artifact are cleaned up. Graphiti knowledge graph episodes/entities and related metadata (derivations, artifact_entities, embedding_queue) remain orphaned.

## SPEC Candidates

### SPEC-KB-017: Recursive Page Delete
**Domain:** KB (Knowledge Base)
**Priority:** P2 (data hygiene, retrieval quality impact)
**Complexity:** Low-medium (3 files to modify, existing patterns to reuse)

## EARS Structure

### Ubiquitous Requirements
- UR-1: The system SHALL delete all associated data when a knowledge page is removed
- UR-2: Graphiti graph cleanup SHALL respect the `graphiti_enabled` setting

### Event-Driven Requirements
- ED-1: WHEN a page is deleted via Gitea webhook, THEN the system SHALL fetch episode IDs from PostgreSQL and delete corresponding Graphiti Episodic nodes
- ED-2: WHEN Graphiti episodes are deleted, THEN the system SHALL remove orphaned Entity nodes not connected to any remaining Episodic node
- ED-3: WHEN a page is deleted, THEN the system SHALL hard-delete derivations, artifact_entities, and embedding_queue records for that page's artifacts

### Unwanted Behavior Requirements
- UB-1: IF Graphiti is disabled or unavailable, THEN page deletion SHALL still complete successfully (graph cleanup skipped with warning log)
- UB-2: IF episode IDs are missing from artifact extra field, THEN graph cleanup SHALL be skipped for that artifact without error

### State-Driven Requirements
- SD-1: WHILE a page is being deleted, IF the deletion partially fails, THEN the webhook handler SHALL log the failure and continue with remaining cleanup steps

## Technical Approach

### Files to Modify

1. **`klai-knowledge-ingest/knowledge_ingest/pg_store.py`**
   - Add `get_page_episode_ids(org_id, kb_slug, path) -> list[str]`
   - Add `cleanup_page_metadata(org_id, kb_slug, path) -> None`

2. **`klai-knowledge-ingest/knowledge_ingest/graph.py`**
   - Rename `delete_kb_episodes()` to `delete_episodes()` (already generic)
   - OR: reuse existing function as-is (it accepts any episode_ids list)

3. **`klai-knowledge-ingest/knowledge_ingest/routes/ingest.py`**
   - Modify webhook handler (lines 476-483) to add graph + metadata cleanup

### Implementation Details

#### pg_store.get_page_episode_ids()
```sql
SELECT extra::jsonb->>'graphiti_episode_id' AS episode_id
FROM knowledge.artifacts
WHERE org_id = $1 AND kb_slug = $2 AND path = $3
  AND extra IS NOT NULL
  AND extra::jsonb->>'graphiti_episode_id' IS NOT NULL
  AND extra::jsonb->>'graphiti_episode_id' != 'no-chunks'
```

#### pg_store.cleanup_page_metadata()
Within a transaction:
1. Get artifact IDs: `SELECT id FROM knowledge.artifacts WHERE org_id=$1 AND kb_slug=$2 AND path=$3`
2. Delete embedding_queue: `DELETE FROM knowledge.embedding_queue WHERE artifact_id = ANY($1)`
3. Delete artifact_entities: `DELETE FROM knowledge.artifact_entities WHERE artifact_id = ANY($1)`
4. Delete derivations: `DELETE FROM knowledge.derivations WHERE child_id = ANY($1) OR parent_id = ANY($1)`
5. Nullify superseded_by: `UPDATE knowledge.artifacts SET superseded_by = NULL WHERE superseded_by = ANY($1)`

#### Webhook handler update
```python
for path in removed:
    try:
        # Existing: Qdrant + PG soft-delete
        await qdrant_store.delete_document(org_id, kb_slug, path)

        # NEW: Graphiti cleanup (before PG soft-delete to read episode IDs)
        episode_ids = await pg_store.get_page_episode_ids(org_id, kb_slug, path)
        if episode_ids:
            await graph_module.delete_kb_episodes(org_id, episode_ids)

        # NEW: Metadata cleanup (derivations, artifact_entities, embedding_queue)
        await pg_store.cleanup_page_metadata(org_id, kb_slug, path)

        # Existing: soft-delete artifact (last, after all references cleaned)
        await pg_store.soft_delete_artifact(org_id, kb_slug, path)
        deleted += 1
    except Exception as exc:
        logger.warning("delete_failed", path=path, error=str(exc))
```

### Execution Order (critical)
1. Qdrant chunk deletion (independent)
2. Fetch episode IDs from PG (must happen BEFORE soft-delete)
3. Graphiti episode deletion (uses episode IDs)
4. PG metadata cleanup (derivations, entities, queue)
5. PG artifact soft-delete (LAST - marks artifact as deleted)

### Risk Analysis

| Risk | Severity | Mitigation |
|------|----------|------------|
| Graphiti service down | Medium | Wrap in try/except, log warning, continue |
| Missing episode IDs | Low | Filter nulls and 'no-chunks' sentinel |
| Partial failure | Medium | Each step independent; log and continue |
| FK violations | Low | Cleanup metadata before soft-delete |

### Out of Scope
- Personal item deletion (same gap, separate SPEC if needed)
- Bulk page deletion UI
- Undo/restore functionality
- Migration to clean existing orphaned data

---

## Implementation Notes

**Status:** Completed
**Commits:** `cab1313`, `cc391e9`
**Deployed:** 2026-04-02, knowledge-ingest on core-01

### What was implemented (as planned)

1. **`pg_store.get_page_episode_ids()`** — path-scoped variant of `get_episode_ids()`
2. **`pg_store.cleanup_page_metadata()`** — transactional cleanup of embedding_queue, artifact_entities, derivations, superseded_by
3. **Webhook handler** — extended with full recursive delete pipeline

### Deviation from plan

- **SD-1 (partial failure handling):** Plan showed a single try/except around all steps. Implementation uses **isolated try/except per step** with specific log event names (`page_qdrant_delete_failed`, `page_graph_cleanup_failed`, `page_metadata_cleanup_failed`, `page_soft_delete_failed`). This is strictly better: partial failures don't block subsequent steps, and each failure is independently identifiable in LogsQL.
- **`no-chunks` filtering:** Plan included the filter in the SQL WHERE clause. Implementation filters in Python (post-fetch) to match the existing `get_episode_ids()` pattern.
- **`graph.py` unchanged:** Reused `delete_kb_episodes()` as-is (already generic). No rename needed.

### EARS Requirement Coverage

| Requirement | Status | Notes |
|---|---|---|
| UR-1 | Implemented | All associated data cleaned up |
| UR-2 | Implemented | `settings.graphiti_enabled` checked |
| ED-1 | Implemented | Episode IDs fetched, Graphiti nodes deleted |
| ED-2 | Implemented | Orphan Entity cleanup via existing Cypher query |
| ED-3 | Implemented | Hard-delete in transactional `cleanup_page_metadata()` |
| UB-1 | Implemented | Graphiti failure non-blocking with warning log |
| UB-2 | Implemented | `no-chunks` sentinel and null filtered |
| SD-1 | Improved | Each step isolated — exceeds spec requirement |
