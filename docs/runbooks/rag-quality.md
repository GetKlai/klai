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
