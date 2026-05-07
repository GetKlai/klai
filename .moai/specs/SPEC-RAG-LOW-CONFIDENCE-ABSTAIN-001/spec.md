---
id: SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001
version: "0.1.0"
status: draft
created: 2026-05-07
updated: 2026-05-07
author: Mark Vletter
priority: high
related:
  - SPEC-RAG-EVAL-001 (acceptance criteria depend on its harness)
  - SPEC-RAG-CONTEXTUAL-001 (REQ-6 audits parity with sparse-input)
  - SPEC-RAG-QUERY-REWRITE-001 (REQ-5 extends its prompt)
  - SPEC-RAG-PARENT-CHILD-001 (link-expand boost interacts with parent expansion)
roadmap: docs/architecture/retrieval-improvements-roadmap.md
---

# HISTORY

| Version | Date       | Author       | Change         |
|---------|------------|--------------|----------------|
| 0.1.0   | 2026-05-07 | Mark Vletter | Initial draft  |

---

# SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001: Low-Confidence Abstention + Brand-Bridging

## Summary

Add a low-confidence detection and abstention layer on top of the existing Tier 1+2 retrieval stack. When `max(reranker_scores_top5)` falls below a threshold, the system MUST mark the response as `confidence_band: low` and the litellm-hook MUST inject an anti-hallucination instruction so the model stops fabricating routes that are not in the retrieved chunks. Combined with five smaller fixes that the diagnostic of the 2026-05-07 Voys-Salesforce conversation surfaced as missing or under-utilised: brand-bridging in the existing rewrite prompt, sparse-input parity audit, top-K from 5 to 20, link-expand reranker boost, and a regression-canary expansion of the chat-suite.

This SPEC is intentionally narrow. Tier 3 (HyDE, GraphRAG, Agentic RAG) remains deferred until 4 weeks of post-launch traces are available, per the existing roadmap. Nothing in this SPEC pre-empts that gate.

## Motivation

The 2026-05-07 19:30 UTC Voys-Salesforce chat showed a failure mode the current stack does not prevent:

1. The user asked *"Ik wil Voys Freedom graag koppelen aan Salesforce. Hoe werkt dat?"*
2. Retrieval returned 5 chunks with `max(reranker_scores_top5) = 0.18` — effectively noise. Top-1 was the `/integraties` index page; the top-5 included unrelated pages (`/klik-en-bel-extensie`, Notion `nummerovername`, `/livekit`).
3. The chat answered with TL;DR *"WhatsApp-integratie of Zapier"* — neither claim is supported by any retrieved chunk. Pure hallucination.
4. Only when the user typed "Bubble" in turn 2 did `max(reranker_scores_top5)` jump to 0.85 and the correct `/bubble` page surfaced as top-1.

Three diagnostic facts shaped this SPEC:

- **Tier 1+2 is already shipped and live on Voys.** Contextual chunks, parent-child, query-rewrite-and-classify, taxonomy classifier — all running on this query. The failure is not a missing retrieval-stack layer.
- **Vocabulary-gap is the root cause.** Brand `Salesforce` does not appear lexically on the Voys help-center pages that explain the integration; those pages lead with `Bubble` / `RedCactus` / `CRM-pakket`. BGE-M3 dense + sparse + reranker do not bridge `Salesforce ↔ Bubble`. The graph-search returns 0 hits because Graphiti's NER did not extract `Salesforce` as an entity (it appears only in list constructions like *"CRM-systemen zoals Salesforce, HubSpot, Zendesk"*). Confirmed via Cypher on the live FalkorDB.
- **Insufficient context amplifies hallucination.** Google's sufficient-context research shows that adding noisy context can move hallucination rates from ~10% (no context) to ~66% (insufficient context). The current behaviour at `max(reranker)=0.18` is exactly that regime: the model trusts what it gets and improvises the rest.

Three references underpin the chosen fixes:

- Anthropic Contextual Retrieval: *"Top-20 chunks proved more effective than top-5 or top-10"*. Klai's hook ships `top_k=5`.
- Anthropic Contextual BM25: contextual prefix MUST be in both the embedding-input AND the BM25 (sparse) index for the full 49% → 67% reduction. Reranker-input parity is confirmed in code; sparse-input parity is not yet confirmed.
- Allganize and Palo Alto Networks engineering posts on synonym-aware RAG identify brand → category bridging as the dominant vocabulary-gap failure mode in enterprise KBs.

