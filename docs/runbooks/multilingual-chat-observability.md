# Multilingual chat observability

> Operator runbook for the `language_correctness` telemetry that ships
> with SPEC-RAG-MULTILINGUAL-CHAT-001 (Phase 2 + Phase 4).

## Three chat paths, three emit points

The `chat_synthesis_complete` log event is emitted from whichever
chat path served the request. There are three:

| Path | When it fires | `service:` label |
|---|---|---|
| A — LibreChat → LiteLLM `klai_knowledge.py` hook → Mistral | LibreChat user-facing chat traffic; presence of `data["user"]` in the LiteLLM request is the signal that this is path A | `litellm` (planned — see "Path A telemetry caveat" below) |
| B — portal-api `/partner/v1/chat/completions` → LiteLLM (no `user` field) → Mistral | Embeddable Widget AND external Partner API tokens both flow through here | `portal-api` |
| C — retrieval-api `/chat` (dormant) | No external callers today; reserved for SPEC-KNOW-005's feedback feature | `retrieval-api` |

Path A is the most user-visible. The `klai_knowledge.py` hook is the
canonical place where the multilingual prefix is constructed for that
path. Paths B and C use the shared `GROUNDED_CHAT_SYSTEM_PROMPT` from
`klai-libs/chat-prompts` directly. After Phase 4 (REQ-10), path A
imports the same constant via the vendored single-file copy at
`deploy/litellm/klai_chat_prompts.py` (drift-tested by
`deploy/litellm/tests/test_klai_chat_prompts_drift.py`).

### Path A telemetry caveat (Phase 4 ship → Phase D close)

Phase 4 ships the multilingual *prompt* contract for path A but does
not ship the `chat_synthesis_complete` *emit* yet. Reason: the LiteLLM
container is a stock upstream image (`ghcr.io/berriai/litellm:v1.83.7-stable`)
and does not bundle `lingua-language-detector`. Without `lingua`, path A
cannot fill `query_language_detected` / `response_language_detected` /
`language_correctness` and the emit would be a partial event of limited
value.

The plan to close this gap aligns with the Phase D pip-install plan
already documented in `deploy/litellm/klai_service_auth.py` and
`deploy/litellm/klai_chat_prompts.py`: build a custom litellm
Dockerfile that `pip install`s `klai-chat-prompts` AND
`lingua-language-detector`, then add an `async_post_call_success_hook`
emit in `klai_knowledge.py` that mirrors the existing emits in
`partner_chat.py` (path B) and `synthesis.py` (path C).

Until then, path-A coverage of the rolling 7-day language-correctness
gate (REQ-05) comes from:

1. The pre-merge eval gate (`evaluation/cross_lingual_runner.py`) —
   exercises path C against the same prompt foundation as path A
   (since v1.2 they share `GROUNDED_CHAT_SYSTEM_PROMPT` byte-identical).
2. Manual smoke tests in LibreChat after deploys (one DE/FR/PT/ES
   query each).
3. Path B (Widget + Partner API) telemetry — extrapolated as a proxy
   when path A traffic shares the same target audience.

## Fields

| Field | Type | Source |
|---|---|---|
| `event` | string | hardcoded `chat_synthesis_complete` |
| `service` | string | `litellm`, `retrieval-api`, or `portal-api` (see table above) |
| `query_language_detected` | string | `lingua` detection on the user's last query — `nl`, `en`, `de`, `fr`, `pt`, `es`, or `und` |
| `response_language_detected` | string | `lingua` detection on the assembled LLM response |
| `language_correctness` | bool \| null | `true` when both languages are known and match, `false` when known and mismatched, `null` when either side is `und` |
| `response_length_chars` | int | length of the response text (trace-level signal) |
| `org_id` | int \| string \| null | tenant id (only emitted by partner_chat — path B) |
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
