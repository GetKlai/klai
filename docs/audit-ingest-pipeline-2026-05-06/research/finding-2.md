# Finding 2 research: stale metadata in Phase 2

## Code verification

### Fields refreshed at Phase 2 write-time (enrichment_tasks.py)

Only one field is re-read from PostgreSQL at Phase 2 write-time:

| Field | Source at Phase 2 | Line |
|---|---|---|
| `visibility` | `await kb_config.get_kb_visibility(org_id, kb_slug, pool)` | enrichment_tasks.py:390 |

Comment at that line (enrichment_tasks.py:387-388):
```
# Refresh visibility from kb_config at write time — catches any visibility
# change that happened while this task was queued or running.
```

### Fields frozen from Phase 1 (ingest.py, consumed by enrichment_tasks.py via extra_payload)

All other mutable metadata fields are snapshotted at Phase 1 request-time (ingest.py lines 463-509) and serialised into `extra_payload` before the Procrastinate job is deferred (ingest.py line 556). They are never re-read from PostgreSQL during Phase 2.

| Field | Phase 1 origin | ingest.py line(s) |
|---|---|---|
| `taxonomy_node_ids` | taxonomy classifier output or centroid-based assignment | 495 |
| `tags` | merged frontmatter tags + LLM-suggested tags | 496-497 |
| `content_label` | `generate_content_label()` LLM call | 499 |
| `kb_name` | `req.kb_name` (caller-supplied) | 502-503 |
| `connector_type` | `req.connector_type` (caller-supplied) | 504-505 |
| `source_domain` | `req.source_domain` (caller-supplied) | 506-507 |
| frontmatter metadata | parsed from document body | 491-492 |
| `source_label` | computed from req fields | 501 |
| `source_type`, `source_ref`, `source_connector_id` | from req fields | 474-478 |
| `belief_time_start`, `belief_time_end` | from knowledge_flavor config | 487-488 |

At Phase 2, `enrichment_tasks._enrich_document()` only reads these fields *out of* extra_payload (e.g., lines 300-310 for `kb_name`, `connector_type`, `source_domain`) — it does not re-fetch them from PostgreSQL or portal-api.

### Qdrant upsert_enriched_chunks — how extra_payload lands

`qdrant_store.upsert_enriched_chunks()` (lines 244-245) merges `extra_payload` into `base_payload` via `base_payload.update(extra_payload)`, which means every field in the Phase 1 snapshot is written verbatim into Qdrant point payloads without any freshness check.

### Retrieval-time drift handling

There is **no retrieval-time logic** that compensates for drift between Qdrant payload and PostgreSQL state for any of the affected fields.

- `taxonomy_node_ids`: retrieval-api's `_search_hybrid()` (search.py:241-252) filters Qdrant using `request.taxonomy_node_ids` — a caller-supplied filter matched against the Qdrant payload value. If the stored payload has stale taxonomy IDs, documents land in wrong or missing taxonomy buckets at search time. No PG join is performed.
- `tags`: same pattern — Qdrant `MatchAny` filter against the payload's `tags` field (search.py:255-261). No live PG read.
- `visibility`: is live-checked because it is re-read at Phase 2 write-time (the only exception). Additionally, the portal sends `PATCH /ingest/v1/kb/visibility` on every KB visibility change, triggering an immediate Qdrant payload update — so visibility has two freshness mechanisms.
- `kb_name`, `content_label`, `source_domain`, `connector_type`: display-only in retrieval results. Not used as Qdrant filters. Drift is cosmetic.

### Design documentation

`docs/architecture/knowledge-ingest-flow.md` lines 812-815 explicitly documents the visibility refresh mechanism:

> "The portal writes KB visibility to knowledge-ingest (PATCH /ingest/v1/kb/visibility) on KB create and on every visibility change. ingest_document() reads the KB's current visibility from kb_config and attaches it to every chunk at ingest time, so the Qdrant filter is always effective."