## Scope

### In scope

**Retrieval-api**

- New `confidence_band` field on the `/retrieve` response, computed from `max(reranker_scores_top5)`.
- Reranker boost for chunks tagged `_link_expanded=True` so the link-expansion feature actually surfaces neighbours in top-K (today: never).
- Sparse-input audit: verify the BGE-M3 sparse-vector input includes `context_prefix + chunk_text`, with a fix if it currently uses `chunk_text` alone.
- Prometheus counters for band distribution and link-expansion-survival rate.

**LiteLLM hook**

- Anti-hallucination prompt-injection when retrieval returns `confidence_band: low`.
- Brand → category bridging extension to `_QUERY_REWRITE_AND_CLASSIFY_PROMPT` so queries mentioning third-party products also include broader category terms in the rewritten query.
- Raise `KNOWLEDGE_RETRIEVE_TOP_K` from `5` to `20` (env var change), with eval delta measured.

**Eval harness**

- 5–10 regression canaries added to `chat.yaml` covering the brand-bridging failure class (`Voys ↔ Salesforce`, `Voys ↔ HubSpot`, `Voys ↔ Pipedrive` — for the brand-NOT-as-entity variants, distinct from the existing easy-lookup `Pipedrive integratie` query that the brand IS an entity for).

**Observability**

- Grafana panel: confidence-band distribution per day, anti-hallucination injection rate, link-expand-survival rate.

### Out of scope

- LLM-based sufficient-context judge (Google selective-generation pattern). Defer until band-proxy measurably plateaus.
- Iterative retry loop on `band: low` (CRAG-style web-search fallback or HyDE re-search). Defer to Tier 3 — the always-on rewrite already gives the LLM one shot at brand-bridging, and a second rewrite with the same model rarely diverges.
- Graphiti NER improvement so that brand mentions in list constructs become entities. Tracked separately as a knowledge-ingest concern (REQ-7 only adds the regression canaries; the underlying graph-NER fix is not part of this SPEC).
- Quality-feedback cold-start threshold change (3 → 1). Single-line config change, no SPEC needed, ships as a standalone PR.
- Rate-limiter Redis URL parsing fix (the `redis-url-password-must-be-parsed-manually` HIGH pitfall). Bug fix, ships as a standalone PR.

## Functional Requirements (EARS)

### REQ-1 — confidence_band emit (ubiquitous)

**THE retrieval-api SHALL** include a `confidence_band` field in every successful `/retrieve` response, computed from the top-5 reranker scores after quality-floor filtering and source-aware selection. Mapping:

- `high` when `max(reranker_scores_top5) >= 0.60`
- `medium` when `0.30 <= max(reranker_scores_top5) < 0.60`
- `low` when `max(reranker_scores_top5) < 0.30`
- `unknown` when reranker is disabled, falls back, or the served list is empty

The same value MUST also be written to the `retrieval_decision_record` log event for offline analysis.

**Defaults rationale:** `0.60` and `0.30` chosen from the observed Voys-Salesforce conversation deltas (turn 1: 0.18 = low / hallucinated; turn 3 rekeningnummer: 0.96 = high). Configurable in `retrieval_api.config` as `confidence_band_high_threshold` and `confidence_band_low_threshold`.

### REQ-2 — anti-hallucination prompt-injection on band=low (event-driven)

**WHEN** the litellm-hook receives `/retrieve` results with `confidence_band: low` OR `confidence_band: unknown`, **THE litellm-hook SHALL** inject an additional system-message segment (Dutch, single block, no template variables exposed to the chat completion) instructing the model to:

- cite only facts that are literally present in the retrieved chunks;
- explicitly refuse to invent integration routes, products, or steps not present;
- end the response with a request for clarification when the chunks do not cover the question.

The injection MUST be append-only (after the existing system prompt + retrieved chunks), MUST NOT remove or rewrite any existing chunk content, and MUST NOT trigger when `confidence_band: high` or `medium`.

The injected text is owned by the hook (not retrieval-api) so it can be tuned without redeploying retrieval-api.

### REQ-3 — link-expand reranker boost (state-driven)

**WHILE** `link_expand_enabled == true`, **THE retrieval-api SHALL** apply a multiplicative boost to the reranker score of any chunk whose `_link_expanded == True` flag is set, capped so the boosted score never exceeds 1.0. The boost factor MUST be configurable in `retrieval_api.config` as `link_expand_score_boost` (default `1.10`, range `1.00` – `1.30`).

