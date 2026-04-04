# Research: Recursive Delete for Knowledge Pages

## Issue

GitHub Issue #80: When deleting a knowledge page, Qdrant vector chunks and Graphiti knowledge graph nodes/edges are NOT cleaned up, causing orphaned data.

## Current Deletion Flow

### Page Deletion (INCOMPLETE)

```
User deletes page in klai-docs UI
  -> DELETE /api/orgs/{org}/kbs/{kb}/pages/{...path}
  -> gitea.deleteFile() + sidebar update
  -> Gitea webhook -> POST /ingest/v1/webhook/gitea
  -> qdrant_store.delete_document(org_id, kb_slug, path)  [OK]
  -> pg_store.soft_delete_artifact(org_id, kb_slug, path)  [OK]
  -> END (Graphiti NOT cleaned, derivations NOT cleaned)   [MISSING]
```

### KB Deletion (COMPLETE - reference implementation)

```
DELETE /api/admin/knowledge-bases/{kb_id}
  -> knowledge_ingest_client.delete_kb(org_id, kb_slug)
  -> pg_store.get_episode_ids(org_id, kb_slug)
  -> graph_module.delete_kb_episodes(org_id, episode_ids)  [OK]
  -> qdrant_store.delete_kb(org_id, kb_slug)               [OK]
  -> pg_store.delete_kb(org_id, kb_slug)                   [OK - hard delete]
```

## What Gets Orphaned on Page Delete

| Component | Current Status | Risk |
|-----------|---------------|------|
| Qdrant chunks | DELETED (filter by org_id, kb_slug, path) | None |
| PostgreSQL artifact | SOFT-DELETED (belief_time_end = now) | None |
| Graphiti Episodic nodes | **ORPHANED** | Pollutes graph queries |
| Graphiti Entity nodes | **ORPHANED** (if no other episodes reference them) | Inflates graph |
| knowledge.derivations | **NOT CLEANED** | FK references to soft-deleted artifacts |
| knowledge.artifact_entities | **NOT CLEANED** | Junction records for dead artifacts |
| knowledge.embedding_queue | **NOT CLEANED** | Low risk (transient) |

## Key Files

### Webhook Handler (page deletion entry point)
- `klai-knowledge-ingest/knowledge_ingest/routes/ingest.py:476-483`

### Qdrant Operations
- `klai-knowledge-ingest/knowledge_ingest/qdrant_store.py:239-250` (delete_document)
- Collection: `klai_knowledge`, filter by (org_id, kb_slug, path)

### Graphiti Operations
- `klai-knowledge-ingest/knowledge_ingest/graph.py:174-195` (delete_kb_episodes)
- Episode ID stored in: `knowledge.artifacts.extra->>'graphiti_episode_id'`
- Cypher: `MATCH (e:Episodic) WHERE e.uuid IN $uuids DETACH DELETE e`
- Orphan cleanup: `MATCH (n:Entity) WHERE NOT ((:Episodic)--(n)) DETACH DELETE n`

### PostgreSQL Store
- `klai-knowledge-ingest/knowledge_ingest/pg_store.py:130-142` (soft_delete_artifact)
- `klai-knowledge-ingest/knowledge_ingest/pg_store.py:145-161` (get_episode_ids)
- `klai-knowledge-ingest/knowledge_ingest/pg_store.py:164-220` (delete_kb - reference)

## Existing Patterns to Reuse

1. **Episode ID lookup**: `pg_store.get_episode_ids()` - filter by (org_id, kb_slug), needs path filter variant
2. **Graph deletion**: `graph_module.delete_kb_episodes()` - works with any episode_ids list
3. **Qdrant deletion**: `qdrant_store.delete_document()` - already works for page level
4. **Soft delete**: `pg_store.soft_delete_artifact()` - already works for page level

## Implementation Approach

### New Functions Needed

1. `pg_store.get_page_episode_ids(org_id, kb_slug, path) -> list[str]`
   - Like `get_episode_ids()` but filtered by path

2. `graph_module.delete_page_episodes(org_id, episode_ids) -> None`
   - Reuse `delete_kb_episodes()` (already generic enough)

3. `pg_store.cleanup_page_metadata(org_id, kb_slug, path) -> None`
   - Delete derivations, artifact_entities, embedding_queue for page artifacts

### Webhook Handler Changes

In `ingest.py` webhook handler, after existing delete calls:
```
episode_ids = await pg_store.get_page_episode_ids(org_id, kb_slug, path)
if episode_ids:
    await graph_module.delete_kb_episodes(org_id, episode_ids)
await pg_store.cleanup_page_metadata(org_id, kb_slug, path)
```

### Constraints

- `settings.graphiti_enabled` must be checked before graph operations
- Episode IDs may be missing (no-chunks sentinel, null extra field)
- Graph deletion should be non-blocking (log errors, don't fail webhook)
- Personal item deletion has same gap (lower priority)