No equivalent documentation exists for taxonomy, tags, or content_label refresh. There is no comment or design doc that explains the snapshot choice for these fields — the visibility-only refresh appears to be a deliberate partial fix made when visibility-gating was introduced (the comment at enrichment_tasks.py:387-388 suggests awareness of the async gap), without extending the same treatment to other fields.

The SPEC-KB-022 and SPEC-KB-023 comments in ingest.py (lines 498-499, 525) focus on ensuring these fields survive the Phase 1→Phase 2 passthrough via extra_payload, not on keeping them fresh relative to PG state.

---

## Current behavior

**Phase 1** (synchronous HTTP handler, ingest.py):

1. Reads KB visibility from `kb_config` (PG, with TTL cache).
2. Calls taxonomy classifier and LLM content-labeler — produces `taxonomy_node_ids`, `content_label`, `tags`.
3. Merges all fields into `extra_payload` dict.
4. Upserts raw chunks to Qdrant (Phase 1 Qdrant write — these points are later deleted by Phase 2).
5. Serialises `extra_payload` into a Procrastinate job and returns HTTP 200 to caller.

**Phase 2** (async Procrastinate worker, enrichment_tasks.py):

1. Picks up the job from the queue (latency: seconds to minutes, potentially longer under queue pressure).
2. Calls LLM enrichment, embeds enriched text and hypothetical questions.
3. Refreshes only `visibility` from PG (line 390).
4. Deletes the Phase 1 Qdrant points for this path.
5. Upserts the Phase 2 enriched points with `extra_payload` as-is (minus the refreshed visibility).

**Result**: If a user renames a KB, reassigns taxonomy nodes, or changes tags between Phase 1 enqueue and Phase 2 write (a window that can be seconds to minutes in normal operation, or hours when the worker is backlogged), the enriched Qdrant points will carry Phase 1 snapshot values. This drift persists until the document is re-ingested.

---

## Industry standard (2026)

### Snapshot-at-ingest vs live-lookup-at-retrieval — the two-axis decision

The industry has converged on a two-axis framework for deciding which metadata to snapshot and which to look up live:

**Axis 1 — How often does the metadata change?**
- Infrequent (days/weeks): visibility flags, ACLs, taxonomy structures → safe to snapshot with a background propagation job.
- Frequent (minutes/hours): user-specific permissions, real-time tags → must be live-lookup or event-driven invalidation.

**Axis 2 — What is the cost of serving stale metadata?**
- Security / access control: stale = data leak or overcorrection. Industry rule: **ACL metadata must never be served stale.** Sources: Databricks Mosaic AI Vector Search (2024), Applied AI enterprise RAG architecture guidance.
- Filtering / taxonomy: stale = retrieval misses or overcounts. Correctness matters for precision but not for security.
- Display-only (kb_name, source_domain): stale = cosmetic inconsistency. Low cost.

### Named system patterns

**Pinecone** (2024-2025): Explicit metadata-only `update` API without re-embedding. Recommended for ACL and permission changes: `index.update(id=..., set_metadata={"acl": [...], "tags": [...]})`. Bulk version filters by metadata field and updates matching records. Eventually consistent (short delay). Guidance: re-embed only when text changes; update metadata in-place for all other mutations. This is the production standard for high-volume metadata-only changes.

**Weaviate** (2025): Cross-reference and property patch operations allow updating metadata without re-vectorising. For multi-tenant deployments, per-tenant ACL metadata is updated via PATCH on the object, not full re-insert. Weaviate's access control documentation (v1.28+) treats permissions as metadata stored alongside vectors, updated asynchronously from an auth service via webhooks.

**LlamaIndex IngestionPipeline**: Uses content-hash-based dedup (node + transformation hash). Metadata mutations that do not change content are outside the dedup mechanism — LlamaIndex does not automatically re-process nodes when only metadata changes. Production guidance from community: use a separate metadata propagation pass after ingest, or trigger targeted `vector_store.update_doc()` calls. No built-in drift detection for metadata-only changes.

