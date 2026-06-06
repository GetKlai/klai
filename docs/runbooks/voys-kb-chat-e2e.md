# Voys KB Chat E2E Runbook

Use this runbook to validate the deployed Voys LibreChat knowledge path in a
real browser session. This is intentionally **not** a Playwright spec. It is a
repeatable operator/agent script for driving one visible session and proving
the result with UI observations plus VictoriaLogs.

## Scope

This validates the production Voys path:

```text
voys.getklai.com/app/chat
  -> LibreChat
  -> LiteLLM KlaiKnowledgeHook
  -> retrieval-api /retrieve
  -> evidence_pack citations
  -> visible Bronnen / Agent activiteit
```

It also checks the managed internal LibreChat MCP server for personal-KB save
traffic. Do not use `mcp.getklai.com` as proof for this runbook; that is the
public OAuth MCP surface, not the internal LibreChat-managed server.

## Preconditions

- Work from the repo root.
- Use the Voys attached session, not the isolated e2e tenant.
- Do not deploy, push, or edit code while running this.
- If this is run by an agent, use Playwright/Browser against the visible
  production page. Do not start localhost servers.
- The test saves one synthetic personal-KB note. That is deliberate because it
  proves the internal managed MCP path. Use a unique phrase per run.

## Preflight

Use the existing Voys storage-state. Do **not** start login/capture as the
first step. The routing is documented in `docs/setup/mcp-servers.md` and must
be followed in this order:

1. Repo-local Voys state:
   `klai-portal/frontend/e2e/prod-tenant/_config/storageState.voys.json`.
2. Global fallback:
   `~/.claude/mcp-storageState.json`.
3. Absolute override via `KLAI_PLAYWRIGHT_STORAGE_STATE=/absolute/path.json`.
4. Only if no existing state verifies: recapture the Voys state.

In Conductor workspaces, the repo-local Voys state may already exist in a
sibling or prior workspace under `.context/worktrees/.../klai-portal/frontend/e2e/prod-tenant/_config/storageState.voys.json`.
Copy that state into the current workspace before recapturing:

```bash
mkdir -p klai-portal/frontend/e2e/prod-tenant/_config
cp <existing-worktree>/klai-portal/frontend/e2e/prod-tenant/_config/storageState.voys.json \
  klai-portal/frontend/e2e/prod-tenant/_config/storageState.voys.json
chmod 600 klai-portal/frontend/e2e/prod-tenant/_config/storageState.voys.json
```

Then verify Voys storage-state:

```bash
cd klai-portal/frontend
npm run e2e:verify-voys-session
```

Expected:

```text
ok: true
mode: "voys-attached"
url: "https://voys.getklai.com/app"
apiMeStatus: 200
```

Only if verification still fails because there is no usable existing Voys state,
recapture:

```bash
cd klai-portal/frontend
npm run e2e:capture-session
# Log in via Google SSO in the opened browser.
npm run e2e:verify-voys-session
```

Confirm VictoriaLogs is reachable:

```bash
curl -sS --max-time 5 \
  -H "Authorization: Basic $VICTORIALOGS_BASIC_AUTH_B64" \
  "http://localhost:9428/select/logsql/query?query=_time:5m&limit=1"
```

If this fails, start or repair the tunnel:

```bash
./scripts/victorialogs-tunnel.sh
```

## Browser Setup

Open:

```text
https://voys.getklai.com/app/chat
```

For the headed Playwright helper that uses the repo-local Voys storage state and
drives the embedded LibreChat iframe, run from the repo root:

```bash
node docs/runbooks/voys-kb-chat-e2e-runner.mjs
```

In the chat top bar:

- `Chat met`: for technical Voys questions, **Support must be selected**.
  That is where the technical help knowledge lives.
- `Handbook` is for organisation/internal-policy questions.
- For multi-KB provenance checks, select both `Support` and `Handbook` and
  record whether both were in scope. This is useful for proving the system does
  not hide which KB produced the answer.
- `Modus`: switch between `Strict` and `Open` as listed below.
- Web search: use the LibreChat web-search control only for the web cases.
- Run in one visible conversation so follow-up context is realistic.

Before sending prompts, start a note with:

```text
run_id = voys-kb-e2e-<YYYYMMDD-HHMM>
personal_phrase = "yangon-<YYYYMMDD-HHMM>"
```

Use the same `personal_phrase` in steps 6-8.