The boost MUST apply BEFORE the source-aware selection and quality-boost passes, so link-expanded chunks compete fairly for the served top-K. After this requirement lands, the existing `link_expand.expanded_in_top_k` metric MUST rise above 0 on Voys traces (today: 0 across all observed conversations).

### REQ-4 — top_k from 5 to 20 in production hook (ubiquitous, config-only)

**THE litellm-hook SHALL** request `top_k=20` from `/retrieve` (configurable via env `KNOWLEDGE_RETRIEVE_TOP_K`, default raised from `5` to `20`). The hook MUST continue to truncate the retrieved chunks to the model's context-window budget; this requirement only changes the retrieval-api request, not the model-prompt construction.

Eval delta on `chat.yaml` MUST be reported in the merge PR description: `context_precision`, `context_recall`, and per-query token-cost change vs. `top_k=5`.

### REQ-5 — brand → category bridging in the rewrite prompt (event-driven)

**WHEN** the user query mentions a third-party brand or product name (e.g. `Salesforce`, `HubSpot`, `Pipedrive`, `Zoom`, `Microsoft Teams`), **THE litellm-hook's `_QUERY_REWRITE_AND_CLASSIFY_PROMPT` SHALL** include 2–4 broader category or related-brand terms in the rewritten query, so the downstream retrieval can find category-specific or partner-brand pages even when the original brand string is absent from the source content.

Implementation: extend the prompt with explicit instruction text and 3 in-context examples covering CRM (`Salesforce → CRM-koppeling, Bubble, RedCactus`), video conferencing (`Zoom → vergader-integratie, telefoonkoppeling`), and a non-CRM example (`Outlook → e-mailkoppeling, agenda-integratie`).

The rewrite MUST stay within the existing `max 200 chars` JSON contract and MUST NOT change the existing taxonomy_node_ids classification step.

### REQ-6 — sparse-input parity audit (unwanted-behaviour)

**IF** `klai-knowledge-ingest`'s sparse-vector pipeline currently embeds the raw chunk text alone (without prepended `context_prefix`), **THE knowledge-ingest pipeline SHALL** be updated to use the same `context_prefix + chunk_text` assembly that the dense embedder and reranker already use ([reranker.py:31-44](klai-retrieval-api/retrieval_api/services/reranker.py#L31-L44)).

Verification artefact: a unit test in `klai-knowledge-ingest/tests/` that asserts `embed_sparse` is called with the contextualised string for any chunk that has a non-empty `context_prefix`, AND a one-shot integration check on a 10-chunk sample that the sparse-vector indices change between with-prefix and without-prefix inputs (proves the prefix is actually reaching the embedder).

If the audit shows parity already exists, this requirement closes with a one-line "verified" entry in the SPEC's HISTORY section and no code change.

### REQ-7 — regression canaries for brand-bridging class (ubiquitous)

**THE chat-suite at `klai-knowledge-ingest/knowledge_ingest/eval/suites/chat.yaml` SHALL** include 5–10 new regression queries covering the brand-bridging failure class. Minimum coverage:

- 1 query mentioning `Salesforce` (the canary from the 2026-05-07 incident)
- 1 query mentioning `HubSpot`
- 1 query mentioning `Microsoft Teams` (or another non-CRM third-party brand present in the Voys KB)
- 1 query mentioning a brand that is NOT in Voys's KB (negative-class canary — should produce `confidence_band: low` and a clarifying-question response)
- 1 query that uses both brand and category terms in the original phrasing (positive-class control — should NOT trigger band=low)

Each query MUST have an `expected_topics` field; queries with discoverable expected chunks MUST also have `expected_chunks`.

### REQ-8 — observability (ubiquitous)

**THE retrieval-api and litellm-hook SHALL** expose Prometheus counters and one Grafana panel:

- `retrieval_confidence_band_total{band, org_id}` (counter, retrieval-api)
- `retrieval_link_expand_top_k_total{outcome=hit|miss, org_id}` (counter, retrieval-api)
- `litellm_low_confidence_injection_total{org_id, reason}` (counter, hook; `reason ∈ {band_low, band_unknown}`)

