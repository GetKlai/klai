---
id: SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001
plan_for: spec.md
created: 2026-05-07
updated: 2026-05-07
author: Mark Vletter
---

# Implementation Plan — SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001

## 0. Pre-flight check

Before opening the worktree:

1. Confirm Tier 1+2 deploy is healthy on Voys (`docs/architecture/retrieval-improvements-roadmap.md` — `post_pr_abcdefg_v1` row in `knowledge.rag_eval_results`).
2. Confirm `klai-retrieval-api/evaluation/eval_runner.py` AND `klai-knowledge-ingest/knowledge_ingest/eval/ragas_runner.py` both run end-to-end on the chat-suite from a developer machine.
3. Capture a fresh `pre_spec_baseline_v1` row (one full run of `chat.yaml`) on the morning the SPEC begins. This is the comparison reference for AC-4 and AC-5.

If any of the three fails, fix that first; the SPEC is gated on a working measurement layer.

## 1. Worktree setup

```bash
git worktree add ../klai-low-confidence-abstain -b feature/SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 main
cd ../klai-low-confidence-abstain
```

Single worktree because the SPEC touches three services (retrieval-api, litellm-hook, knowledge-ingest) but no migrations and no deploy-compose changes. Sequential foreground work — no parallel teammates needed.

## 2. Architecture decisions

- **Threshold defaults configurable, not hard-coded.** `confidence_band_high_threshold` and `confidence_band_low_threshold` live in `retrieval_api.config.Settings` so post-deploy tuning is an env-var change, not a redeploy.
- **Anti-hallucination text owned by the hook.** Rationale: prompt-tuning iterations are far more frequent than retrieval-api releases. Putting the text behind retrieval-api would couple iteration cycles. The hook already has a stable contract surface (`_QUERY_REWRITE_AND_CLASSIFY_PROMPT`) that this slots next to.
- **Brand-bridging via in-context examples, not a brand dictionary.** The dictionary path was already rejected ("we gaan niet handmatig per tenant onderhouden"). Three in-context examples in the prompt (CRM, video conferencing, e-mail/agenda) let `klai-fast` generalise to brands the examples don't cover, while staying YAML-free and tenant-agnostic.
- **No retry loop.** Adding a second pass with the same `klai-fast` model after the first rewrite produces near-identical output; only a different strategy (HyDE) would diverge meaningfully, and that is Tier 3. Documented as deliberate omission in spec.md "Out of scope".
- **Link-expand boost is multiplicative + capped at 1.0.** Additive boosts can flip ranks across the whole top-K; multiplicative-with-ceiling only flips chunks already near the top. Lower regression risk on the existing 30-query suite.
- **Sparse-input audit ships unit-test-first.** REQ-6 may close as "no change required" if parity already exists; the unit test is what closes it either way.

## 3. Implementation Units

Six units, executed in dependency order. Each is a separate commit (or commit cluster).

### Unit 1 — Sparse-input parity audit (REQ-6)

**Why first**: changes to retrieval-pipeline thresholds (REQ-1, REQ-3) and to top_k (REQ-4) all interact with what the sparse-vector leg of the 3-leg RRF is matching against. If the sparse leg is missing `context_prefix`, the pre/post measurement will confound the rest of this SPEC. So: audit first, fix-or-confirm, baseline, then continue.

Files to inspect:

- `klai-knowledge-ingest/knowledge_ingest/sparse_embedder.py` — call sites + arguments
- `klai-knowledge-ingest/knowledge_ingest/qdrant_store.py:317` (where `context_prefix` is written to payload) — confirm whether the same value flows to the sparse embedder upstream
- `klai-knowledge-ingest/knowledge_ingest/enrichment.py:435` (`enriched_text = f"{result.context_prefix}\n\n{chunk_text}"`) — confirm whether `enriched_text` is what gets passed to the sparse embedder, or whether the sparse leg gets raw `chunk_text` unchanged

Outcomes:

- **(a) parity already exists**: write a unit test that documents this (asserts `embed_sparse` receives the contextualised string), commit with message `test(rag): document sparse-input contextual parity (REQ-6)`. Update `spec.md` HISTORY: `0.1.1 — REQ-6 verified, no code change required`.
- **(b) parity missing**: change the sparse-embedder call site to receive `enriched_text`. Add unit test. Add a one-shot integration check on a 10-chunk sample (sparse-vector indices differ between with-prefix and without-prefix inputs). If a re-index is needed for existing Voys data, defer to the existing `rebuild_kb` operator runbook — DO NOT in-line a re-index in this SPEC. Track the re-index as a separate operator action.

