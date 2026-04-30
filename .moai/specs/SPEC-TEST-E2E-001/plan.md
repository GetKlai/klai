# SPEC-TEST-E2E-001 — Implementation Plan

This is a runbook, not a code-change SPEC. "Implementation" here means: Claude executes the journey list against the live tenant inside a single session, using `mcp__playwright__*` MCP tools, and writes the report and screenshots to `.tmp/`.

## Run Mode

- **Driver:** Claude Code session (this session or a future `/moai run SPEC-TEST-E2E-001`).
- **Browser:** `mcp__playwright__*` (shared session). NOT `mcp__playwright-isolated__*` — the user's manual login must persist.
- **Methodology:** Neither TDD nor DDD applies. There is no source code to write or refactor; the SPEC is the test plan and the runner is the test.

## Pre-flight (human + runner together)

1. Human: open a Playwright browser by asking the runner to navigate to `https://getklai.getklai.com`.
2. Human: log in via Zitadel, choose the `getklai.getklai` workspace, land on `/app/*`.
3. Human: type `ingelogd` (or English equivalent) in chat.
4. Runner: confirm session via Journey 0.

## Journey Execution

Journeys execute sequentially in the order listed in `spec.md` § Journey Inventory. Each journey is bracketed by:

1. **Pre-step:** capture initial console state via `browser_console_messages`.
2. **Action steps:** the journey-specific navigation and assertions.
3. **Post-step:** capture console-message diff, capture network requests via `browser_network_requests`, compute slowest call, classify status.
4. **Status push:** append a `JourneyResult` entry to the in-session results list.
5. **Failure branch:** if status is `fail`, capture screenshot to `.tmp/e2e-screenshots/{YYYYMMDD-HHmm}/{journey}.png` and continue (R3 — non-fatal).

Fatal exit conditions (terminate run after running cleanup):

- Authentication loss mid-run (URL redirects to `/login` outside Journey 0).
- Browser session crash (any `mcp__playwright` call returns hard error twice in succession).
- Tenant unreachable (DNS or 5xx on the root domain).

## Cleanup Registry

In-session structure:

```
cleanup_registry = [
  {"type": "kb", "id": "<uuid-or-slug>", "name": "e2e-test-20260429-smoke-kb"},
  {"type": "template", "id": "<uuid>", "name": "e2e-test-20260429-tpl"},
]
```

Cleanup processing order: templates first, then KBs (KB deletion may cascade sources). Each delete is verified by re-fetching the list and confirming absence. If a delete fails, the failure is logged in the report's "Cleanup verification" section but does not block the report — the human can clean up manually using the recorded id.

## Selector Blocklist Enforcement

Before every `browser_click` call, the orchestration layer (Claude in this session) shall:

1. Read the target element's accessible text via `browser_snapshot` or `browser_evaluate`.
2. Compare against the case-insensitive blocklist defined in `spec.md` § Selector Blocklist.
3. If matched: log the journey as `warn` with reason `"skipped — excluded action: <matched phrase>"` and skip the click.

This is a defensive layer in addition to journey-level scoping. The journeys themselves are written never to require a blocklisted click; the blocklist is a belt-and-braces check.

## Failure Modes & Mitigations

| Failure mode | Likelihood | Mitigation |
|---|---|---|
| User logged out mid-run (Zitadel session expiry) | Low | Detect via URL pattern, abort cleanly with cleanup |
| Throwaway KB delete fails (race with indexing) | Medium | Retry once after 10s, then log to cleanup-failure section |
| Vexa meeting-bot accidentally started | Very low (selector blocklist + journey-6 read-only) | Two-layer guard: journey scope + blocklist |
| Connector OAuth opened in new tab | Low | Detect new-tab event, close immediately, log warn |
| Console errors flood the report | Medium | Cap at 3 verbatim errors per journey, summarize count |
| Tenant has zero KBs / templates (read journeys vacuously pass) | Low | Detect empty list, mark journey `warn` with reason |
| Admin pages 403 because user is not admin | Medium | Detect 403, mark all admin journeys `warn` per R6 (skip semantic) |

## Risk Register

- **R-1 — Real-tenant pollution:** mitigated by R4 enumeration + cleanup registry + selector blocklist.
- **R-2 — Production data exposure in screenshots:** screenshots stay in `.tmp/` (gitignored). Report is reviewed by user before any external sharing.
- **R-3 — Vexa cost trigger:** explicit Journey 6 is render-only; selector blocklist guards `start meeting`.
- **R-4 — Connector token consumption:** R5 forbids OAuth; selector blocklist guards `connect *`.
- **R-5 — Customer-facing impact:** the `getklai.getklai` tenant is Klai's own internal tenant per the user's framing. Worst case is 2-3 throwaway objects briefly visible in admin to other Klai team members.

## Reference Implementations

- Existing routes inventory: `klai-portal/frontend/src/routes/app/` and `klai-portal/frontend/src/routes/admin/` (full list captured in `research.md`).
- KB creation flow: `klai-portal/frontend/src/routes/app/knowledge/new.tsx` — used to identify the KB creation form selectors.
- Template creation flow: `klai-portal/frontend/src/routes/app/templates/new.tsx`.
- Sidebar component: `klai-portal/frontend/src/components/layout/Sidebar.tsx` — used to detect admin role visibility.
- Chat config: `klai-portal/frontend/src/routes/app/_components/ChatConfigBar.tsx` — KB picker selector source.

## Task Decomposition

| # | Task | Owner | Output |
|---|---|---|---|
| T0 | Confirm login (Journey 0) | Runner | URL = `/app/*`, sidebar visible |
| T1 | Auth/shell journey | Runner | Account page rendered, locale switch tested |
| T2 | Chat journey | Runner | Streamed response, no 5xx |
| T3 | KB read tour | Runner | First 2 KBs × 5 tabs render |
| T4 | KB write + chat-against-it | Runner | Throwaway KB created, registered for cleanup |
| T5 | Templates journey | Runner | Existing template opens, throwaway created, registered |
| T6 | Meetings render | Runner | `/app/meetings/start` renders, no start click |
| T7 | Transcribe journey | Runner | List + 1 detail render |
| T8 | Scribe render | Runner | Page renders |
| T9 | Focus render | Runner | Page renders |
| T10 | Gaps render | Runner | Page renders |
| T11 | Admin tour (if admin) | Runner | 10 admin pages render or skip with warn |
| T12 | Cleanup | Runner | All registered objects deleted, verified |
| T13 | Report write | Runner | `.tmp/e2e-reports/smoke-{YYYYMMDD-HHmm}.md` |

## Quality Gate

This SPEC has no source-code lint/test gate. The acceptance criteria in `acceptance.md` ARE the gate. The run is considered acceptable when:

- All 7 acceptance scenarios are satisfied.
- Cleanup verification shows zero `e2e-test-` objects remaining in the tenant.
- The report includes a Confidence line with a numeric score and observable-evidence summary.

## Out of Scope

- Conversion to a recurring CI smoke (separate SPEC if desired).
- Functional correctness assertions (search relevance, transcription accuracy, LLM output quality).
- Cross-browser, mobile, or performance load testing.
- Accessibility audit.
- Localization audit beyond the locale switcher render check.
