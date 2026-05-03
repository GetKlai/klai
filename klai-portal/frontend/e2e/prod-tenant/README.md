# prod-tenant E2E

Production tenant end-to-end tests. Runs against a dedicated bot tenant
on production (default `e2e.getklai.com`). Every PR/deploy gets exercised
against real LibreChat + LiteLLM + retrieval pipeline, no mocks.

> **Plan & rationale:** see [`docs/testing/test-suite-plan.md`](../../../../docs/testing/test-suite-plan.md).

## Run locally

```bash
# .env.local (gitignored; values from 1Password)
export E2E_BASE_URL=https://e2e.getklai.com
export E2E_USER_EMAIL=e2e@getklai.com
export E2E_USER_PASSWORD=...
export E2E_TOTP_SECRET=...

cd klai-portal/frontend
npm run test:e2e:prod
```

Run a single journey with the UI inspector:

```bash
npx playwright test e2e/prod-tenant/J03-knowledge.spec.ts --headed
```

Debug-mode (step-through):

```bash
PWDEBUG=1 npx playwright test e2e/prod-tenant/J02-chat.spec.ts
```

## Add a new journey

1. **Pick a free `J##` slot** higher than the existing ones. E.g. if
   `J11-headers-perf.spec.ts` is the highest, you become `J12-...`.
2. **Copy the structure** of an existing journey that resembles yours:
   - Pure read-only? Copy `J04-gaps.spec.ts`.
   - Write + cleanup? Copy `J05-templates.spec.ts`.
   - Async background work? Copy `J03-knowledge.spec.ts` (uses `pollUntil`).
3. **Reuse helpers** from `_lib/`:
   - `loginAsE2EBot` is automatic — your spec inherits the
     authenticated-journeys project's storageState.
   - Use `pollUntil` for any "wait until status=ready" pattern.
   - Use `cleanup.*` for tear-down so the tenant stays clean.
   - Add new fixtures (markdown / audio / etc.) to `fixtures/` and
     export them from `_lib/fixtures.ts`.
4. **Hard fail vs soft fail** — distinguish in `expect()`:
   - `expect(value).toBe(...)` for hard correctness checks.
   - `expect.soft()` for performance / non-critical assertions
     (page-load <3s, header presence) so they show up but don't gate.
5. **Update [`docs/testing/test-suite-plan.md`](../../../../docs/testing/test-suite-plan.md)
   §5 with a row for the new journey.**

## Architecture

```
prod-tenant/
├── _lib/
│   ├── auth.ts         loginAsE2EBot, persistAuthState, logoutE2EBot
│   ├── poll.ts         pollUntil — generic async-status polling
│   ├── cleanup.ts      deleteKnowledgeBase, deleteTemplate, etc.
│   └── fixtures.ts     KB_FIXTURE, AUDIO_FIXTURE — paths + canaries
├── _config/
│   ├── playwright.prod.config.ts
│   └── storageState.json    gitignored — written by J01, read by J02..J11
├── fixtures/
│   ├── e2e-fixture.md       KB document with the canary string
│   ├── e2e-fixture.wav      ~3s audio for J06 (regenerate via generate.sh)
│   └── generate.sh          regenerator (TTS via espeak / macOS say)
└── J01-login.spec.ts ... J11-headers-perf.spec.ts
```

The Playwright config defines two projects:

1. **`login`** — runs `J01-login.spec.ts` first; persists
   `_config/storageState.json`.
2. **`authenticated-journeys`** — depends on `login`; runs everything
   else with the saved state. No re-login, no re-TOTP.

## Gotchas

- **TOTP drift**: if your local clock skews >30s the codes mismatch.
  The CI runner is NTP-synced so this is mostly a local issue.
- **Storage-state stale**: if J01's run is older than the session-cookie
  TTL, J02..J11 will 401 on first request. Re-run with
  `--project=login` to refresh.
- **Caddy upstream missing for the tenant**: the `tenant_container_no_route`
  audit-event from SPEC-INFRA-CONTAINER-HYGIENE-001 REQ-5 fires for
  `librechat-e2e-bot` if the tenant was deprovisioned without Caddy
  cleanup. Check `service:klai-orphan-audit AND _time:[now-1h,now]`
  in VictoriaLogs after a failing run.
- **Rate-limiting**: portal-api has signup + login rate-limits. The
  bot is exempted via tenant whitelist (set up in Fase 1, see plan §7).

## Failure debugging

Always check three things first when a journey turns red in CI:

1. **HTML report artifact** — `playwright-report-prod-tenant/` is
   uploaded by the workflow. Open `index.html`, click the failing
   spec, see screenshots + traces.
2. **VictoriaLogs request_id** — every test request carries an
   `X-Request-ID` header (Caddy-generated). Find it in the trace
   and query `request_id:<uuid>` in the victorialogs MCP for the
   full server-side chain.
3. **Recent deploy** — was a service deployed in the last 5 min?
   Check `gh run list --branch main --limit 5` and correlate timing.

See plan §10 for symptom→check mapping.
