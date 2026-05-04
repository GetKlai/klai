---
id: SPEC-RAG-TAXONOMY-001
version: "0.1.0"
status: draft
created: 2026-05-04
updated: 2026-05-04
author: Mark Vletter
priority: medium
related:
  - SPEC-RAG-EVAL-001 (precondition: must measure delta)
  - SPEC-RAG-QUERY-REWRITE-001 (shares the same LLM call site)
  - PR #90 (closed; the FROZEN klai-focus precursor — this SPEC ports the same intent to the Knowledge stack)
roadmap: docs/architecture/retrieval-improvements-roadmap.md
---

# SPEC-RAG-TAXONOMY-001: Query-time taxonomy filtering

## Summary

Ingest-time taxonomy classification already lands on every chunk — `taxonomy_node_ids: int[]` is written into the Qdrant payload by `klai-knowledge-ingest/knowledge_ingest/taxonomy_classifier.py`. But query-time, nothing reads it. The retrieval-api code path that filters on `taxonomy_node_ids` exists in `_scope_filter()` but is never called by the LiteLLM hook because no caller produces the classification.

This SPEC closes the loop: classify the (rewritten) query against the tenant's KB taxonomy in the LiteLLM hook, inject `taxonomy_node_ids` into the `retrieve_body`, and let the existing retrieval-api filter narrow the candidate pool before BM25 + dense + reranker run.

The expected impact is biggest on **large categorised KBs** (>1000 chunks across many topics) where the top-K is currently polluted by chunks from off-topic categories that happened to have a strong embedding match. PR #90's original measurement on `klai-focus` showed **+10-15% precision on the categorical-noise subset** with no regression on the random-recall subset.

## Motivation

1. **The infrastructure is already there.** The classifier runs at ingest. The filter is wired in `_scope_filter()`. The taxonomy tree is curated per KB. Only the query-time call is missing — closing the gap is < 200 lines of new code.
2. **Companion to query-rewriting.** SPEC-RAG-QUERY-REWRITE-001 already calls `klai-fast` per query in the litellm-hook. Re-using that call to ALSO emit `taxonomy_node_ids` adds zero latency and ~50 tokens per query. The two SPECs ship as one logical change for the hook.
3. **PR #90 closed without landing.** The klai-focus implementation was scope-frozen and the PR closed obsolete. The Knowledge stack needs the same capability — this SPEC is the Knowledge-stack revival.
4. **Coverage-stats fallback prevents the dead-zone.** A common failure mode of taxonomy filters: KB has 20% tagged content, query gets classified, filter excludes 80% of the KB, recall craters. The fallback (skip filter when coverage < threshold) avoids this without operator intervention.

## Scope

### In scope

**LiteLLM hook — taxonomy classifier**

- Extend the `_rewrite_query` helper from SPEC-RAG-QUERY-REWRITE-001 to ALSO return classified taxonomy nodes. Output shape:
  ```python
  async def _rewrite_and_classify(
      raw_query: str,
      conversation_history: list[dict],
      taxonomy_tree: list[TaxonomyNode],
      client: httpx.AsyncClient,
  ) -> tuple[str, list[int], dict]:
      """Return (rewritten_query, classified_node_ids, debug_meta)."""
  ```
- Single LLM call asks `klai-fast` to produce BOTH the rewritten query AND a JSON list of taxonomy node IDs the query is "about". Structured output via JSON-mode.
- If the rewriter SPEC ships first without classification: the classification is added as a second field in the same prompt (no new roundtrip).
- Empty list (`[]`) means "no narrowing" — the filter is not applied.

**Coverage-stats fallback**

- Before injecting the filter, the hook checks the KB's tagging coverage:
  - New cached helper `get_kb_taxonomy_coverage(kb_slug, db) -> float` returns `tagged_chunks / total_chunks` for the KB.
  - Cache TTL: 5 minutes per `(org_id, kb_slug)`.
- Fallback rule: if coverage < `KLAI_TAXONOMY_COVERAGE_THRESHOLD` (default `0.30`), the filter is dropped and the query goes through without taxonomy narrowing. Logged as `taxonomy_filter_skipped_low_coverage`.

**Taxonomy tree fetch**

- New helper in retrieval-api `klai-retrieval-api/retrieval_api/services/taxonomy_lookup.py`:
  - `async def get_taxonomy_tree(org_id: int, kb_slug: str, db: AsyncSession) -> list[TaxonomyNode]`
  - Returns the flat list `(id, name, parent_id, depth)` for the LLM to classify against.
  - Cached in process for 60s per `(org_id, kb_slug)`.
- The hook calls this BEFORE the LLM rewrite/classify call.

**Hook integration**

- In `deploy/litellm/klai_knowledge.py`, the existing pre-call hook:
  1. Resolves accessible KBs (existing logic)
  2. Fetches taxonomy tree (new) — falls back to empty tree on failure
  3. Calls `_rewrite_and_classify` with tree (new)
  4. Sets `retrieve_body["query"]` to rewritten query (existing, from SPEC-RAG-QUERY-REWRITE-001)
  5. Sets `retrieve_body["taxonomy_node_ids"]` to classified IDs **iff** coverage >= threshold (new)
- All steps are best-effort: each individual failure falls back to "no narrowing" without blocking retrieval.

**Retrieval-api — filter wiring (already exists, needs verification)**

