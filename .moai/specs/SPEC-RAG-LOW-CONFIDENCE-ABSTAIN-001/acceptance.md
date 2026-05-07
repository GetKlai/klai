---
id: SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001
acceptance_for: spec.md
created: 2026-05-07
updated: 2026-05-07
author: Mark Vletter
---

# Acceptance — SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001

This document is the executable contract for the SPEC. Every row below corresponds to an `AC-N` in `spec.md` and gets either ✅ (verified) or ❌ (failing) as part of the merge gate. A row that cannot be exercised yet is `⏸ pending`.

## How to run

Three test surfaces:

1. **Unit / structural tests** — `uv run pytest` in each affected service.
2. **Eval harness** — `docker exec klai-core-knowledge-ingest-1 python -m knowledge_ingest.eval --suite chat --variant <name>`.
3. **Live replay** — manual end-to-end through the litellm-hook on `core-01`, with a captured trace from VictoriaLogs (`request_id:<uuid>` query) attached to the merge PR.

The replay uses the original 2026-05-07 19:30 UTC Voys-Salesforce conversation:

```
turn 1: "Ik wil Voys Freedom graag koppelen aan Salesforce. Hoe werkt dat?"
turn 2: "Heb ik niet iets van Red Cactus daarvoor nodig of iets van Bubble, waardoor ik diep kan integreren?"
```

Tenant: `org_zitadel_id = 368884765035593759`. Conversation: `f46b3c21-4b2f-471f-9049-62038c7d43b3` (preserved in MongoDB `librechat-voys.conversations`).

---

## Acceptance criteria

### AC-1 — confidence_band returned on Voys-Salesforce replay

**Given** the production retrieval-api with REQ-1 deployed,
**when** turn 1 of the Voys-Salesforce replay is sent to `/retrieve` with `query="Ik wil Voys Freedom graag koppelen aan Salesforce. Hoe werkt dat?"` and `top_k=20`,
**then** the response body MUST contain `"confidence_band": "low"`.

**Verify with**:
```bash
curl -sS -H "Authorization: Bearer $KLAI_INTERNAL_SECRET" \
     -H "X-Caller-Service: litellm" \
     -X POST https://retrieval.internal/retrieve \
     -d '{"query":"Ik wil Voys Freedom graag koppelen aan Salesforce. Hoe werkt dat?","org_id":"368884765035593759","top_k":20}' \
  | jq .confidence_band
```
Expected output: `"low"`.

**Counter-test (regression-canary)**: run the same with the rekeningnummer query from turn 5 of the same conversation (`"Ik moet mijn rekeningnummer wijzigen, want het geld wordt van de verkeerde rekening afgeschreven."`). Expected: `"high"` (max-rerank in original logs was 0.96).

---

### AC-2 — Anti-hallucination injection fires AND chat stays factual on band=low

**Given** REQ-1 + REQ-2 deployed,
**when** turn 1 of the replay is sent end-to-end through the litellm-hook (`POST /v1/chat/completions` with the Voys-Salesforce question as the user message),
**then**:

1. VictoriaLogs MUST show `service:litellm AND _msg:"low_confidence_injection_applied"` for the request_id.
2. The chat-completion response text MUST contain at least one of:
   - the substring `verduidelijking` (clarifying-question)
   - the substring `vind hier weinig`
   - the substring `staat niet expliciet in de`
3. The chat-completion response text MUST NOT contain any of the substrings: `WhatsApp`, `Zapier`, `IFTTT`. (These are the canonical hallucinated route from the original incident; they may legitimately appear if/when the KB later has explicit Voys-WhatsApp content, but at that point AC-2 should be re-baselined with new negative-class strings.)

**Counter-test**: replay turn 5 (rekeningnummer). Expected: NO `low_confidence_injection_applied` log event for this request_id.

---

### AC-3 — Brand-bridging rewrite for the original query

**Given** REQ-5 deployed,
**when** turn 1 of the replay reaches the `_QUERY_REWRITE_AND_CLASSIFY_PROMPT` step in the hook,
**then** the rewritten query (logged under `service:litellm AND event:"query_rewrite_done"`) MUST contain at least one of: `CRM`, `CRM-koppeling`, `Bubble`, `RedCactus`.

