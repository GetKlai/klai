---
id: SPEC-KB-021
version: "1.0"
status: draft
created: "2026-04-05"
updated: "2026-04-05"
author: Mark Vletter
priority: medium
tags: [taxonomy, retrieval, knowledge-ingest, qdrant, proposals]
related: [SPEC-TAXONOMY-001, SPEC-KB-015, SPEC-KB-020]
---

# SPEC-KB-021: Taxonomy Integration — Chunk Tagging, Retrieval Filtering & Proposal Generation

## Context

The portal has a complete taxonomy infrastructure (nodes, proposals, review queue UI, API) but no producer. Taxonomy nodes exist in PostgreSQL only — they have zero effect on retrieval because Qdrant chunks carry no category information. The result: taxonomy is a standalone editorial tool with no downstream impact on what the AI retrieves or how gap detection works.

Research confirms that taxonomy-guided retrieval yields 35–48% higher precision over flat vector search. The missing integration has three parts:

1. **Chunk tagging at ingest** — store `taxonomy_node_id` on Qdrant chunks so retrieval can filter by category
2. **Proposal generation** — knowledge-ingest suggests new taxonomy categories based on document clustering
3. **Retroactive backfill** — classify existing chunks that predate this feature

A fourth integration (gap detection × taxonomy) is included as an additive signal.

---

## Scope

**In scope:**
- `klai-knowledge-ingest`: classify documents at ingest time, store taxonomy_node_id in Qdrant payload, generate proposals to portal, expose backfill endpoint
- `klai-retrieval-api`: add optional `taxonomy_node_ids` filter to RetrieveRequest
- `klai-portal/backend`: internal taxonomy proposals endpoint (already exists — verify it works end-to-end)

**Out of scope:**
- klai-docs publication structure with taxonomy navigation (separate SPEC)
- User-facing taxonomy selector in the KBScopeBar (separate SPEC, depends on this)
- Graph-based taxonomy traversal or ontology expansion

---

## Requirements

### R1 — Chunk Tagging at Ingest

WHEN a document is ingested into a knowledge base that has at least one taxonomy node,
THEN the ingest pipeline SHALL classify the document into the best matching taxonomy node
and store `taxonomy_node_id` (integer | null) on all resulting Qdrant chunks.

WHEN no taxonomy node matches with sufficient confidence (< 0.5),
THEN `taxonomy_node_id` SHALL be stored as `null` on the chunks.

WHEN a knowledge base has NO taxonomy nodes,
THEN classification SHALL be skipped (no LLM call), `taxonomy_node_id` SHALL be omitted from chunks,
AND the document SHALL be added to the "unmatched" batch for proposal generation (see R4).

The classification SHALL use `klai-fast` with:
- Input: document title + first 500 characters of content
- Context: list of existing taxonomy node names (id + name)
- Output: `{ node_id: int | null, confidence: float }`
- Timeout: 5 seconds; on timeout, store `null` without failing the ingest

### R2 — Qdrant Payload Index

The system SHALL create a `keyword`-type payload index on `taxonomy_node_id` in the `klai_knowledge` collection,
alongside existing indexes for `org_id`, `kb_slug`, `artifact_id`, etc.

### R3 — Retrieval Filter

WHEN a retrieve request includes `taxonomy_node_ids: list[int]` (non-empty),
THEN the Qdrant query SHALL add a `MatchAny` filter on `taxonomy_node_id`
in addition to existing `org_id` and `kb_slug` filters.

IF `taxonomy_node_ids` is absent or empty,
THEN no taxonomy filter SHALL be applied (existing behavior preserved).

The `taxonomy_node_ids` field SHALL be optional in `RetrieveRequest` with default `None`.

### R4 — Proposal Generation (self-bootstrapping)

WHEN knowledge-ingest finishes ingesting a batch of documents for a KB,
AND at least one document in the batch is "unmatched" (either `taxonomy_node_id = null` OR the KB has no nodes),
THEN the ingest service SHALL cluster the unmatched documents and call
`POST /api/app/knowledge-bases/{kb_slug}/taxonomy/proposals` for each detected cluster,
using `klai-fast` to generate a suggested category name per cluster.

This means the system is self-bootstrapping:
- KB with 0 nodes: all documents are unmatched → proposals are generated from scratch
- KB with existing nodes: only truly unmatched documents (confidence < 0.5) trigger proposals

The proposal SHALL only be submitted when:
- At least 3 documents are in the unmatched batch
- The suggested category name does not already exist among the KB's taxonomy nodes or pending proposals
- The portal internal token is configured (`PORTAL_INTERNAL_TOKEN` env var)

IF `PORTAL_INTERNAL_TOKEN` is not configured,
THEN proposal submission SHALL be skipped silently (log a warning, do not fail ingest).

### R5 — Backfill Endpoint

The system SHALL expose `POST /ingest/v1/taxonomy/backfill` (internal endpoint, requires `X-Internal-Token`).

Request body:
```json
{ "org_id": "string", "kb_slug": "string", "batch_size": 100 }
```

WHEN called, the endpoint SHALL:
1. Fetch taxonomy nodes for the given KB from the portal
2. Iterate Qdrant chunks for that org/KB that have no `taxonomy_node_id` payload field
3. For each chunk, classify using `klai-fast` and update the Qdrant payload via `set_payload`
4. Return `{ "processed": int, "tagged": int, "skipped": int }`

The endpoint SHALL be idempotent: re-running on already-tagged chunks SHALL be a no-op.

### R6 — Gap Event Taxonomy Signal

WHEN a gap event is fired (hard gap or soft gap),
AND the retrieval request included a `taxonomy_node_ids` filter,
THEN the gap event payload SHALL include `taxonomy_node_ids` so editorial tooling can correlate unmet queries to specific knowledge areas.

This is additive — gap events without taxonomy context continue to fire unchanged.

### R7 — Constraints

The system SHALL NOT break existing retrieval behavior when taxonomy_node_id is absent from a chunk payload.
The system SHALL NOT increase P95 retrieval latency by more than 20ms.
The system SHALL NOT call the taxonomy classification LLM more than once per document (not per chunk).
The system SHALL NOT submit duplicate proposals (same name, same KB) within a 24-hour window.

---

## Data Model Changes

### Qdrant chunk payload (additions)

```
taxonomy_node_id: int | null   (new, optional field)
                               null  = classified, no match
                               <int> = matched to portal taxonomy node ID
                               absent = KB has no taxonomy nodes
```

### RetrieveRequest (additions)

```python
taxonomy_node_ids: list[int] | None = None
```

### knowledge-ingest environment variables (additions)

```
PORTAL_URL               # already used for other internal calls (or new)
PORTAL_INTERNAL_TOKEN    # shared secret for internal portal endpoints
```

---

## Assumptions

| Assumption | Confidence | Risk if wrong |
|---|---|---|
| `klai-fast` can classify a document into a taxonomy node from title + 500 chars | High | Classification quality drops; mitigation: lower confidence threshold → more nulls |
| Portal taxonomy endpoint authentication uses X-Internal-Token header | High | Check existing implementation in portal backend |
| Qdrant set_payload is safe to call on existing points | High | Standard Qdrant operation |
| Backfill can run without blocking production traffic | Medium | Add rate limiting (batch_size + sleep between batches) |
| LLM cost for classification is acceptable (1 call per document) | High | klai-fast is the cheapest tier; classification is a tiny call |