## Test Steps

### 1. Strict, org KB, web off, relevant KB answer

Settings:

- `Chat met`: `Support` on. Optional multi-KB variant: `Support` + `Handbook`
  on; record the exact selected KBs.
- `Persoonlijk`: either on or off is acceptable, but record it
- `Modus`: `Strict`
- Web search: off

Prompt:

```text
Wat zegt onze kennisbank over Voys Freedom koppelen aan HubSpot?
```

Expected UI:

- Answer is grounded in Voys/KB material.
- Visible `Bronnen` disclosure exists when citable KB sources were used.
- Visible `Agent activiteit` says Strict / KB-only and shows KB retrieval.
- No pure web/general answer if KB evidence is absent.

Expected logs:

- One `retrieval_decision_record`.
- `kb_narrow=true`.
- `gate_skipped_reason="strict_mode"` or no gate bypass.
- `evidence_pack.source_count > 0` for a sourced answer.

### 2. Strict, org KB, web off, unknown topic refusal

Prompt:

```text
Wat is het officiële recept voor tiramisu volgens onze Voys kennisbank?
```

Expected UI:

- Refuses or says it is not in the knowledge base.
- Does not provide a broad tiramisu recipe.
- No `Bronnen` unless a real KB source was somehow used.
- `Agent activiteit` should show zero/no citable KB evidence when rendered.

Expected logs:

- Retrieval was attempted.
- `kb_narrow=true`.
- `evidence_pack.source_count=0` or no citable reason, unless the KB really has
  a relevant source.

### 3. Open, org KB, web off, general fallback allowed

Settings:

- `Modus`: `Open`
- Web search: off

Prompt:

```text
Leg kort uit wat een eSIM is en vermeld alleen Voys-bronnen als onze kennisbank daar iets over zegt.
```

Expected UI:

- A normal useful answer is allowed.
- `Bronnen` appear only if KB sources are actually used.
- Low-confidence, generic support pages must not be attached as citations.
  Regression guard: the answer must not cite `Overige problemen` for this
  eSIM prompt unless that source actually contains eSIM evidence.
- No claim that the KB was used when there are no citations/activity.

Expected logs:

- `kb_narrow=false`.
- Retrieval may be attempted or gate-bypassed, depending on the query.
- If gate-bypassed, that is acceptable only in Open mode.
- If retrieval returns `confidence_band=low`, LiteLLM should either apply the
  low-confidence Open guard or pass through a general answer without rendering
  unsupported KB sources.

### 4. Open, org KB, web on, KB plus web provenance

Settings:

- `Modus`: `Open`
- Web search: on

Prompt:

```text
Gebruik eerst onze kennisbank voor Voys Freedom en HubSpot. Zoek daarna op internet of er actuele HubSpot-eisen zijn die relevant kunnen zijn.
```

Expected UI:

- KB source provenance remains visible when KB evidence is used.
- Web/tool activity is visible separately.
- Web results do not hide whether KB retrieval happened.

Expected logs:

- Retrieval-api log for the KB leg if KB was consulted.
- LibreChat/tool activity for web search.
- No runaway tool loop.

### 5. Strict, org KB, web on, KB is still primary

Settings:

- `Modus`: `Strict`
- Web search: on

Prompt:

```text
Controleer in onze kennisbank of Voys Freedom met HubSpot kan koppelen. Gebruik web alleen als aanvullende context, niet als vervanging van de kennisbank.
```

Expected UI:

- KB is consulted.
- Answer does not become a web-only answer.
- `Agent activiteit` makes KB retrieval visible.

Expected logs:

- `kb_narrow=true`.
- Strict gate bypass must not happen.
- Check whether web tool calls occurred and whether they stayed bounded.

### 6. Managed MCP personal save

Settings:

- `Persoonlijk`: on
- Mode can stay `Open`

Prompt, replacing the phrase:

```text
Onthoud dit voor mij in mijn persoonlijke kennisbank: de e2e-codezin voor vandaag is "yangon-<YYYYMMDD-HHMM>". Antwoord alleen met "opgeslagen".
```

Expected UI:

- The model calls the managed personal save tool or otherwise reports save
  success.
- No OAuth consent prompt appears.
- No resource-mismatch or authorization error appears.

Expected logs:

- `klai-knowledge-mcp` receives internal managed traffic.
- No relevant `resource mismatch` error for this request.
- No public `mcp.getklai.com` OAuth path is needed for success.

