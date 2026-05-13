---
id: SPEC-RAG-PARENT-CHILD-001
version: "0.1.0"
status: draft
created: 2026-05-04
updated: 2026-05-04
author: Mark Vletter
priority: medium
related:
  - SPEC-RAG-EVAL-001 (precondition: must measure delta)
  - SPEC-RAG-CONTEXTUAL-001 (orthogonal; both work together)
roadmap: docs/architecture/retrieval-improvements-roadmap.md
---

# SPEC-RAG-PARENT-CHILD-001: Parent-child chunking

## Summary

Introduce a hierarchical chunk model — small "child" chunks (200-400 tokens) for embedding-and-matching, large "parent" chunks (1500-3000 tokens) for LLM context. Retrieval matches on child chunks but returns the parent chunk for the LLM prompt. This resolves the precision-vs-context tradeoff: small chunks match better, but small chunks lack the surrounding narrative the LLM needs to answer well.

The current Klai chunk size is 1500 chars / 200 overlap. That's a single-tier compromise: precise enough to match, broad enough for context. Parent-child decouples the two: precise matching AND broad context.

## Motivation

1. **Long-document weakness in current stack.** A 12-page Notion page becomes ~30 chunks at 1500 chars each. Match is on a single chunk; the LLM sees only that chunk plus N neighbours. Compared to "match on a paragraph, return the whole section": the latter loses less narrative.
2. **Industry-standard for 2026 RAG.** Most enterprise RAG implementations (Dify, Databricks RAG Studio, AWS Bedrock Knowledge Bases) ship parent-child as the default. Klai not having it is a comparative weakness.
3. **Compatible with contextual retrieval.** SPEC-RAG-CONTEXTUAL-001 prepends context to chunks before embedding; parent-child changes WHICH chunk gets the context (the small child) and WHICH gets returned to the LLM (the large parent). The two compose cleanly.
4. **Especially valuable for Knowledge-tier customers.** The big-doc-heavy enterprise tenants are exactly the ones who benefit most.

## Scope

### In scope

**Backend — chunking strategy**

- New module `klai-knowledge-ingest/knowledge_ingest/chunking.py` (or extend the existing chunker — decision in plan-phase) with:
  - `def chunk_with_parents(text: str, document_id: str) -> tuple[list[ChildChunk], list[ParentChunk]]`
  - Child: 300 tokens nominal, 50-token overlap. Indexed in Qdrant.
  - Parent: 1500 tokens nominal, no overlap (boundaries align with section headings where possible). Indexed in Postgres `knowledge.parent_chunks` table.
- Each child gets `parent_chunk_id: int` payload. Each parent has `parent_chunk_id` (PK) and `text` blob.

**Database — parent storage**

```sql
CREATE TABLE knowledge.parent_chunks (
  id BIGSERIAL PRIMARY KEY,
  artifact_id UUID NOT NULL REFERENCES knowledge.artifacts(id) ON DELETE CASCADE,
  org_id INT NOT NULL,
  text TEXT NOT NULL,
  token_count INT NOT NULL,
  position INT NOT NULL,                -- ordering within document
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_parent_chunks_artifact ON knowledge.parent_chunks (artifact_id);
```

Parent text is NOT in Qdrant — only IDs in child payloads. Retrieval-api fetches parent text via a JOIN at result-build time.

**Qdrant payload extension**

- Add `parent_chunk_id: int | null` to child chunk payload.
- Existing chunks keep `null` (backward-compatible). New ingests populate it.

**Retrieval-api — fetch parents**

- New module `klai-retrieval-api/retrieval_api/services/parent_lookup.py`:
  - `async def fetch_parents(child_chunks: list[dict], db: AsyncSession) -> dict[int, str]`
  - Takes the top-K child chunks, queries `parent_chunks` for the matching IDs.
  - Returns dict `{parent_id: parent_text}`.
- `retrieve.py::retrieve` endpoint: after reranking, swaps child `text` for parent `text` in the response, where parent is available. Backward-compatible: chunks without `parent_chunk_id` keep their child text.
- ChunkResult model gets new `is_parent_text: bool` flag for client-side debugging.

**Eval comparison**

- SPEC-RAG-EVAL-001 harness with `RAG_EVAL_VARIANT=parent_child_v1`.
- Target: ≥10% improvement in `faithfulness` (LLM has more context to be faithful to) AND no regression in `context_precision`.

**Re-ingest existing corpus**

- Operator-triggered Procrastinate task `rechunk_kb_with_parents` per KB.
- Documented in `docs/runbooks/rechunk-kb-with-parents.md`.
- Idempotent: re-running reproduces parents from same input deterministically.

### Out of scope

- Multi-level hierarchy (grandparent / chapter chunks). Two levels is sufficient for 95% of use cases.
- Dynamic parent boundaries based on semantic similarity. Use heading-based or fixed-size for v1.
- Parent-aware reranker (the cross-encoder still sees the child for reranking — its strength is fine-grained matching). Re-rerank with parent in v2 if eval shows benefit.
- Frontend display changes (chat UI doesn't need to know about parent vs child).

## Acceptance Criteria (EARS)

- **REQ-1**: WHEN a document is ingested via the new chunker, every child chunk SHALL have a `parent_chunk_id` payload pointing to a row in `knowledge.parent_chunks`.
- **REQ-2**: WHEN the retrieval-api endpoint returns chunks AND `parent_chunk_id` is non-null, the response SHALL contain the parent's `text`, NOT the child's.
- **REQ-3**: WHEN `parent_chunk_id` is null (legacy chunk), the response SHALL contain the child's text — backward-compatible.
- **REQ-4**: Per-document ingest time SHALL increase by < 20% vs the current chunker (verified on a 10-document benchmark).
- **REQ-5**: Parent-chunk text SHALL be deduplicated within a document — the same parent is returned at most once even if multiple children matched it (top-10 with parent dedup may yield < 10 unique parents).
- **REQ-6**: Connector-delete SHALL cascade to `parent_chunks` via FK ON DELETE CASCADE.
- **REQ-7**: After deploy + rechunk on test-tenant, RAGAS metrics with `variant=parent_child_v1` SHALL show ≥10% improvement in `faithfulness` AND no regression > 5% in `context_precision`.

## Open Questions (resolve in /plan)

1. **Heading-based vs fixed-size parents** — heading-based is more semantically coherent but requires parsing structure (markdown, HTML). Fixed-size always works. Default fixed-size for v1; revisit if eval-quality plateaus.
2. **Top-K dedup ratio** — if 8 of top-10 children share 1 parent, do we return 1 chunk or 10? Default: dedup, so 1 unique parent + 2 other parents = 3 chunks. May reduce surface area to < 10 chunks. Trade-off documented.
3. **Storage cost** — duplicating text (children + parents) doubles storage. For 100M tokens, this is ~200MB — negligible. Verify in plan.
4. **Crawler integration** — the SPEC-CRAWLER-005 anchor_texts/links_to flow currently writes them on the chunk-level. Do they go on parent or child? Likely child (matching). Decide in plan.

## Estimated effort

7-10 days:
- Day 1-2: `chunking.py` parent-child module + unit tests
- Day 3: alembic migration + `parent_chunks` table
- Day 4: enrichment_tasks integration + Qdrant payload update
- Day 5-6: retrieval-api parent-lookup + integration tests
- Day 7: rechunk task + runbook
- Day 8: deploy to staging, rechunk test-tenant, run eval with `variant=parent_child_v1`
- Day 9-10: tune chunk sizes based on eval; ship to production