**Counter-test**: a control query that does not mention any third-party brand (e.g. `"Hoe stel ik vakantie aan?"`). Expected: rewritten query does NOT contain category-bridging terms — the brand-bridging instruction MUST not over-apply on queries that have no brand in scope.

---

### AC-4 — Voys-Salesforce regression canary improvement

**Given** REQ-1 through REQ-5 deployed AND the `chat-brand-salesforce-bridging` regression query in `chat.yaml` (REQ-7),
**when** the eval harness runs:
```bash
docker exec klai-core-knowledge-ingest-1 \
  python -m knowledge_ingest.eval --suite chat --variant low_confidence_v1
```
**then** the row in `knowledge.rag_eval_results` for `query_id = chat-brand-salesforce-bridging` AND `variant = low_confidence_v1` MUST have:

- `context_precision >= 0.50` (vs. estimated 0.05 baseline at the original max-rerank 0.18)
- AT LEAST ONE entry in `retrieved_chunk_ids` that maps to a Qdrant payload with `source_url LIKE '%bubble%' OR source_url LIKE '%redcactus%'`

**Verify with**:
```sql
SELECT
  context_precision,
  retrieved_chunk_ids
FROM knowledge.rag_eval_results
WHERE query_id = 'chat-brand-salesforce-bridging'
  AND variant = 'low_confidence_v1'
ORDER BY run_at DESC
LIMIT 1;
```

The `retrieved_chunk_ids` array is a list of Qdrant point IDs. Verifying the URL constraint requires a follow-up scroll on Qdrant — the merge PR description MUST include this lookup as a runbook command, with the actual matching URLs pasted into the PR.

---

### AC-5 — No aggregate regression on existing chat-suite

**Given** the full `chat.yaml` suite (existing 30 + new 7 = 37 queries),
**when** the same eval run as AC-4 completes,
**then** comparing `low_confidence_v1` against the captured `pre_spec_baseline_v1` row from the SPEC's pre-flight:

- Aggregate `context_precision` across the 30 PRE-EXISTING queries MUST NOT drop by more than 0.02
- Aggregate `context_recall` across the 30 PRE-EXISTING queries MUST NOT drop by more than 0.02
- Aggregate `faithfulness` across the 30 PRE-EXISTING queries MUST NOT drop below 0.78 (4 points below the current 0.81 alert-buffer)

**Verify with**:
```sql
WITH baseline AS (
  SELECT query_id,
         context_precision AS cp_pre,
         context_recall AS cr_pre,
         faithfulness AS fa_pre
  FROM knowledge.rag_eval_results
  WHERE variant = 'pre_spec_baseline_v1'
), treatment AS (
  SELECT query_id,
         context_precision AS cp_post,
         context_recall AS cr_post,
         faithfulness AS fa_post
  FROM knowledge.rag_eval_results
  WHERE variant = 'low_confidence_v1'
)
SELECT
  AVG(t.cp_post - b.cp_pre) AS delta_cp,
  AVG(t.cr_post - b.cr_pre) AS delta_cr,
  AVG(t.fa_post)             AS abs_fa_post
FROM baseline b
JOIN treatment t USING (query_id)
WHERE b.query_id NOT LIKE 'chat-brand-%';  -- exclude the new canaries
```

Expected: `delta_cp >= -0.02 AND delta_cr >= -0.02 AND abs_fa_post >= 0.78`.

If the aggregate moves outside the band, the PR is blocked from merging until either thresholds are re-tuned or a regression is fixed.

---

### AC-6 — Link-expand survival rate > 10% over 7 days

**Given** REQ-3 deployed for at least 7 days,
**when** Prometheus is queried for the rolling 7-day window:
```promql
sum(rate(retrieval_link_expand_top_k_total{outcome="hit",org_id="368884765035593759"}[7d]))
/ sum(rate(retrieval_link_expand_top_k_total{org_id="368884765035593759"}[7d]))
```
**then** the result MUST be `>= 0.10`.

**Counter-test**: the same query for an org where link-expand provides no value (no internal cross-references in source content) MAY show a lower number; this counter-check is informational, not a gate.

---

### AC-7 — Sparse-input parity audit closed

**Either** outcome is acceptable, but exactly one MUST be present in the merge PR:

