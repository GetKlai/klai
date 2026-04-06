---
id: SPEC-KB-023
version: "1.0"
status: implemented
created: "2026-04-06"
updated: "2026-04-06"
author: Mark Vletter
priority: medium
tags: [taxonomy, knowledge-ingest, blind-labeling, qdrant, discovery]
related: [SPEC-KB-022, SPEC-KB-024]
---

# SPEC-KB-023: Taxonomy Discovery — Blind Labeling at Ingest

## Context

SPEC-KB-022 implemented multi-label classification against *existing* taxonomy nodes. The fundamental problem with that approach for category *discovery* is that an LLM given a fixed list of categories will always pick the best match — even when the match is weak. Confirmation bias is structural, not a prompt problem.

To discover genuinely new categories without this bias, we need a separate signal: a free-form description of each document generated *before* the LLM sees the existing taxonomy. This description is unanchored — it reflects only what the document is about, not what the system already knows.

These descriptions, stored as `content_label` per document, become the raw material for SPEC-KB-024 (embedding clustering + auto-categorisation). Without them, KB-024 cannot function.

---

## Scope

**In scope:**
- `klai-knowledge-ingest`: generate `content_label` for every ingested document
- `klai-knowledge-ingest`: store `content_label` as Qdrant payload on all chunks of a document
- `klai-knowledge-ingest`: add Qdrant keyword index on `content_label`

**Out of scope:**
- Clustering or analysis of `content_label` values (SPEC-KB-024)
- Any changes to existing `taxonomy_node_ids` classification logic
- Portal UI for `content_label`
- Backfill of existing chunks (done separately via the backfill endpoint extended in SPEC-KB-024)

---

## Requirements

### R1 — Blind Label Generation at Ingest

WHEN a document is ingested into any knowledge base,
THEN the ingest pipeline SHALL generate a `content_label` for the document
BEFORE consulting existing taxonomy nodes.

The `content_label` generation SHALL:
- Use `klai-fast`
- Input: document title + first 500 characters of content
- Prompt: ask for 3–5 lowercase keywords describing the document content, with NO reference to any existing taxonomy categories
- Return a `list[str]` of 3–5 keywords, e.g. `["sip-trunk", "provider-portability", "telefooncentrale"]`
- Complete within 15 seconds; on timeout or error: store `content_label: []` and continue (non-fatal)

### R2 — Storage on Qdrant Chunks

WHEN `content_label` is generated (including empty list on failure),
THEN it SHALL be stored as a keyword array payload field on ALL chunks of that document in Qdrant.

The field SHALL be named `content_label` (distinct from `tags` which is classification-derived).

### R3 — Qdrant Payload Index

The `content_label` field SHALL have a keyword payload index in Qdrant
so that it can be used in scroll filters during clustering (SPEC-KB-024).

This index SHALL be created in `_ensure_payload_indexes()` alongside existing indexes.

### R4 — Rate Limiting

`content_label` generation SHALL use the existing `_TokenBucketLimiter` / `_RateLimitedTransport`
pattern (same rate as `graphiti_llm_rps`, default 1 req/s) to avoid 429s on LiteLLM.

The label generation and taxonomy classification are sequential (label first, then classify)
so they share the same module-level limiter without additional configuration.

### R5 — One LLM Call Total Budget

The total LLM call budget per document at ingest time SHALL be 2 calls:
1. `content_label` generation (blind, no taxonomy context)
2. `taxonomy_node_ids` + `tags` classification (with taxonomy context, existing from KB-022)

No additional LLM calls SHALL be introduced by this SPEC.

---

## Technical Design

### New module: `content_labeler.py`

```
async def generate_content_label(
    title: str,
    content_preview: str,
) -> list[str]:
```

Prompt (system):
> You are a document keyword extractor. Given a document title and content preview, return 3-5 lowercase keywords that describe what this document is about. Return JSON only: {"keywords": ["keyword1", "keyword2"]}. Do NOT use category names or organisational terms — use only descriptive content keywords.

Uses `_RateLimitedTransport` with the shared `_get_llm_limiter()` from `taxonomy_classifier.py`.

### Ingest pipeline change (`routes/ingest.py`)

Order of operations per document:
1. Embed chunks (existing)
2. Generate `content_label` (NEW — blind, no taxonomy)
3. Classify `taxonomy_node_ids` + `tags` (existing KB-022)
4. Upsert to Qdrant with all three fields

### Qdrant payload (`qdrant_store.py`)

`upsert_chunks()` signature extended:
```python
content_label: list[str] | None = None
```

---

## Acceptance Criteria

| # | Criterion | How to verify |
|---|-----------|---------------|
| AC1 | New docs get `content_label` in Qdrant payload | Scroll Qdrant after ingest, check field present |
| AC2 | `content_label` reflects document content, not taxonomy node names | Manual spot check: label for "Fanvil Opties" should contain hardware/phone keywords, not "Telefonie-apparatuur" |
| AC3 | LiteLLM not rate-limited during ingest of 10 consecutive docs | No 429 in logs |
| AC4 | `content_label` index exists in Qdrant collection | `GET /collections/klai_knowledge` shows keyword index on `content_label` |
| AC5 | Timeout/failure stores `[]` and does not fail ingest | Force timeout via env override, verify chunk is stored |
| AC6 | Existing `taxonomy_node_ids` classification unaffected | Spot check classification results unchanged |
