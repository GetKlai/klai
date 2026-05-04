---
id: SPEC-RAG-CONTEXTUAL-001
version: "0.1.0"
status: draft
created: 2026-05-04
updated: 2026-05-04
author: Mark Vletter
priority: high
related:
  - SPEC-RAG-EVAL-001 (precondition: must measure delta)
  - SPEC-CRAWLER-005 (anchor_texts pattern, similar enrichment)
roadmap: docs/architecture/retrieval-improvements-roadmap.md
---

# SPEC-RAG-CONTEXTUAL-001: Contextual Retrieval (Anthropic-pattern)

## Summary

Implement Anthropic's contextual-retrieval pattern: before embedding a chunk, prepend a 1-2 sentence LLM-generated context that situates the chunk in its source document. Anthropic's published benchmark: **49% reduction in retrieval-failure-rate** (top-20-chunk failures dropped from 5.7% to 2.9%) when combined with hybrid retrieval.

This is the highest-ROI Tier-1 enhancement after the eval harness, because it addresses a structural limitation of every embedding-based RAG: **chunks are not self-contained**. Without context, the chunk *"This requires the customer's explicit consent and may take up to 14 days."* is a vague match against any privacy-related query. With the prepended context *"This chunk is from the Data Subject Access Request section of the Voys GDPR procedure manual."* it becomes a precise match for *"how long does a Voys GDPR access request take?"*.

## Motivation

1. **Cheap-once, win-forever.** Generating context per chunk is a one-time ingest cost. Anthropic's research: ~€1 per million doc-tokens with prompt caching. For Klai's pre-launch corpus (estimated < 100M tokens) the entire baseline is ~€100, then incremental on each new ingest.
2. **Klai's enrichment pipeline is the perfect home.** `klai-knowledge-ingest/knowledge_ingest/enrichment_tasks.py` already runs as a Procrastinate fire-and-forget after ingest. Adding a `chunk_context` field to the same enrichment job is a natural extension.
3. **Stacks with existing crawler enrichment.** SPEC-CRAWLER-005 already prepends `anchor_texts` and `links_to` for crawled pages. Contextual retrieval is the same pattern (write metadata that helps embedding) but works for ALL chunk types — uploads, Notion, Drive, M365 — not just crawled pages.

## Scope

### In scope

**Backend — chunk-context generation**

- New module `klai-knowledge-ingest/knowledge_ingest/contextual.py` with:
  - `async def generate_chunk_context(chunk_text: str, document_summary: str, document_title: str, llm_client) -> str`
  - Returns 1-2 sentence context (max ~100 tokens). Cached per `(document_id, chunk_index)` so re-runs are free.
- LLM model: `klai-fast` (Mistral small via litellm). Prompt-caching enabled.
- Prompt template (initial draft, refined in plan-phase):
  ```
  Document title: {title}
  Document summary (1-2 sentences): {summary}
  Chunk to contextualise: {chunk_text}

  Write 1 sentence (≤30 words) that places this chunk in the document's
  context, focusing on what topic, section, or scenario the chunk
  addresses. Reply with ONLY the sentence, no preamble.
  ```

**Backend — wiring into enrichment**

- `enrichment_tasks.enrich_document_interactive` and `enrich_document_bulk` get a new step:
  - For each chunk in `extra_payload["chunks"]`, generate `chunk_context` and prepend it to the chunk text BEFORE the embedding call.
  - Persist the original chunk + context separately:
    - `chunk_text` (original, what the LLM sees in the prompt)
    - `chunk_context` (new, what BM25 + embedding see)
    - `embedding_input` = `chunk_context + "\n\n" + chunk_text` (what TEI/sparse models embed)
- Document-level summary (`document_summary`) is generated once per document (not per chunk) via klai-fast at ingest time. Stored on artifact.

**Qdrant schema**

- New payload key `chunk_context: str | null`. Backward-compatible: existing chunks have `null`, new ingests have a string.
- New ensure_collection() entry: index `chunk_context` for BM25 (sparse) so contextual BM25 works as Anthropic prescribed.

**Re-embed of existing corpus**

- One-shot Procrastinate task `recontextualize_kb` that takes `(org_id, kb_slug)` and:
  - Iterates all chunks
  - Generates context per chunk
  - Re-embeds and updates Qdrant payload
- Operator-triggered, not automatic. Documented in `docs/runbooks/recontextualize-kb.md`.

**Eval comparison**

- Use SPEC-RAG-EVAL-001 harness with `RAG_EVAL_VARIANT=contextual_v1` to measure delta vs baseline.
- Target: ≥10% improvement in `context_precision` AND ≥5% improvement in `faithfulness` to declare success.

### Out of scope

- Self-updating context when documents change (initial: re-run `recontextualize_kb` manually; auto-trigger in follow-up)
- Multi-language context (initial: same language as the document; mixed-language docs use English context)
- Context-based query expansion (separate concern; HyDE territory in Tier 3)
- Reranker integration changes (the cross-encoder already sees the full chunk text + context-prepended via `embedding_input`; no behaviour change there)

## Acceptance Criteria (EARS)

- **REQ-1**: WHEN a new document is ingested via the enrichment pipeline, every chunk SHALL have a `chunk_context` field of length 10-500 chars in its Qdrant payload.
- **REQ-2**: WHEN context generation fails (LLM timeout, parse error), the chunk SHALL still be embedded with its original text and `chunk_context: null` — pipeline never blocks on this step.
- **REQ-3**: The `embedding_input` SHALL be `chunk_context + "\n\n" + chunk_text` when context is present, and `chunk_text` alone when null. Reranker receives the full text plus context.
- **REQ-4**: WHEN `recontextualize_kb` runs against an existing KB, every chunk's payload SHALL be updated and re-embedded; the operation SHALL be idempotent (re-running yields the same context for unchanged docs, due to caching).
- **REQ-5**: After deploy + recontextualize-kb on the test-tenant corpus, RAGAS metrics on the `chat` suite SHALL show ≥10% improvement in `context_precision` (vs baseline run with `variant=baseline`).
- **REQ-6**: Per-document one-time cost SHALL be < €0.05 for an 8000-token document (verified by counting LLM tokens in the contextual generation calls).

## Open Questions (resolve in /plan)

1. **Document summary generation** — generate once per document, or per ingest-batch? If a document is split across multiple ingest tasks (long Notion page), regenerating the summary is wasteful.
2. **Prompt caching strategy** — Anthropic's pattern caches the document text and re-uses for each chunk. With Mistral via litellm, do we have the same caching support? Need verification.
3. **Sparse vs dense behaviour** — should context be prepended for the dense embedding only, or also for sparse (BM25)? Anthropic's research uses both; verify our `bge-m3-sparse` accepts the longer input.
4. **Backward compatibility** — chunks without `chunk_context` (the legacy corpus) need to keep working. The `embedding_input` fallback covers it, but the BM25 boost from contextual-BM25 only applies to new chunks. After re-contextualize, the corpus is uniform.

## Estimated effort

5-7 days:
- Day 1-2: `contextual.py` module + unit tests + prompt iteration
- Day 3: enrichment pipeline integration + `extra_payload` plumbing
- Day 4: Qdrant schema migration + ensure_collection update
- Day 5: `recontextualize_kb` task + runbook
- Day 6: deploy to staging, re-contextualize the test-tenant corpus, run eval harness with `variant=contextual_v1`
- Day 7: tune prompt based on eval delta, ship to production