Grafana panel `RAG Quality > Low-Confidence` showing 7-day rolling distribution of `confidence_band` per tenant, with the existing `RAGAS metrics` panels unchanged. Panel MUST include an alert rule `low_confidence_served_rate` that fires when `(band=low + band=unknown) / total > 0.20` over 1h per tenant — this is the signal that the KB has coverage gaps the SPEC's anti-hallucination layer is masking and follow-up content work is needed.

## Non-Functional Requirements

- **Latency**: end-to-end p95 retrieve latency (retrieval-api side) MUST NOT increase by more than 10% vs. the pre-SPEC baseline. Most additions are constant-time computations; the `top_k` increase from 5 to 20 affects payload size and reranker output, not latency in any meaningful way (reranker already scores 20 candidates per `reranker_candidates: int = 20` config).
- **Token cost**: per-query LLM cost MUST NOT increase by more than 15% vs. pre-SPEC. The dominant change is the `top_k=5→20` expansion, which adds ~15 chunks × ~500 tokens ≈ 7.5k extra prompt tokens per call. On `klai-primary` (Mistral Large) this is ≤ 3¢ per call. Stays well under the roadmap's 30% combined-stack cap.
- **Fail-open**: every new layer (band emit, prompt injection, link-expand boost, brand-bridging rewrite) MUST degrade to current behaviour on any failure path. No new hard failure modes.
- **Backwards compatibility**: legacy chunks without `_link_expanded` flag, without `context_prefix`, or without `feedback_count` MUST continue to be served unchanged. The boost (REQ-3) and the injection (REQ-2) MUST not raise on missing fields.
- **Multi-tenant**: every metric label MUST include `org_id`; no global rollups that hide per-tenant regressions.

## Acceptance Criteria

| AC ID | Test                                                                                                                                                                       | Expected outcome                                                                                                                                                                                                       |
|-------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| AC-1  | Replay turn 1 of the 2026-05-07 19:30 Voys-Salesforce conversation against retrieval-api                                                                                   | `confidence_band: low` returned                                                                                                                                                                                       |
| AC-2  | Same replay, end-to-end via litellm-hook                                                                                                                                   | Anti-hallucination injection present in the system prompt; chat response contains a clarifying-question phrase OR an explicit "ik vind hier weinig over" disclaimer; response does NOT contain `WhatsApp` or `Zapier` |
| AC-3  | Replay with REQ-5 brand-bridging in place; rewrite-prompt output for the original Voys-Salesforce query                                                                    | Rewritten query contains at least one of: `CRM`, `CRM-koppeling`, `Bubble`, `RedCactus`                                                                                                                                |
| AC-4  | Same conversation, after REQ-3 + REQ-4 + REQ-5 land; `chat.yaml` regression canary `chat-brand-salesforce-bridging`                                                       | `context_precision >= 0.50` (vs. baseline ~0.05 estimated from `max-rerank=0.18`); top-K serving includes at least one `bubble` or `redcactus` URL                                                                      |
| AC-5  | Existing `chat.yaml` non-brand-bridging queries (e.g. `chat-easy-bubble-troubleshoot`, `chat-easy-yealink-firmware`)                                                       | Aggregate `context_precision` and `context_recall` MUST NOT regress by more than 0.02 vs. pre-SPEC `post_pr_abcdefg_v1` baseline                                                                                       |
| AC-6  | `link_expand.expanded_in_top_k` Prometheus counter on Voys org over 7 days post-deploy                                                                                     | Survival rate (`hit / (hit + miss)`) > 0.10                                                                                                                                                                            |
| AC-7  | Sparse-input audit (REQ-6)                                                                                                                                                  | Either: (a) a unit test passes proving sparse-embed sees `context_prefix + chunk_text` for non-null prefixes, OR (b) the SPEC HISTORY entry `verified parity, no code change`                                          |
| AC-8  | Negative-class canary (`chat-brand-not-in-kb`)                                                                                                                              | `confidence_band: low` returned; chat response includes clarifying-question; response does NOT fabricate steps                                                                                                         |
| AC-9  | Latency p95 on retrieve over 24 h post-deploy                                                                                                                                | ≤ pre-SPEC p95 + 10%                                                                                                                                                                                                  |
| AC-10 | Grafana panel `RAG Quality > Low-Confidence` shows non-zero counts in all three bands within 24 h of deploy                                                                  | Panel renders, three series present, alert rule loaded                                                                                                                                                                 |

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Threshold defaults (`0.60` / `0.30`) are wrong for Klai's score distribution and cause the injection to fire too often (or too rarely) | medium | medium | Defaults are based on a single observed conversation. Mitigation: ship as configurable values; tune after 7 days of production traces using the new Grafana panel. Re-deploy is config-only. |
| Brand-bridging prompt-tweak adds tokens that push `klai-fast` rewrite latency over the existing per-call budget | low | medium | The added prompt is ~150 tokens (instruction + 3 examples). Existing `_QUERY_REWRITE_AND_CLASSIFY_PROMPT` is ~600 tokens. < 25% growth on a klai-fast call that completes in ~400ms today. Acceptance criterion: rewrite-call p95 must not exceed 600ms after deploy. |
| `top_k=20` causes regression on the `chat.yaml` faithfulness metric (more chunks → more noise → lower faithfulness) | low | high | This is exactly what the eval harness is for. PR description MUST include faithfulness delta. If `faithfulness` drops below the existing 0.85 alert threshold, REQ-4 reverts to `top_k=10` (compromise) or `top_k=5` (rollback) and the SPEC HISTORY records the result. No silent ship. |
| Anti-hallucination injection causes the LLM to abstain on queries where confidence is genuinely high but the hard threshold mis-fires | medium | low | Injection only fires on `band ∈ {low, unknown}`; `medium` is permissive. Plus AC-5 requires no aggregate regression on the existing 30 chat queries. |
| Link-expand boost surfaces noise (boosted neighbours that the reranker correctly demoted) | medium | medium | Boost is multiplicative and capped at 1.0. Default 1.10 is mild — flips a chunk's rank only when it is already close to the top. AC-5 catches aggregate regression. Per-tenant Grafana panel catches per-tenant regression. |
| Sparse-input parity is already broken AND fixing it changes the eval baseline mid-SPEC | medium | medium | If REQ-6 audit reveals sparse-input was missing `context_prefix`, a separate eval-variant `contextual_v1_sparse_fix` must be captured BEFORE measuring REQ-1/2/3/4/5 deltas — otherwise the retrieval-pipeline delta and the chunk-pipeline delta confound. |

