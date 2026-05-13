# RAG Quality Runbooks

> Step-by-step procedures for RAGAS evaluation harness alerts.
> See `.moai/specs/SPEC-RAG-EVAL-001/spec.md` for the harness design.

---

## faithfulness-baseline-low

**Situation:** the alert `rag_eval_faithfulness_low` has fired. The `baseline` faithfulness RAGAS metric scored below 0.85 on TWO consecutive nightly runs for at least one suite. The retrieval stack is producing answers that the judge LLM thinks are not grounded in the retrieved chunks.

**This is a quality-regression detector, not an outage.** Chat keeps working; users just get worse answers. Triage during business hours unless a customer is escalating.

### Step 1 — Identify which suite regressed

```bash
ssh core-01

docker exec klai-core-postgres-1 psql -U klai -d klai -c "
  SELECT
    suite,
    date_trunc('day', run_at) AS day,
    AVG(faithfulness) AS faithfulness_avg,
    COUNT(*) AS query_count,
    COUNT(*) FILTER (WHERE faithfulness IS NULL) AS null_count
  FROM knowledge.rag_eval_results
  WHERE variant = 'baseline'
    AND run_at >= NOW() - INTERVAL '7 days'
  GROUP BY suite, date_trunc('day', run_at)
  ORDER BY suite, day DESC;
"
```

Look for the suite where `faithfulness_avg` dropped. A drop on `chat` only is different from a drop on both suites — the latter is more likely a retrieval-stack issue, the former a query-handling issue.

### Step 2 — Inspect the failing rows

```bash
docker exec klai-core-postgres-1 psql -U klai -d klai -c "
  SELECT
    query_id,
    faithfulness,
    answer_relevance,
    array_length(retrieved_chunk_ids, 1) AS chunk_count,
    retrieval_ms,
    meta->>'error' AS error,
    meta->>'errors' AS errors
  FROM knowledge.rag_eval_results
  WHERE variant = 'baseline'
    AND run_at >= NOW() - INTERVAL '24 hours'
    AND (faithfulness < 0.85 OR faithfulness IS NULL)
  ORDER BY faithfulness ASC NULLS FIRST;
"
```

Common patterns:
- **`null_count` is high**: judge or retrieval is failing. Check `meta.error` for `retrieval_failed:` or `judge_answer_failed`. Likely root cause: klai-fast / LiteLLM proxy down, OR retrieval-api down, OR `KLAI_RETRIEVAL_INTERNAL_SECRET` rotation broke auth.
- **Faithfulness dropped uniformly** (most queries from 0.9 → 0.7): the corpus changed underneath us. New ingest moved the baseline. Check `knowledge.artifacts` row count diff between the two days.
- **A specific subset failed**: targeted regression. Look at the `query_id` prefix (`chat-easy-*`, `chat-vague-*`, `chat-synth-*`) — if all the easy-lookup canaries dropped, retrieval lost a key chunk.

### Step 3 — Check infrastructure

```bash
# Retrieval-api health
docker logs --tail 50 klai-core-klai-retrieval-api-1 2>&1 | grep -iE "error|warn" | head -20

# LiteLLM proxy health (judge LLM path)
docker logs --tail 50 klai-core-litellm-1 2>&1 | grep -iE "error|429|503|unauthorized" | head -20

# knowledge-ingest worker (where the harness runs)
docker logs --tail 100 klai-core-knowledge-ingest-1 2>&1 | grep -E "rag_eval_" | head -30
```

If any of these show errors correlating with the regression timestamp, fix the infra problem first and let the next nightly run reset the alert.

### Step 4 — Reproduce the regression manually

```bash
docker exec klai-core-knowledge-ingest-1 \
  python -m knowledge_ingest.eval --suite chat --variant manual-debug
```

This runs the same suite synchronously and prints per-query results. Cheaper than waiting 24h for the next nightly. Cost: ~€0.10 per run.

### Step 5 — Decide whether to escalate

- If infra issue is fixed: do nothing, alert auto-resolves on the next 6h evaluation cycle once two consecutive `baseline` runs are above 0.85 again.
- If the corpus changed and the new baseline is the new normal: file a follow-up SPEC and lower the alert threshold to the new floor (in `deploy/grafana/provisioning/alerting/rag-eval-rules.yaml`).
- If a Tier-2/3 SPEC implementation is the cause (someone shipped a `variant` that became the default — should not happen): revert.

### Related

- SPEC: `.moai/specs/SPEC-RAG-EVAL-001/spec.md`
- Plan: `.moai/specs/SPEC-RAG-EVAL-001/plan.md`
- Roadmap: `docs/architecture/retrieval-improvements-roadmap.md`

---

## Low-confidence served rate alert (SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001)

**Alert:** `RAG: Low-confidence served rate > 20%`
**UID:** `spec-rag-001-low-confidence-rate`
**Severity:** HIGH

**Situation:** More than 20% of retrieval responses for a given tenant have been classified `confidence_band=low` or `confidence_band=unknown` continuously for 5 minutes over the trailing 1-hour window. This is not a hard outage — chat keeps working — but the anti-hallucination injection (REQ-2) is likely firing on many queries, meaning users are being asked clarifying questions instead of receiving substantive answers. Triage during business hours unless customer escalation is active.

