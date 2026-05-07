# Multilingual chat observability

> Operator runbook for the `language_correctness` telemetry that ships
> with SPEC-RAG-MULTILINGUAL-CHAT-001 (Phase 2).

## What is logged

Both `klai-retrieval-api/services/synthesis.py` and
`klai-portal/backend/app/services/partner_chat.py` emit a structured
log event named `chat_synthesis_complete` after every chat completion
returns to the user.

Fields:

| Field | Type | Source |
|---|---|---|
| `event` | string | hardcoded `chat_synthesis_complete` |
| `service` | string | `retrieval-api` or `portal-api` |
| `query_language_detected` | string | `lingua` detection on the user's last query — `nl`, `en`, `de`, `fr`, `pt`, `es`, or `und` |
| `response_language_detected` | string | `lingua` detection on the assembled LLM response |
| `language_correctness` | bool \| null | `true` when both languages are known and match, `false` when known and mismatched, `null` when either side is `und` |
| `response_length_chars` | int | length of the response text (trace-level signal) |
| `org_id` | int \| string \| null | tenant id (only emitted by partner_chat) |
| `request_id` | uuid | propagated by `RequestContextMiddleware` |

## Querying VictoriaLogs

Top-level health query:

```
event:chat_synthesis_complete
| stats by (response_language_detected, language_correctness) count() AS n
```

Last-7-day per-language correctness rate:

```
event:chat_synthesis_complete AND language_correctness != null
_time:7d
| stats by (query_language_detected) count_if(language_correctness=true) AS correct, count() AS total
| extend rate = correct / total
```

Per-tenant break-down (portal-api only):

```
event:chat_synthesis_complete AND service:portal-api AND org_id != null
_time:7d
| stats by (org_id, query_language_detected) count_if(language_correctness=false) AS mismatches
| sort -mismatches
```

## Alerting threshold

Per SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-05: per-language language-correctness
must hold ≥ 95% over a rolling 7-day window. Suggested alert rule
(VictoriaLogs / VMAlert):

- query: `count_if(event:chat_synthesis_complete AND language_correctness=false) / count_if(event:chat_synthesis_complete AND language_correctness != null)`
- group by: `query_language_detected`
- evaluate window: 7d
- fire when: `rate > 0.05` for any group

## Adding a Grafana panel

The dashboards under `deploy/grafana/provisioning/dashboards/` are
managed via JSON. The current set has no dedicated chat dashboard. To
expose `language_correctness` as a stat panel:

1. Open Grafana → Create → Dashboard → Add visualization.
2. Pick the VictoriaLogs datasource.
3. Use the LogsQL query above (per-language rate variant).
4. Visualization: bar chart, x-axis = `query_language_detected`,
   y-axis = `rate`, threshold red < 0.95.
5. Save — Grafana provisioning auto-syncs to disk under
   `provisioning/dashboards/` next reload.

Until that JSON lives in repo, the metric is queryable directly via the
LogsQL queries above. For a 5-minute health check during/after a
deploy, the top-level query is sufficient.

## Why no automatic switch on bad correctness?

`language_correctness` is observability-only. A `false` value does not
retry, does not switch models, does not re-prompt the user. It exists
to flag drift so operators can act — not to mask drift in the
production code path. The pre-merge eval gate
(`evaluation/cross_lingual_runner.py`) is the enforcement surface;
this runbook is the post-deploy monitoring surface.

If a tenant consistently hits low correctness for one language, the
follow-up actions are:

1. Check whether the source documents for that tenant are in a
   non-target language (out-of-distribution input).
2. Consider whether `klai-fast` (Mistral Small) is sufficient or
   whether per-tenant model escalation should be added — explicitly
   deferred from SPEC-RAG-MULTILINGUAL-CHAT-001 V1, would be a
   follow-up SPEC.

## Privacy

Both `query_language_detected` and `response_language_detected` are
high-level metadata (one-of `nl|en|de|fr|pt|es|und`). They do **not**
contain user content. The `response_length_chars` field is an integer
length only.