**(a)** Code change + unit test asserting `embed_sparse` is invoked with `context_prefix + chunk_text` for any chunk where `context_prefix` is non-empty. Test file: `klai-knowledge-ingest/tests/test_sparse_input_parity.py`. Plus a one-shot script-output (committed as `klai-knowledge-ingest/tests/sparse_parity_evidence.json` or pasted into PR description) showing sparse-vector indices differ between with-prefix and without-prefix inputs on a 10-chunk sample.

**(b)** A PR comment + HISTORY entry in `spec.md` reading `0.1.1 — REQ-6 verified, no code change required`. The PR comment MUST include the file:line citations that prove parity (e.g. `enrichment.py:435 → embed_sparse(enriched_text)`).

---

### AC-8 — Negative-class canary triggers band=low

**Given** REQ-7 includes a negative-class canary (a brand NOT in Voys's KB; `chat.yaml` entry `chat-brand-not-in-kb`, with brand TBD during implementation — pick something genuinely absent like `Salesforce Commerce Cloud B2B Lightning Marketplace Connector`),
**when** the eval harness runs the negative canary,
**then**:

- The row in `knowledge.rag_eval_results` MUST have `meta->>'confidence_band' = 'low'`
- An end-to-end replay through the litellm-hook MUST inject the anti-hallucination message AND the chat response MUST contain a clarifying-question phrase

This canary intentionally exercises the case where the SPEC's safety net is the desired behaviour, not a workaround for a fixable retrieval gap.

---

### AC-9 — Latency p95 ≤ baseline + 10%

**Given** the SPEC fully deployed,
**when** Grafana p95 retrieval-api latency is measured over 24 h post-deploy:
```promql
histogram_quantile(0.95, sum(rate(retrieval_api_request_duration_seconds_bucket[24h])) by (le))
```
**then** the value MUST be `<= pre_spec_p95 * 1.10`.

The `pre_spec_p95` baseline is captured 1 hour before deploy, written to `docs/runbooks/rag-quality.md` for reference, and used as the comparison anchor.

If the gate fails: profile the link-expand boost path and the band-emit computation. Both should be effectively constant-time. If `top_k=20` is the cause (more reranker outputs to serialize), revisit Unit 5 sequencing.

---

### AC-10 — Grafana panel renders + alert loads

**Given** REQ-8 deployed,
**when** the Grafana UI is opened to `RAG Quality > Low-Confidence`,
**then**:

- Panel renders within 5 seconds
- Three time-series visible (`band=high`, `band=medium`, `band=low`)
- One time-series visible for `link_expand_top_k` survival rate
- One time-series visible for `low_confidence_injection_total`
- Alert rule `rag_low_confidence_served_rate` appears in `Alerting → Alert rules` in `pending` or `inactive` state — NEVER `error` (which would indicate a malformed expr).

**Verify with**: a Grafana screenshot attached to the merge PR PLUS the output of:
```bash
ssh core-01 "docker exec klai-core-grafana-1 sh -c 'wget -qO- http://localhost:3000/api/v1/provisioning/alert-rules \
  | jq -r \".[] | select(.title==\\\"rag_low_confidence_served_rate\\\") | .uid\"'"
```
Expected: a non-empty UID.

---

## Counter-tests (regression-canary set, MUST stay green)

These are NOT acceptance criteria; they are existing canary queries that MUST continue to pass after the SPEC ships, captured here so the SPEC's reviewer remembers to check them:

| Existing canary | Why it might regress | What to check |
|---|---|---|
| `chat-easy-bubble-troubleshoot` | top_k=20 may flood with marginal chunks | `context_precision` stays within ±0.02 of pre-baseline |
| `chat-easy-yealink-firmware` | link-expand boost may surface unrelated firmware pages | top-1 still includes a Yealink-firmware URL |
| `chat-easy-pipedrive-integratie` | brand-bridging rewrite may under-narrow when the brand IS in the KB | top-3 still includes a Pipedrive-specific URL |
| `chat-easy-opzeggingen` | anti-hallucination injection MUST NOT fire on a high-confidence query | no `low_confidence_injection_applied` event in the trace |

---

## Sign-off

The merge PR description MUST include a checklist mirroring AC-1 through AC-10, each with ✅/❌ and a link to the test artefact (log excerpt, eval-run ID, screenshot, etc.). A SPEC merge with any ❌ requires either:

1. The failing AC reverted from scope (with HISTORY entry), AND
2. A follow-up issue tracking the deferred work.

No silent merges — every AC has a documented outcome.
