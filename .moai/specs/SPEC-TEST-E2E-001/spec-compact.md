# SPEC-TEST-E2E-001 — Compact

## Requirements

- **R1 (Ubiquitous):** Read-only by default; writes only via R4 enumeration with prefix `e2e-test-{YYYYMMDD}-`.
- **R2 (Event-driven):** WHEN runner starts THEN verify auth (`/app/*`, no redirect); abort with chat instruction otherwise.
- **R3 (Event-driven):** WHEN journey completes THEN record status (pass/warn/fail), console errors, 4xx/5xx, slowest call >3s, screenshot on fail.
- **R4 (State-driven):** IF throwaway resource created THEN prefix `e2e-test-{YYYYMMDD}-` + cleanup-registry tracking + delete before exit, including on failure. Permitted writes: 1 KB, 1 source in that KB, 1 chat prompt against it, 1 template. All deleted at cleanup.
- **R5 (Unwanted):** No signup, MFA, password change, billing change, connector OAuth, meeting-bot start, widget embed, API key rotate, workspace settings mutation, or deletion of non-R4 resources.
- **R6 (Optional):** Where admin role: read-only smoke on /admin/* pages; if not admin, skip with `warn`.

## Acceptance Criteria

- **A1 Login bootstrap:** Runner reaches `/app/*` after user login.
- **A2 Authenticated-session abort:** No login → emit chat instruction, no journeys execute, only stub report.
- **A3 Chat read-only:** First KB picked, prompt sent, streamed response, no 5xx, no US model names → `pass`.
- **A4 Throwaway create+cleanup:** Create KB `e2e-test-{date}-smoke-kb` + source + chat-against-it → cleanup deletes both → list confirms absence → `pass`.
- **A5 Cleanup-on-failure:** Mid-run exception → failure handler processes cleanup registry → zero `e2e-test-` objects remain → report status `fail` with exception location.
- **A6 Excluded-action invariant:** Blocklist match before click → click refused → journey `warn` with `"skipped — excluded action: <phrase>"` → run continues.
- **A7 Report fields:** Markdown report at `.tmp/e2e-reports/smoke-{YYYYMMDD-HHmm}.md` includes per-journey: ordinal, name, status, console errors (count + first 3 verbatim), slowest call >3s, screenshot path on fail. Bottom line `Confidence: {0-100} — {evidence}`.

## Files to Modify

None. SPEC produces only `.moai/specs/SPEC-TEST-E2E-001/` documents and runtime `.tmp/` artifacts (gitignored).

## Exclusions

- No functional correctness validation (search relevance, transcription accuracy, LLM output quality).
- No CI integration / test framework scaffolding (Pytest, Vitest, Playwright Test runner).
- No cross-browser, mobile, or performance load testing.
- No accessibility audit.
- No authentication flow testing (login is a manual user step).
- No data fixture seeding beyond R4 throwaway list.
- No source-code modifications anywhere in the repository.
- No actions matching the Selector Blocklist (Start meeting, Connect *, Rotate, Regenerate, Delete workspace, etc.).
- No connector OAuth flows that would consume external credentials.
- No Vexa meeting-bot starts.
