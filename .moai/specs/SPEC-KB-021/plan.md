---
id: SPEC-KB-021
phase: plan
---

# Implementation Plan — SPEC-KB-021

## Phase 1: Qdrant infrastructure (Day 1)

**File:** `klai-knowledge-ingest/knowledge_ingest/qdrant_store.py`

1. Add `taxonomy_node_id` to `_ensure_payload_indexes()` — create keyword index alongside existing ones
2. Add `taxonomy_node_id: int | None` parameter to `upsert_chunks()` and `upsert_full_document()` — store in `base_payload` when provided
3. No migration needed — Qdrant schema is additive

## Phase 2: Classification service (Day 1–2)

**New file:** `klai-knowledge-ingest/knowledge_ingest/taxonomy_classifier.py`

```python
async def classify_document(
    title: str,
    content_preview: str,  # first 500 chars
    taxonomy_nodes: list[TaxonomyNode],  # id + name
) -> tuple[int | None, float]:
    """Returns (node_id, confidence). node_id=None if confidence < 0.5 or no nodes."""
```

- Uses `klai-fast` via LiteLLM
- Structured output: `{ "node_id": int | null, "confidence": float, "reasoning": str }`
- 5-second timeout, falls back to `(None, 0.0)` on error
- Unit-testable without LLM (mock the LiteLLM call)

**New file:** `klai-knowledge-ingest/knowledge_ingest/portal_client.py`

```python
async def fetch_taxonomy_nodes(kb_slug: str, org_id: str) -> list[TaxonomyNode]
async def submit_taxonomy_proposal(kb_slug: str, proposal: TaxonomyProposal) -> None
```

- Reads `PORTAL_URL` + `PORTAL_INTERNAL_TOKEN` from settings
- `fetch_taxonomy_nodes` result is cached per (org_id, kb_slug) for 5 minutes
- Missing token → returns empty list / skips submission with warning log

## Phase 3: Ingest pipeline integration (Day 2)

**File:** `klai-knowledge-ingest/knowledge_ingest/tasks.py` (or wherever document ingest is orchestrated)

In the document ingest task, after chunking and before embedding:

```python
taxonomy_nodes = await portal_client.fetch_taxonomy_nodes(kb_slug, org_id)
if taxonomy_nodes:
    node_id, confidence = await classify_document(title, content[:500], taxonomy_nodes)
else:
    node_id = None
# pass node_id to upsert_chunks(...)
```

Track documents with `node_id = None` per batch. After batch completes, run proposal generation if ≥3 unmatched.

## Phase 4: Proposal generation (Day 2–3)

**File:** `klai-knowledge-ingest/knowledge_ingest/proposal_generator.py`

```python
async def maybe_generate_proposal(
    org_id: str,
    kb_slug: str,
    unmatched_documents: list[DocumentSummary],
    existing_nodes: list[TaxonomyNode],
) -> None
```

- Uses `klai-fast` to suggest a category name for the cluster of unmatched documents
- Deduplication: query portal for existing pending proposals with same name before submitting
- Submits via `portal_client.submit_taxonomy_proposal()`

## Phase 5: Retrieval filter (Day 3)

**File:** `klai-retrieval-api/retrieval_api/api/retrieve.py`

Add `taxonomy_node_ids: list[int] | None = None` to `RetrieveRequest`.

**File:** `klai-retrieval-api/retrieval_api/services/search.py`

In the Qdrant prefetch builder, if `taxonomy_node_ids` is non-empty, add:
```python
FieldCondition(key="taxonomy_node_id", match=MatchAny(any=taxonomy_node_ids))
```
alongside existing org_id/kb_slug conditions.

## Phase 6: Backfill endpoint (Day 3–4)

**File:** `klai-knowledge-ingest/knowledge_ingest/routes/backfill.py` (or add to existing routes)

`POST /ingest/v1/taxonomy/backfill`
- Protected by `X-Internal-Token` header
- Scrolls Qdrant with filter `{ must_not: [{ key: "taxonomy_node_id", is_empty: false }] }` per org/kb
- Classifies each unique (path, artifact_id) document once, updates all its chunks with `set_payload`
- Returns progress stats

## Phase 7: Gap event tagging (Day 4)

**File:** `deploy/litellm/klai_knowledge.py` (the LiteLLM hook)

When firing gap event and `taxonomy_node_ids` was in the retrieve request, include them in the event payload.

---

## Milestone Summary

| Milestone | Deliverable | Day |
|---|---|---|
| M1 | Qdrant index + upsert with taxonomy_node_id | 1 |
| M2 | TaxonomyClassifier + PortalClient | 1–2 |
| M3 | Ingest pipeline tags chunks at ingest time | 2 |
| M4 | Proposal generation when ≥3 docs unmatched | 2–3 |
| M5 | Retrieval API accepts taxonomy_node_ids filter | 3 |
| M6 | Backfill endpoint for existing chunks | 3–4 |
| M7 | Gap events include taxonomy signal | 4 |

Total estimated effort: 3–4 days single developer.

---

## Risk Register

| Risk | Likelihood | Mitigation |
|---|---|---|
| klai-fast classification quality is too low | Medium | Start with confidence threshold 0.5, monitor hit rate; lower to 0.3 if needed |
| Portal fetch_taxonomy_nodes adds latency to ingest | Low | Cache result 5 min; skip gracefully if portal unreachable |
| Backfill runs too slowly on large KBs | Medium | batch_size param + configurable sleep between batches |
| Duplicate proposals spam the review queue | Low | Deduplication query before submit + 24h window |