- `_scope_filter()` in `klai-retrieval-api/retrieval_api/services/search.py` already supports `taxonomy_node_ids` as an optional Qdrant `must` filter.
- Verify: when `taxonomy_node_ids` is non-empty, the filter is `payload.taxonomy_node_ids[ANY] in {requested_ids}` (intersection, not subset).
- Add a unit test that asserts the filter is included when IDs are provided AND skipped when the list is empty.

**Eval comparison**

- Use SPEC-RAG-EVAL-001 harness with `RAG_EVAL_VARIANT=taxonomy_v1`.
- Target on the `knowledge_org` suite (where categorised KBs live):
  - ≥10% improvement in `context_precision`
  - No regression > 5% in `context_recall`
- The `chat` suite is expected to show neutral movement (most chat queries are personal-KB and untagged).

### Out of scope

- **Auto-generating the taxonomy tree.** Customers curate their own taxonomy via the existing portal UI. This SPEC consumes whatever tree is there.
- **Re-classifying existing chunks.** Classification at ingest already runs on all new content. Backfill of legacy untagged content is a separate operator-triggered task (uses the existing `recompute_taxonomy_tags` Procrastinate task, not introduced here).
- **Hierarchical filtering.** Initial implementation does flat-set membership. If the LLM classifies a parent node, children are NOT auto-included. Revisit if eval shows this is a frequent miss.
- **Per-tenant tuning of the coverage threshold.** Use a global default; ops-tunable via env var; per-tenant only if a customer asks.
- **Frontend display of "filtered by topic X".** The chat UI doesn't expose the classification step. Internal debug only via `retrieve_body["raw_query"]` + structured logs.

## Acceptance Criteria (EARS)

- **REQ-1**: WHEN the litellm pre-call hook runs AND the KB taxonomy coverage is ≥ `KLAI_TAXONOMY_COVERAGE_THRESHOLD`, the hook SHALL invoke `_rewrite_and_classify` and inject the resulting `taxonomy_node_ids` into `retrieve_body`.
- **REQ-2**: WHEN coverage < threshold OR the LLM classify call returns `[]` OR fails, retrieval SHALL proceed WITHOUT a `taxonomy_node_ids` filter (no narrowing). The fallback SHALL emit a structured log event.
- **REQ-3**: WHEN `taxonomy_node_ids` is present in `retrieve_body`, retrieval-api SHALL include a `payload.taxonomy_node_ids` ANY-of filter in the Qdrant query, in addition to the existing `org_id` + `kb_slug` filters. The org/kb scoping SHALL NEVER be weakened by this filter.
- **REQ-4**: The classifier SHALL only return node IDs that exist in the fetched taxonomy tree for the KB in question. (Anti-hallucination guard; verified by a unit test asserting the rewriter does not invent node IDs not in the tree.)
- **REQ-5**: Total added latency SHALL be < 100ms p95 ON TOP of SPEC-RAG-QUERY-REWRITE-001's added latency (i.e. the combined budget for both SPECs is < 600ms p95). Achieved by re-using a single LLM call for both rewrite + classify.
- **REQ-6**: Total added per-query token cost SHALL be < 100 tokens ON TOP of SPEC-RAG-QUERY-REWRITE-001 (combined budget < 300 tokens).
- **REQ-7**: Every classify invocation SHALL log a `taxonomy_classify` event with: `org_id`, `kb_slug`, `coverage_ratio`, `classified_node_ids`, `was_applied: bool`, `skip_reason: str | null`.
- **REQ-8**: After deploy, RAGAS metrics with `variant=taxonomy_v1` on the `knowledge_org` suite SHALL show ≥10% improvement in `context_precision` AND no regression > 5% in `context_recall` vs baseline.

## Open Questions (resolve in /plan)

1. **Combine with SPEC-RAG-QUERY-REWRITE-001's call or two roundtrips?** Strongly prefer combine — same model, same context window, JSON-output supports both fields. Decided in plan-phase based on JSON reliability of klai-fast (Mistral small).
2. **Coverage threshold default — 0.30 or 0.50?** 0.30 is permissive (only skip when KB is mostly untagged); 0.50 is conservative (skip if half-untagged). Default 0.30; revisit after eval data.
3. **What about the `chat` (personal) KB?** Personal KBs typically have NO curated taxonomy. The coverage check naturally drops the filter, so the SPEC degrades to no-op. Confirm with a unit test that the personal-KB happy path is identical to baseline.
4. **Filter mode — ANY-of (intersection) vs ALL-of (subset)?** A query classified to nodes `[5, 12]` retrieves chunks tagged with EITHER (`ANY`) or BOTH (`ALL`). Anthropic's pattern uses ANY because a query is rarely about all classified topics simultaneously. Default ANY.
5. **Hierarchical expansion later?** If the LLM consistently classifies the parent node `Beleid` but chunks are tagged with the child `Verlofbeleid`, recall drops. Track in eval; fix in v2 by walking the tree at query-time if the issue is real.

## Estimated effort

3-4 days (assuming SPEC-RAG-QUERY-REWRITE-001 has shipped):

- Day 1: Extend `_rewrite_query` to `_rewrite_and_classify` + JSON-output prompt + unit tests
- Day 2: `taxonomy_lookup.py` + coverage helper + cache + integration into hook
- Day 3: Verify retrieval-api filter wiring + add filter-applied/filter-skipped tests + deploy to staging
- Day 4: Run eval harness with `variant=taxonomy_v1` on `knowledge_org` suite, tune coverage threshold, ship to production

If SPEC-RAG-QUERY-REWRITE-001 has NOT shipped: add 2 days for the standalone `_classify_query` helper + integration. Better to ship rewrite-first.