### Unit 2 — confidence_band emit + thresholds (REQ-1)

Files:

- `klai-retrieval-api/retrieval_api/config.py` — add `confidence_band_high_threshold: float = 0.60` and `confidence_band_low_threshold: float = 0.30`. Add validation: `low < high`, `0 <= low`, `high <= 1`.
- `klai-retrieval-api/retrieval_api/models.py` — add optional `confidence_band: Literal["high", "medium", "low", "unknown"] | None = None` to `RetrieveResponse`.
- `klai-retrieval-api/retrieval_api/api/retrieve.py` — compute band from `decision_record["reranker_scores_top5"]` AFTER quality-floor + source-aware-select + quality-boost passes. Write to both response and `decision_record`. Edge cases: empty served list → `unknown`; reranker disabled → `unknown`; reranker fallback (any score is `None`) → `unknown`.
- `klai-retrieval-api/retrieval_api/metrics.py` — add `retrieval_confidence_band_total` counter labelled `band` and `org_id`.

Tests:

- `klai-retrieval-api/tests/test_confidence_band.py` — new — covers all four bands, empty list, reranker-disabled, reranker-fallback, threshold misconfiguration rejection.

### Unit 3 — link-expand reranker boost (REQ-3)

Files:

- `klai-retrieval-api/retrieval_api/config.py` — add `link_expand_score_boost: float = 1.10` with validator `1.00 <= x <= 1.30`.
- `klai-retrieval-api/retrieval_api/api/retrieve.py` — apply boost to `reranker_score` for chunks where `_link_expanded == True`, BEFORE source-aware selection and quality-boost. Cap boosted value at `1.0`. Skip if `link_expand_enabled == false`.
- `klai-retrieval-api/retrieval_api/metrics.py` — add `retrieval_link_expand_top_k_total` counter labelled `outcome` (`hit` / `miss`) and `org_id`. Increment after the served list is finalised: `hit` when at least one expanded chunk made the served top-K, `miss` otherwise (only count requests where `link_expand_count > 0`).

Tests:

- `klai-retrieval-api/tests/test_link_expand_boost.py` — new — boost is multiplicative, cap at 1.0, no-op when disabled, no-op on chunks without flag, ordering effect verified on a synthetic 20-chunk fixture.
- Update `klai-retrieval-api/tests/test_link_expand_retrieve.py` (existing) — assert at least one expanded chunk survives top-K with boost enabled in the fixture.

### Unit 4 — anti-hallucination prompt-injection (REQ-2)

Files:

- `deploy/litellm/klai_knowledge.py` — receive `confidence_band` from the retrieval response; when `band ∈ {low, unknown}`, append a Dutch system-message segment to the chat-completion prompt. Single block, no template placeholders exposed to the user.
- `deploy/litellm/klai_knowledge.py` — add Prometheus-counter increment for `litellm_low_confidence_injection_total` labelled `org_id` and `reason` (`band_low` / `band_unknown`).

Anti-hallucination text (initial; tunable post-deploy):

```
[Klai retrieval — lage relevantie]
Het opgehaalde KB-materiaal heeft een lage relevantie-score voor deze
vraag. Citeer alleen wat letterlijk in de chunks staat. Verzin geen
integratie-routes, productnamen, of stappen die niet expliciet in de
chunks voorkomen. Sluit af met een vraag om verduidelijking als het
materiaal de vraag niet volledig dekt.
```

Tests:

- New: a unit test that constructs a fake `/retrieve` response with `confidence_band: low` and asserts the injected segment is present in the outgoing chat-completion request payload.
- New: a unit test asserting NO injection on `band: high` and `band: medium`.

### Unit 5 — top_k 5→20 + brand-bridging rewrite (REQ-4 + REQ-5)