## Sources

Research underpinning the requirements:

- [Contextual Retrieval — Anthropic](https://www.anthropic.com/news/contextual-retrieval) — top-K=20 finding (REQ-4), contextual BM25 stacking (REQ-6).
- [Deeper insights into RAG: The role of sufficient context — Google Research](https://research.google/blog/deeper-insights-into-retrieval-augmented-generation-the-role-of-sufficient-context/) — insufficient-context-amplifies-hallucination (REQ-2 motivation).
- [Corrective Retrieval Augmented Generation — arXiv 2401.15884](https://arxiv.org/abs/2401.15884) — three-bucket evaluator pattern (REQ-1).
- [RAG Synonyms: Why Enterprise Search Misses Half of the Match — Allganize](https://www.allganize.ai/en/blog/rags-long-standing-challenge-synonyms) — vocabulary-gap as dominant failure mode (REQ-5).
- [Bridging the Language Gap: Synonym-Aware RAG — Palo Alto Networks engineering blog](https://live.paloaltonetworks.com/t5/engineering-blogs/bridging-the-language-gap-our-journey-to-a-synonym-aware-rag/ba-p/1236616) — production pattern for brand → category mapping (REQ-5).

Internal references:

- [docs/architecture/retrieval-improvements-roadmap.md](docs/architecture/retrieval-improvements-roadmap.md) — Tier 1+2 shipped state, Tier 3 deferral conditions.
- [klai-retrieval-api/retrieval_api/api/retrieve.py](klai-retrieval-api/retrieval_api/api/retrieve.py) — pipeline stages where REQ-1/REQ-3 land.
- [klai-retrieval-api/retrieval_api/services/reranker.py](klai-retrieval-api/retrieval_api/services/reranker.py#L31-L44) — context_prefix parity reference for REQ-6.
- [deploy/litellm/klai_knowledge.py](deploy/litellm/klai_knowledge.py) — hook-side surface for REQ-2/REQ-4/REQ-5.
- [klai-knowledge-ingest/knowledge_ingest/eval/suites/chat.yaml](klai-knowledge-ingest/knowledge_ingest/eval/suites/chat.yaml) — REQ-7 destination.