**What `confidence_band` means:** the retrieval-api classifies each `/retrieve` response by `max(reranker_scores_top5)`:
- `high` when `>= 0.60` — retrieval found relevant content
- `medium` when `>= 0.30` — retrieval found partial content
- `low` when `< 0.30` — retrieval is effectively noise; anti-hallucination injection fires
- `unknown` when the reranker is disabled, falls back, or the served list is empty

A rate above 20% for 5+ minutes is anomalous — in normal operation most queries should resolve to `high` or `medium` for a well-indexed KB.

**This is a quality-regression detector, not an outage.** Do not page on-call unless a customer SLA breach is confirmed.

---

### Triage path 1 — KB coverage gap

**Most likely cause when the alert fires for a specific tenant only.**

Check whether the queries driving low-confidence results are genuinely not covered by the KB:

```bash
# Query VictoriaLogs for trailing hour of low-confidence events on the affected tenant
# (replace <ORG_ID> with the org_id from the alert label)
# Run via the VictoriaLogs MCP or with an SSH tunnel to the victorialogs-tunnel.sh script
```

LogsQL query:
```
service:retrieval-api AND confidence_band:low AND org_id:<ORG_ID> AND _time:[now-1h, now)
```

Sample 5 queries from the results. For each, judge:
- Does the Voys help-centre KB realistically contain an answer to this query?
- Does the query use a third-party brand name (e.g. Salesforce, HubSpot, Zoom) that the brand-bridging rewrite (REQ-5) should have expanded?

If the queries are genuinely not in scope for this KB (e.g. a new product area, an integration partner whose page was never ingested), the correct resolution is a **KB gap issue** filed against the relevant KB owner — not a threshold change.

**Resolution:** file an issue against the KB owner identifying the uncovered query class. The alert will auto-resolve once the KB is updated and retrieval confidence recovers.

---

### Triage path 2 — Confidence threshold mistune

**Most likely cause when the alert fires across multiple tenants simultaneously, or when the low-confidence rate is only slightly above 20% and the retrieval-api recently had threshold config changes.**

Check the actual reranker score distribution on recent `retrieval_decision_record` log events:

```
service:retrieval-api AND _time:[now-1h, now) AND confidence_band:low AND org_id:<ORG_ID>
```

Look at the `reranker_scores_top5` field values. If the distribution clusters around 0.28–0.32 (just below the `low` threshold of 0.30), the thresholds may be too aggressive.

Also check whether there was a recent `confidence_band_high_threshold` or `confidence_band_low_threshold` config change in the retrieval-api environment:

```bash
ssh core-01 "docker exec klai-core-klai-retrieval-api-1 printenv | grep -i confidence"
```

**Resolution:** if the thresholds are mistimed, adjust `CONFIDENCE_BAND_HIGH_THRESHOLD` and `CONFIDENCE_BAND_LOW_THRESHOLD` env vars in `deploy/docker-compose.yml` (retrieval-api environment block) and redeploy via `compose-up.sh`. The defaults are `0.60` and `0.30` per SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-1. Any change below `0.20` for the low threshold or above `0.50` for the high threshold requires SPEC sign-off.

---

### Triage path 3 — Degraded retrieval-api or reranker service

**Most likely cause when the alert fires suddenly across all tenants and the low-confidence rate jumps to 80–100% (i.e. the reranker is returning noise or zeroes uniformly).**

Check the Grafana `Reranker latency` and `Reranker fallback rate` panels on the `klai-health` dashboard.

Also check retrieval-api container health directly:

```bash
ssh core-01

# Retrieval-api recent errors
docker logs --tail 50 klai-core-klai-retrieval-api-1 2>&1 | grep -iE "error|warn|rerank|timeout" | head -20

# Reranker service (if separate)
docker logs --tail 50 klai-core-klai-reranker-1 2>&1 | grep -iE "error|warn" | head -20 2>/dev/null || echo "No separate reranker container"
```

If `confidence_band=unknown` is dominating (not `band=low`), the reranker has likely fallen back or is disabled. Check:

```bash
docker exec klai-core-klai-retrieval-api-1 printenv | grep -i rerank
```

**Resolution:** if retrieval-api or the reranker is degraded, follow the standard infrastructure incident runbook at `docs/runbooks/platform-recovery.md`. The alert auto-resolves once the reranker resumes producing valid scores and the 1h window clears.

---

### Checking the anti-hallucination injection rate

To confirm the injection is firing (and the model is being guided correctly on low-confidence queries), check the LiteLLM injection counter:

```
service:litellm AND _time:[now-1h, now)
```

Look for log events with the `litellm_low_confidence_injection_total` counter increment — these should correlate with the retrieval-api `confidence_band:low` events on the same `request_id`.

If the injection rate is 0% but the low-confidence band rate is above 20%, there is a wiring gap between retrieval-api and the litellm hook — check that the `confidence_band` field is present in the `/retrieve` response and that the hook version is current.

---

### Related

- SPEC: `.moai/specs/SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001/spec.md`
- Alert rule: `deploy/grafana/provisioning/alerting/rag-eval-rules.yaml` (uid: `spec-rag-001-low-confidence-rate`)
- Dashboard panel: `deploy/grafana/provisioning/dashboards/rag-quality.json` (panel id 6, "Low-Confidence")
- Regression canaries: `klai-knowledge-ingest/knowledge_ingest/eval/suites/chat.yaml` (mix: `brand_bridging`)
- Roadmap: `docs/architecture/retrieval-improvements-roadmap.md`
