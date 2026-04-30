# SPEC-TEST-E2E-001 — Acceptance Criteria

Seven Given/When/Then scenarios. The run is acceptable when all seven evaluate true and the cleanup verification shows zero residual `e2e-test-` objects.

## A1 — Login bootstrap (happy path)

- **Given** the human user has logged into `https://getklai.getklai.com` via the shared Playwright browser and landed on `/app` or `/app/index`,
- **When** the runner navigates the Playwright browser to `https://getklai.getklai.com`,
- **Then** the resolved URL matches the pattern `/app/*` with no redirect to `/login`, `/signup`, `/select-workspace`, `/no-account`, or any IdP page, and the sidebar element from `klai-portal/frontend/src/components/layout/Sidebar.tsx` renders.

## A2 — Authenticated-session abort

- **Given** the Playwright session has no valid Zitadel cookie (e.g. user has not logged in or session expired),
- **When** the runner navigates to `https://getklai.getklai.com` and observes a redirect to `/login` or any auth provider domain,
- **Then** the runner emits a chat message instructing the user to log in manually, exits the journey loop without executing any subsequent journey, and produces no `.tmp/e2e-reports/` file beyond a stub indicating "aborted: not authenticated".

## A3 — Read-only chat journey passes

- **Given** the user is logged in and at least one knowledge base exists in the tenant,
- **When** the runner navigates to `/app/chat`, picks the first knowledge base from the picker, submits the prompt `"e2e smoke test — please reply with OK"`, and waits up to 30 seconds for a streamed response,
- **Then** the chat returns at least one message-rendered token, no console errors are logged during the journey, no HTTP response with status >= 500 is observed on the network, no model option in the model picker contains a US-only LLM brand name (per the `klai-portal-ui` project rule), and the journey is recorded with status `pass`.

## A4 — Throwaway create + cleanup roundtrip

- **Given** the runner is in Journey 4 (KB write),
- **When** the runner creates a knowledge base named `e2e-test-{YYYYMMDD}-smoke-kb`, adds a text source named `e2e-test-{YYYYMMDD}-source` with body `"Smoke test content — see SPEC-TEST-E2E-001"`, registers both for cleanup, queries the KB via chat with prompt `"what does this say?"`, and reaches the cleanup phase,
- **Then** the cleanup phase deletes the source and the KB in that order, the runner re-fetches the KB list and confirms the throwaway KB is absent, and Journey 4 is recorded with status `pass`.

## A5 — Cleanup-on-failure

- **Given** the runner has registered at least one throwaway object in the cleanup registry and a later journey raises an unexpected exception (e.g. browser crash, navigation timeout exceeding the journey budget),
- **When** the runner exits via the failure handler before reaching the normal cleanup journey,
- **Then** the failure handler processes the cleanup registry in the same order as the normal cleanup phase, all registered objects are deleted, the report's "Cleanup verification" section confirms zero `e2e-test-` objects remain, and the report's overall status is `fail` with the exception location recorded in the Findings section.

## A6 — Excluded-action invariant

- **Given** any journey is executing and an action would require clicking a UI element whose accessible text matches the Selector Blocklist in `spec.md` § Selector Blocklist (e.g. `Start meeting`, `Connect Google`, `Rotate key`, `Delete workspace`),
- **When** the orchestration layer pattern-matches the target text against the blocklist before issuing `browser_click`,
- **Then** the click is refused, the journey is recorded with status `warn` and reason `"skipped — excluded action: <matched phrase>"`, and the runner continues to the next journey rather than failing the run.

## A7 — Per-journey report fields and screenshot path

- **Given** the runner has completed all journeys (whether pass, warn, or fail),
- **When** the runner generates the markdown report at `.tmp/e2e-reports/smoke-{YYYYMMDD-HHmm}.md`,
- **Then** the report contains, for each journey: ordinal, name, status, console-error count, first-three console-error texts (verbatim, if any), slowest network call as `{route} {ms}ms` (only listed if >3000ms), and a screenshot path of the form `.tmp/e2e-screenshots/{YYYYMMDD-HHmm}/{journey-slug}.png` if and only if the journey status is `fail`. The report also includes a `Confidence: {0-100} — {evidence summary}` line at the bottom per the `report-confidence` project rule.

## Run-Acceptance Aggregate

The full smoke run is **acceptable** when:

- A1 evaluates true (login bootstrap succeeded), AND
- For each subsequent journey, either the journey-specific scenario evaluates true OR the journey is recorded as `warn` with a documented skip reason (per A6 or per R6 admin-skip), AND
- A4 and A5 cleanup invariants both hold, AND
- A7 report-format fields are all present.

The run is **not acceptable** if:

- Any throwaway object remains in the tenant after cleanup (violation of R4).
- Any blocklisted action was actually executed (violation of R5).
- The report is missing the Confidence line.

A run with multiple `warn` journeys but no `fail` journeys is acceptable; a run with any `fail` journey is acceptable iff cleanup still completed correctly (failures are signal, not blockers).