### 7. Personal KB retrieval

Prompt:

```text
Wat is mijn e2e-codezin voor vandaag? Zoek in mijn kennisbank.
```

Expected UI:

- Answer contains the exact `personal_phrase`.
- `Bronnen`/activity should indicate KB retrieval when citable.
- If no source appears but the phrase is answered, logs must prove whether it
  came from retrieval or conversation memory.

Expected logs:

- Retrieval scope is `personal` or `both`.
- `retrieval_personal_scope_canonical_filter_applied` appears for personal/both
  requests.
- `knowledge.queried` product event has `had_results=true`.

### 8. No selected KB, Open

Settings:

- Turn `Persoonlijk` off.
- Turn all org KBs off.
- `Modus`: `Open`.

Prompt:

```text
Wat is mijn e2e-codezin voor vandaag?
```

Expected UI:

- The assistant should not claim the KB was consulted.
- No KB citation should appear.
- It may mention it knows only from current conversation context if the phrase
  is still in the same visible conversation. That is acceptable; it is not KB
  proof. The log check must show no KB retrieval for this turn.

Expected logs:

- No `/retrieve` call for this turn, or explicit no-scope branch.
- No misleading `knowledge.queried` event.

### 9. No selected KB, Strict

Settings:

- All KB scopes off.
- `Modus`: `Strict`.

Prompt:

```text
Wat is het verschil tussen een simkaart en eSIM?
```

Expected UI:

- Should not answer broadly from general knowledge.
- Should communicate that no KB scope is available.
- Known observability gap: this branch may not render `Agent activiteit`
  because it can return before `_klai_kb_meta` is attached.

Expected logs:

- No retrieval request should be made.
- If the model answers broadly, record it as a Strict/no-scope contract failure.

## Log Correlation

For every prompt, record:

- timestamp
- final answer summary
- visible `Bronnen`: yes/no and titles
- visible `Agent activiteit`: yes/no and key lines
- network/request id if available

Prefer request-id correlation:

```text
request_id:<uuid>
```

If no request id is available, use a narrow time window and the unique
`personal_phrase` only when telemetry mode allows content logging. Otherwise
avoid querying raw prompt text.

Useful LogsQL probes:

```text
_time:15m service:retrieval-api "retrieval_decision_record"
_time:15m service:retrieval-api "retrieval_personal_scope_canonical_filter_applied"
_time:15m service:retrieval-api "knowledge.queried"
_time:15m service:litellm "query_rewrite" OR "query_rewrite_metadata"
_time:15m service:litellm "KlaiKnowledgeHook: retrieval"
_time:15m "resource mismatch"
_time:15m "requiresOAuth"
_time:15m (" 429 " OR "status=429" OR "status\":429")
```

For a single request id, inspect:

- Caddy status and upstream path.
- LiteLLM hook state: mode, retrieval failure, citation decision.
- retrieval-api: scope, `kb_narrow`, gate, confidence band, source count,
  result count, timings.
- knowledge-mcp for save/search tool calls.

## Pass/Fail Criteria

Pass only if all are true:

- Strict relevant KB answers cite KB evidence or refuse when uncited.
- Strict unknown/no-scope cases do not become general answers.
- Open can answer generally but does not fake KB citations.
- Web search usage stays distinguishable from KB provenance.
- Managed internal MCP does not trigger an OAuth/resource mismatch flow.
- Normal prompt sequence does not hit 429.
- Retrieval latency is reasonable and there is no duplicate/runaway retrieval
  pattern.

Fail or mark residual risk if:

- No logs can be correlated to the visible browser prompts.
- Citations are present but `Agent activiteit` does not prove KB retrieval.
- Personal phrase answer could be explained by conversation memory rather than
  retrieval and logs do not disambiguate.
- Any prompt produces repeated tool calls, OAuth prompts, 429, 5xx, or retrieval
  failures.

## Expected Architectural Gaps To Watch

- Citations prove used sources, not that KB retrieval happened.
- `Agent activiteit` currently summarizes mode, fragment count, source
  selection, source titles, confidence, and no-citable reason, but not every
  `_klai_kb_meta` field such as all KBs in scope.
- Some early no-scope branches may not render activity at all.
- Open mode can gate-bypass retrieval; that is expected only outside Strict.
- Ambient logs are not proof. Use request id or a tight timestamp window.