**LangChain / Haystack**: No first-class mechanism for metadata-only propagation. Community pattern: on taxonomy or ACL change, emit a change event, run a targeted metadata-filter query to find affected vectors, call partial-update API. Full re-ingest is discouraged for large KBs (expensive and slow).

**Databricks Mosaic AI Vector Search (2024)**: ACL metadata is treated as a first-class filter field synced from Unity Catalog. The recommended pattern is: source system change event → Delta Lake update → incremental sync to vector index. Metadata-only changes use partial sync rather than full re-ingest. This is the clearest industry example of event-driven ACL propagation in a production RAG system.

**Google Vertex AI Search (2025)**: Import pipeline snapshots document metadata at ingest time. Mutable metadata (ACLs, labels) requires a separate metadata-update API call after initial import. Google's architecture guide explicitly warns: "if you update access controls in your source system without re-indexing, queries may return documents the user cannot access."

### Event-driven invalidation pattern (current best practice)

The industry consensus for metadata that can change post-ingest is:

```
Source system change event
    → event bus (Kafka / pub-sub)
    → metadata propagation worker
    → vector store partial metadata update (no re-embed)
    → optional: cache invalidation
```

This decouples metadata freshness from embedding freshness. Embeddings change on content changes; metadata changes on permission/taxonomy/label changes. The two have different frequencies and different correctness requirements.

For taxonomy specifically: the primary risk is filter-miss (user queries a taxonomy node and gets no results because documents were classified before the taxonomy reorganisation). The standard mitigation is a **backfill job**: on taxonomy change, query the vector store for all documents in the affected KB and re-run taxonomy classification. This is more targeted than full re-ingest and preserves enriched text and embeddings.

---

## Fix recommendations

Ranked by impact and implementation cost.

### Priority 1 — `taxonomy_node_ids` (HIGH — affects retrieval correctness)

**Should be refreshed** at Phase 2 write-time, or via event-driven propagation.

Two options:

**Option A — Re-read from PG at Phase 2 write-time** (mirrors the visibility pattern):
```python
# In _enrich_document(), just before upsert_enriched_chunks:
if "taxonomy_node_ids" in extra_payload:
    fresh_taxonomy = await pg_store.get_artifact_taxonomy(artifact_id, pool)
    if fresh_taxonomy is not None:
        extra_payload["taxonomy_node_ids"] = fresh_taxonomy
```
Cost: one PG query per Phase 2 job. Limitation: only fixes the Phase 1→Phase 2 window; documents ingested before a taxonomy reorganisation still drift.

**Option B — Background propagation job** (more complete):
On KB taxonomy change in portal-api, publish an event. A worker queries Qdrant for all points in that KB and updates `taxonomy_node_ids` via Qdrant's `set_payload` API. This handles both the Phase 1→2 window and post-ingest reorganisations.

Option A should be implemented first (low cost, mirrors existing visibility pattern). Option B is the long-term fix.

### Priority 2 — `tags` (MEDIUM — affects retrieval filtering)

**Should be refreshed** at Phase 2 write-time if tags are set by the user (not frontmatter-derived), because users can add/remove tags in the portal between Phase 1 and Phase 2.

Frontmatter-derived tags cannot change without re-ingest (they come from document content), so the risk is limited to portal-managed tags. Same pattern as Priority 1 Option A: re-read portal tags from PG at Phase 2 write-time and merge.

If tags are only set via frontmatter in klai's current deployment (not via a portal UI), this can be deferred.

### Priority 3 — `kb_name` (LOW — display-only, no retrieval impact)

**Should stay as snapshot.** KB renames are infrequent and `kb_name` is display-only — it does not appear in any Qdrant filter in retrieval-api. Stale `kb_name` is a cosmetic inconsistency.

Mitigation if desired: emit an `PATCH /ingest/v1/kb/rename` endpoint (analogous to the existing visibility endpoint) that calls Qdrant `set_payload` on all points in the KB. This is an optional UX improvement, not a correctness issue.

### Priority 4 — `content_label`, `source_domain`, `connector_type` (LOW — display-only)

