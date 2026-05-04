---
id: SPEC-RAG-QUERY-REWRITE-001
version: "0.1.0"
status: draft
created: 2026-05-04
updated: 2026-05-04
author: Mark Vletter
priority: high
related:
  - SPEC-RAG-EVAL-001 (precondition)
roadmap: docs/architecture/retrieval-improvements-roadmap.md
---

# SPEC-RAG-QUERY-REWRITE-001: Query rewriting in the LiteLLM hook

## Summary

Add a lightweight query-understanding layer in `deploy/litellm/klai_knowledge.py` that rewrites or expands the raw user query via `klai-fast` before it goes into `retrieve_body["query"]`. Today the user's literal phrasing — including pronouns, follow-ups without context, vague phrasing — is fed 1-on-1 to the embedding model. Rewriting closes the semantic gap between question style and document style.

Industry baseline for 2026: a query-rewriting layer typically adds **+15-25% precision on vague queries** at a cost of one extra `klai-fast` call (~200ms latency, ~€0.0003 per call).

## Motivation

1. **The most common quality regression is the simplest to fix.** "Wat zei hij over die deal?" doesn't match any document containing the deal — it depends on the previous turn for "hij" and "die deal". A rewrite *"What did Acme's CEO say about the Q4 partnership deal in the August call?"* matches.
2. **No corpus changes needed.** Query rewriting is purely query-side. We can deploy and measure delta in days, no re-embedding, no backfill.
3. **Synergy with SPEC-RAG-CONTEXTUAL-001.** Contextual retrieval makes chunks self-contained; query rewriting makes queries self-contained. The two compound — the bigger the gap they close together, the better the matching.
4. **Foundation for SPEC-RAG-TAXONOMY-001.** The same LLM call can simultaneously produce a rewritten query AND a classification against the KB taxonomy — saving a roundtrip when SPEC-RAG-TAXONOMY-001 lands.

## Scope

### In scope

**LiteLLM hook — query rewriter**

- New helper in `deploy/litellm/klai_knowledge.py`:
  ```python
  async def _rewrite_query(
      raw_query: str,
      conversation_history: list[dict],
      client: httpx.AsyncClient,
  ) -> tuple[str, dict]:
      """Return (rewritten_query, debug_meta). Falls back to raw_query on any error."""
  ```
- Call site: in the pre-call hook, just before `retrieve_body["query"] = query`.
- Substitutes the rewritten query into `retrieve_body["query"]`. The original raw query is preserved in `retrieve_body["raw_query"]` (new field) for downstream logging only — retrieval-api ignores it.
- Conversation history input: last 4 turns (user + assistant). Truncated to ~1000 chars.

**Prompt** (initial draft, refined in plan-phase):

```
You are a query rewriter for a RAG system. Rewrite the user's question
so that it makes sense as a stand-alone search query, resolving pronouns
and references using the conversation history. If the question is
already clear and self-contained, return it unchanged.

Conversation history (last 4 turns):
{history}

User's current question:
{raw_query}

Reply with ONLY the rewritten question, no preamble or explanation.
Maximum 200 characters. Same language as the user's input.
```

LLM model: `klai-fast` (Mistral small). Timeout: 1.5s. On timeout / non-200 / empty response: fall back to `raw_query` and log warning.

**Logging**

- New structured log event `query_rewrite` with fields: `org_id`, `user_id`, `raw_query`, `rewritten_query`, `rewrite_ms`, `was_changed: bool`.
- These flow through the existing structlog pipeline and end up in VictoriaLogs. Useful for retrospective tuning of the prompt.

**Eval comparison**

- Use SPEC-RAG-EVAL-001 harness with `RAG_EVAL_VARIANT=query_rewrite_v1`.
- Target: ≥15% improvement in `context_precision` on the `chat` suite, where queries are typically more conversational.

### Out of scope

- HyDE-style hypothetical answer generation (Tier 3, separate SPEC if metrics demand it)
- Multi-query expansion (generate N alternative queries, run N retrievals, fuse) — adds latency, defer until eval shows the simple rewrite is insufficient
- Query decomposition for multi-hop questions (Tier 3, SPEC-RAG-AGENTIC-001 if it lands)
- Auto-translation (queries in NL retrieve from EN docs) — defer; requires a separate language-detection layer

## Acceptance Criteria (EARS)

- **REQ-1**: WHEN the litellm pre-call hook runs and `query` is non-empty, the hook SHALL invoke `_rewrite_query` and use the result for `retrieve_body["query"]`.
- **REQ-2**: WHEN `_rewrite_query` returns a string identical to the raw query (no rewrite needed) OR the LLM call fails / times out, retrieval SHALL proceed with the raw query without retry.
- **REQ-3**: The rewriter SHALL NOT add information not derivable from the conversation history. ([anti-hallucination guard]; verified by a unit test that asserts the rewriter does not invent entity names not in the input).
- **REQ-4**: Total added latency SHALL be < 500ms p95 (target: 200-300ms typical).
- **REQ-5**: Total added per-query token cost SHALL be < 200 tokens (verified by Mistral usage logs); at klai-fast pricing this is < €0.0005.
- **REQ-6**: Every invocation SHALL log a `query_rewrite` event with the fields listed above.
- **REQ-7**: After deploy, RAGAS metrics with `variant=query_rewrite_v1` on the `chat` suite SHALL show ≥15% improvement in `context_precision` vs baseline.

## Open Questions (resolve in /plan)

1. **Should the rewriter also see the available KB names?** Helps when query mentions "the policy" — rewriter could substitute the actual policy name. But adds tokens and may overfit.
2. **Per-tenant prompt customization?** Some tenants might want domain-specific rewriter prompts (e.g. legal vs medical). Defer until a customer asks.
3. **Conversation history limit** — 4 turns vs 6 vs 10? Each turn is ~50-200 tokens. 4 turns is enough for most pronoun resolution; 10 turns adds cost without clear gain.
4. **What about no-retrieval queries?** Some chat turns are clearly meta ("repeat that", "shorter please"). The hook could detect these and skip rewriting + retrieval entirely. Defer to Tier 3 (agentic routing).

## Estimated effort

2-3 days:
- Day 1: `_rewrite_query` helper + unit tests + prompt iteration on a hand-curated set of 10 vague queries
- Day 2: Integrate into hook, deploy to staging, run eval harness with `variant=query_rewrite_v1`
- Day 3: Tune prompt based on eval delta, deploy to production