REQ-4 is a one-line env-var change; REQ-5 is a prompt extension. They ship together because measurement of REQ-5's effect requires REQ-4's wider candidate set (Anthropic's own delta numbers assume top-20).

Files:

- `deploy/litellm/klai_knowledge.py:253` — change `RETRIEVE_TOP_K = int(os.getenv("KNOWLEDGE_RETRIEVE_TOP_K", "5"))` default to `20`.
- `klai-infra/core-01/.env.sops` — add `KNOWLEDGE_RETRIEVE_TOP_K=20` (or remove explicit override if it was set to 5; default-of-20 takes effect). Follow standard SOPS workflow per `.claude/rules/klai/infra/sops-env.md`. **Pre-flight per `validator-env-parity` HIGH pitfall**: confirm the env value is set before the code default lands, OR that the code default of 20 alone is sufficient.
- `deploy/litellm/klai_knowledge.py` `_QUERY_REWRITE_AND_CLASSIFY_PROMPT` (around line 465–485 in the current file) — extend the rewrite instruction with one paragraph + 3 in-context examples:

```
3. If the question mentions a third-party brand or product name (e.g.
Salesforce, HubSpot, Pipedrive, Zoom, Microsoft Teams), ALSO include
2–4 broader category or related-brand terms in the rewritten query so
the search can find category-specific or partner-brand pages even
when the original brand string is absent from the source content.
Keep total length within 200 chars.

Examples:
- "Hoe koppel ik Voys aan Salesforce?" → "Voys Salesforce CRM-koppeling Bubble RedCactus"
- "Ondersteunen jullie Zoom?" → "Voys Zoom vergader-integratie telefoonkoppeling"
- "Werkt Outlook met Voys?" → "Voys Outlook e-mailkoppeling agenda-integratie"
```

Tests:

- `deploy/litellm/tests/test_klai_knowledge.py` (or wherever the hook tests live) — add a unit test that mocks the `klai-fast` LLM and asserts the prompt sent to it includes the new instruction segment AND the three examples.
- A live-LLM test (run manually pre-merge, NOT in CI) that calls `klai-fast` with the production prompt + the Voys-Salesforce query and asserts the rewritten output contains at least one of `CRM`, `koppel`, `Bubble`, `RedCactus`. Documented in PR description, not committed as a CI gate.

### Unit 6 — regression canaries + observability (REQ-7 + REQ-8)

Files:

- `klai-knowledge-ingest/knowledge_ingest/eval/suites/chat.yaml` — append 7 queries (5 minimum + 2 negative-class). Each query has `id`, `org_zitadel_id: 368884765035593759`, `query`, `expected_topics`, optional `expected_chunks`, `mix: brand_bridging` (new mix-tag).
- `deploy/grafana/provisioning/dashboards/rag-quality.json` — add `Low-Confidence` panel section with three series (band counter), one series for link-expand survival, one series for low-confidence-injection rate. All split by tenant.
- `deploy/grafana/provisioning/alerting/rag-eval-rules.yaml` — add `rag_low_confidence_served_rate` HIGH alert: `(band_low + band_unknown) / total > 0.20` over 1h, per-tenant.
- `docs/runbooks/rag-quality.md` — extend with a "Low-confidence served rate alert" section documenting the response (check whether the canary class shifted, whether KB coverage gap exists, how to tune thresholds).

Tests:

- `klai-knowledge-ingest/tests/test_chat_suite_structure.py` (or extend an existing structural test) — assert the new queries parse, have required fields, and that the `mix: brand_bridging` tag appears for at least 5 entries.
- Smoke-run: one `python -m knowledge_ingest.eval --suite chat --variant low_confidence_v1` execution from the worktree, captured in PR description as the SPEC-completion baseline for AC-4 and AC-5.

## 4. File-impact summary

```
klai-retrieval-api/
  retrieval_api/
    config.py                    # +3 settings (REQ-1, REQ-3)
    models.py                    # +1 RetrieveResponse field (REQ-1)
    metrics.py                   # +2 counters (REQ-1, REQ-3)
    api/retrieve.py              # band emit + boost (REQ-1, REQ-3)
  tests/
    test_confidence_band.py      # NEW
    test_link_expand_boost.py    # NEW
    test_link_expand_retrieve.py # extend (REQ-3)

klai-knowledge-ingest/
  knowledge_ingest/
    eval/suites/chat.yaml        # +7 regression queries (REQ-7)
  tests/
    test_sparse_input_parity.py  # NEW (REQ-6)
    test_chat_suite_structure.py # extend (REQ-7)

deploy/
  litellm/klai_knowledge.py      # injection + rewrite-prompt + top_k (REQ-2, REQ-4, REQ-5)
  litellm/tests/                 # NEW unit tests for injection + rewrite
  grafana/provisioning/dashboards/rag-quality.json     # +1 panel (REQ-8)
  grafana/provisioning/alerting/rag-eval-rules.yaml    # +1 alert (REQ-8)

klai-infra/                      # SOPS env update (REQ-4)
  core-01/.env.sops              # KNOWLEDGE_RETRIEVE_TOP_K=20

docs/
  runbooks/rag-quality.md        # +1 section (REQ-8)

NO migrations, NO docker-compose changes, NO new services.
```

## 5. Sequencing rationale

- **Unit 1 first** because the audit outcome can shift the rest of the SPEC's measurement baseline. Cheapest single test.
- **Unit 2 (band emit) second** because Units 3 and 4 both consume the band signal. Without it they have no trigger.
- **Unit 3 (link-expand boost) third**, before the hook changes, because retrieval-api regressions need to surface against pre-hook-change baseline (the eval harness bypasses the hook).
- **Unit 4 (hook injection) fourth** consumes Unit 2's response field.
- **Unit 5 (top_k + brand-bridging)** ships together because Unit 5's measurement needs Unit 4 to be live (otherwise low-confidence cases keep producing the same hallucinated answers and brand-bridging looks ineffective in chat-suite end-to-end traces).
- **Unit 6 last** because it sets up the measurement baselines and observability for everything above.

## 6. Pre-merge checklist

- [ ] All 9 acceptance criteria verified (see `acceptance.md`)
- [ ] `pre_spec_baseline_v1` and `low_confidence_v1` rows captured in `knowledge.rag_eval_results`
- [ ] Per-metric delta in PR description: `context_precision`, `context_recall`, `faithfulness`, `answer_relevance`, latency p95, token-cost per query
- [ ] No regression > 0.02 on aggregate `chat.yaml` non-brand-bridging queries (AC-5)
- [ ] Voys-Salesforce regression canary `chat-brand-salesforce-bridging` `context_precision >= 0.50` (AC-4)
- [ ] Grafana panel `RAG Quality > Low-Confidence` renders with three band series and one alert rule loaded
- [ ] Sparse-input audit closed with either a code change + test OR a HISTORY entry "verified, no change"
- [ ] Manual end-to-end test through the litellm-hook with the original Voys-Salesforce question, captured as a screenshot or log excerpt in the PR — confirming injection text appears AND chat does not contain `WhatsApp` or `Zapier`

## 7. Known unknowns / pending decisions

These are intentionally NOT settled in the SPEC; they get resolved during implementation, with the outcome recorded in HISTORY:

1. **Threshold values 0.60 / 0.30**: based on a single conversation. After 7 days of production traces, we may discover the right defaults are 0.50 / 0.25, or that low-band fires too often and 0.20 is the better cutoff. The SPEC ships the configurable knobs; the right values are post-deploy tuning, not pre-deploy proof.
2. **Boost factor 1.10**: same as above. Capped, configurable. If link-expand survival rate stays at 0% post-deploy, raise to 1.20. If it floods top-K with false positives, lower to 1.05.
3. **Brand-bridging examples**: 3 examples ship initially. If `klai-fast` over-applies the bridging on queries that don't mention third-party brands, add a fourth negative example demonstrating "leave alone if no brand".

## 8. Out-of-band PRs accompanying (not part of this SPEC)

These ship as separate PRs, on the SPEC's deploy date or earlier — they do not require a SPEC of their own:

- **PR-A**: rate-limiter Redis URL parsing fix (`klai-retrieval-api/retrieval_api/services/rate_limit.py:47`). Pure bug fix per the `redis-url-password-must-be-parsed-manually` HIGH pitfall in `.claude/rules/klai/pitfalls/process-rules.md`.
- **PR-B**: quality-feedback cold-start threshold `_COLD_START_MIN_VOTES = 3 → 1` (`klai-retrieval-api/retrieval_api/quality_boost.py:14`). Single-line change with a test update; rationale in the file's existing comment block.

Both PRs are referenced (not blocked-on) by this SPEC's PR description for context completeness.

## 9. Rollback

Every requirement has fail-open semantics, so a SPEC-wide rollback is rarely needed. Per-requirement rollback paths:

- REQ-1 / REQ-3 / REQ-8: env-var rollback (set thresholds to values that effectively disable the band, set `link_expand_score_boost=1.0`).
- REQ-2: env-var on the hook to disable the injection (`KNOWLEDGE_DISABLE_LOW_CONFIDENCE_INJECTION=1`); the code path is preserved but skipped.
- REQ-4: revert `KNOWLEDGE_RETRIEVE_TOP_K` to `5` via SOPS env update.
- REQ-5: revert the prompt commit (single-file change).
- REQ-6: if a sparse-input change caused regression, revert and re-trigger ingest re-index (the existing `rebuild_kb` operator path).

If multiple regressions land at once, prefer per-requirement rollback over wholesale revert; the SPEC's modular boundaries are designed for this.