**Should stay as snapshot.** These fields are derived from document content (`content_label`) or are static connector configuration (`source_domain`, `connector_type`). They cannot meaningfully change without re-ingesting the document. No retrieval correctness impact.

### Priority 5 — Frontmatter metadata (NONE — by definition snapshot)

**Must stay as snapshot.** Frontmatter metadata (titles, custom keys) is part of the document content. Changes to frontmatter require re-ingesting the document. No action needed.

---

## Risk assessment

**Taxonomy drift in klai's actual usage patterns:**

- **Phase 1→2 window**: Procrastinate queue latency for `enrich-interactive` jobs is typically seconds to low minutes. For `enrich-bulk` (crawl/import), it can be minutes to tens of minutes under load. The risk of a taxonomy reassignment happening during this window on a specific document is low for interactive uploads but non-trivial for bulk imports spanning hours.

- **Post-ingest taxonomy reorganisation**: This is the higher-risk scenario. When an org admin adds a new taxonomy node or restructures the tree, existing documents are not re-classified. The SPEC-KB-022 taxonomy classifier (`classify_document`) runs at Phase 1 only. Documents indexed before the reorganisation will filter incorrectly until re-ingested. For a KB with hundreds of documents, this can mean a substantial fraction of content is invisible or mis-placed in taxonomy-filtered queries.

- **Practical frequency**: Taxonomy reorganisations are expected to be occasional (quarterly-scale) rather than continuous. The risk is concentrated at KB setup time when admins are actively tuning the taxonomy. This is precisely when the drift is most likely to be noticed.

**Tag drift**: Lower risk. Tags are currently primarily frontmatter-derived in klai (static per document). Portal-managed tags, if they exist, would carry higher drift risk.

**Visibility drift**: Already mitigated. The dual mechanism (Phase 2 re-read + PATCH endpoint) makes visibility the most reliable field.

---

## References

- [Pinecone — Update records](https://docs.pinecone.io/guides/manage-data/update-data): partial metadata update API, recommended for ACL/tag changes without re-embedding.
- [Pinecone — Bulk Data Operations: Update, Delete, Fetch by Metadata](https://www.pinecone.io/blog/update-delete-and-fetch-by-metadata/): bulk metadata update pattern for large-scale propagation.
- [LlamaIndex — Async Ingestion Pipeline + Metadata Extraction](https://developers.llamaindex.ai/python/examples/ingestion/async_ingestion_pipeline/): async metadata extraction during ingest; no mechanism for post-ingest metadata mutation.
- [LlamaIndex — Ingestion Pipeline (TS)](https://developers.llamaindex.ai/typescript/framework/modules/data/ingestion_pipeline/): content-hash dedup — metadata-only changes do not trigger re-processing.
- [Haystack — Advanced Metadata Enrichment](https://haystack.deepset.ai/cookbook/metadata_enrichment): static enrichment during ingest; no post-ingest mutation mechanism documented.
- [Databricks — Mastering RAG Chatbot Security: ACL and Metadata Filtering with Mosaic AI Vector Search](https://community.databricks.com/t5/technical-blog/mastering-rag-chatbot-security-acl-and-metadata-filtering-with/ba-p/101946): ACL metadata as first-class filter field; incremental sync from source system on change.
- [DBI Services — RAG Series: Embedding Versioning with pgvector](https://www.dbi-services.com/blog/rag-series-embedding-versioning-with-pgvector-why-event-driven-architecture-is-a-precondition-to-ai-data-workflows/): event-driven reindexing; metadata-only changes explicitly excluded from re-embedding.
- [Applied AI — Enterprise RAG Architecture: A Practitioner's Guide](https://www.applied-ai.com/briefings/enterprise-rag-architecture/): ACL-aware retrieval; event-driven metadata propagation as the production standard.
- [VentureBeat — Enterprises are measuring the wrong part of RAG](https://venturebeat.com/orchestration/enterprises-are-measuring-the-wrong-part-of-rag): freshness failures from async indexing pipelines; event-driven reindexing as the mitigation.
