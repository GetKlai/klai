---
id: SPEC-TEST-E2E-001
version: "1.1.0"
status: completed
created: "2026-04-29"
updated: "2026-04-30"
author: MoAI
priority: medium
issue_number: 0
lifecycle: spec-first
---

## HISTORY

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0.0 | 2026-04-29 | MoAI | Initial draft — Klai portal smoke test against the live `getklai.getklai.com` tenant. |
| 1.1.0 | 2026-04-30 | MoAI | First execution complete; status `draft` → `completed`. See "Implementation Notes (2026-04-30 Run)" at the bottom of this document. |

# SPEC-TEST-E2E-001: Klai Portal End-to-End Smoke Test

## Overview

This specification defines an interactive Playwright-driven smoke test of the Klai portal against the live `getklai.getklai.com` tenant. The runner is an AI-orchestrated session (Claude + `mcp__playwright`), not a CI test suite. The user logs in once via the shared Playwright browser; afterwards the runner autonomously walks predefined journeys, records pass/warn/fail per journey, and produces a markdown report with screenshots.

The intent is **route-render and basic-API smoke coverage**, not full functional validation. The SPEC's value is in providing a repeatable, scope-bounded, cleanup-safe runbook that exercises the entire visible surface of the portal without risking destructive side-effects on a real tenant.

## Environment

- **Target tenant:** `https://getklai.getklai.com` (production-equivalent multi-tenant Klai instance).
- **Runner:** Claude Code session driving Playwright via `mcp__playwright__*` MCP tools (NOT `mcp__playwright-isolated__*` — the user's pre-authenticated session must be reused).
- **Auth provider:** Zitadel (`https://auth.getklai.com`) — login is performed once by the human user before the runner starts; the runner never touches credentials.
- **Frontend stack:** React + TanStack Router, routes under `klai-portal/frontend/src/routes/` (see `research.md`).
- **Backend stack:** FastAPI portal-api, knowledge-ingest, retrieval-api, connector, scribe, mailer, research-api (the runner only interacts via the public portal UI, not directly with these services).
- **Local artifacts:**
  - Markdown report: `.tmp/e2e-reports/smoke-{YYYYMMDD-HHmm}.md`
  - Screenshots on fail: `.tmp/e2e-screenshots/{YYYYMMDD-HHmm}/{journey-name}.png`

## Assumptions

- A1: The human user is logged in as a tenant member of `getklai.getklai` before the runner starts; the runner verifies but never authenticates.
- A2: The user is an admin of the tenant. If not, R6 (admin journey) is downgraded to skip with a `warn` status, not `fail`.
- A3: The tenant already contains at least one knowledge base and at least one template; the runner uses the first existing KB it sees for read-only checks.
- A4: `mcp__playwright__*` retains the cookies/storage state established by the human login for the entire run.
- A5: Throwaway names prefixed `e2e-test-{YYYYMMDD}-` will not collide with real tenant data on any reasonable inspection.
- A6: Console-error capture and network-request capture are available via `browser_console_messages` and `browser_network_requests` MCP tools.
- A7: The cleanup phase runs deterministically before exit, including on early termination (Python-`finally`-equivalent semantics in the orchestration loop).

## Requirements

### R1 — Ubiquitous: Read-only by default

The system shall, by default, perform only read-only operations against the live `getklai.getklai.com` tenant; any write operation (resource creation, modification, deletion) shall be explicitly enumerated in this SPEC under R4 and shall use the throwaway naming convention `e2e-test-{YYYYMMDD}-{slug}`.

### R2 — Event-driven: Login bootstrap verification

WHEN the runner starts and navigates to `https://getklai.getklai.com` THEN the system shall verify the Playwright session is authenticated by asserting the resolved URL matches `/app/*` (no redirect to `/login`, `/signup`, `/select-workspace`, `/no-account`, or any IdP page); IF authentication is not detected THEN the runner shall abort with a clear chat message instructing the user to log in manually before retrying, and shall not execute any journey.

### R3 — Event-driven: Per-journey result capture

WHEN each journey completes (pass, warn, or fail path) THEN the system shall record:
- the journey name and ordinal,
- a status of `pass` / `warn` / `fail`,
- the count and first-three texts of console errors observed during the journey,
- any HTTP 4xx or 5xx network responses with route + status code,
- the slowest network call's route and duration in ms (only flagged if >3s),
- a screenshot path under `.tmp/e2e-screenshots/{YYYYMMDD-HHmm}/{journey-name}.png` if the status is `fail`.

The runner shall continue to the next journey on `warn` or non-fatal `fail`; only an authentication loss or browser session crash terminates the run early.

### R4 — State-driven: Throwaway-object lifecycle

IF the runner creates a throwaway resource (knowledge base, knowledge-base source, template) THEN:
- the resource name SHALL start with `e2e-test-{YYYYMMDD}-`,
- the resource SHALL be tracked in an in-memory cleanup registry keyed by resource type and identifier,
- the resource SHALL be deleted in the cleanup phase before the runner reports the run summary,
- on early termination via the failure handler the cleanup registry SHALL still be processed, leaving zero objects with the `e2e-test-` prefix in the tenant.

The exhaustive list of permitted writes:
1. Create one knowledge base named `e2e-test-{YYYYMMDD}-smoke-kb`.
2. Add one short text source ("Smoke test content — see SPEC-TEST-E2E-001") to that knowledge base.
3. Submit one chat prompt scoped to that knowledge base.
4. Create one template named `e2e-test-{YYYYMMDD}-tpl`.
5. Delete (1), (2), and (4) in the cleanup phase.

No other writes are permitted by this SPEC.

### R5 — Unwanted Behavior: Excluded destructive actions

The system shall not, under any condition, perform any of the following actions:
- Account signup or invite-acceptance.
- MFA enrollment, MFA reset, or password change.
- Billing plan change, billing portal redirect, payment method update.
- Connector OAuth flow (Google Drive, Notion, Microsoft 365, GitHub, Moneybird, etc.).
- Meeting-bot start (Vexa recorder), meeting deletion, transcription deletion.
- Widget embedding outside the tenant or rotation of widget embed tokens.
- API key creation, rotation, regeneration, or deletion.
- Workspace settings mutations (domain whitelist, member role edits, KB ownership transfer, group membership edits, MCP credential edits).
- Deletion of any resource not created by this SPEC's R4 list.

### R6 — Optional: Admin coverage

Where the logged-in user has admin role in the tenant, the system shall additionally execute read-only smoke checks on the admin surface (`/admin/users`, `/admin/groups`, `/admin/api-keys`, `/admin/widgets`, `/admin/mcps`, `/admin/domains`, `/admin/join-requests`, `/admin/templates`, `/admin/settings`, `/admin/billing`); if the user is not admin, these journeys shall be skipped with status `warn` and reason `"not admin in this tenant"`, and the run shall not be considered failed for that reason alone.

## Specifications

### Journey Inventory

The following journeys execute in order. Order matters for cleanup safety: write journeys precede the journeys that depend on them, and cleanup is the last journey before the report.

| # | Journey | Routes / Action | Type |
|---|---|---|---|
| 0 | Login bootstrap | `/` → assert `/app/*` | read |
| 1 | Account & shell | `/app/account`, sidebar render, locale switch | read |
| 2 | Chat | `/app/chat` — pick KB, send 1 prompt, await stream | read |
| 3 | KB list + read-only KB tour | `/app/knowledge`, then for first 2 KBs: overview, members, settings, taxonomy, advanced, docs viewer + 1 page | read |
| 4 | KB write | Create `e2e-test-{date}-smoke-kb`, add text source, query via chat | **write (R4)** |
| 5 | Templates | `/app/templates` list + open existing edit; create `e2e-test-{date}-tpl` | **write (R4)** |
| 6 | Meetings | `/app/meetings/start` render only, `/app/meetings/{id}` if a recent meeting exists | read |
| 7 | Transcribe | `/app/transcribe` list, open one transcription | read |
| 8 | Scribe | `/app/scribe` render | read |
| 9 | Focus | `/app/focus` render | read |
| 10 | Gaps | `/app/gaps` render | read |
| 11 | Admin (R6) | `/admin/*` — users, groups, api-keys list, widgets list, mcps, domains, join-requests, templates, settings, billing — render only | read (admin) |
| 12 | Cleanup | Delete throwaway KB, source, template; verify deletion | **write (R4)** |
| 13 | Report | Aggregate results, write markdown to `.tmp/e2e-reports/` | local |

### Selector Blocklist

To enforce R5 mechanically, the runner shall maintain a static blocklist of UI elements that must NEVER be clicked. The orchestrator must pattern-match selectors before any `browser_click` call and refuse the click if it matches:

- Buttons or links with text matching (case-insensitive): `delete workspace`, `transfer ownership`, `rotate key`, `regenerate`, `revoke`, `disconnect`, `start meeting`, `start recording`, `connect google`, `connect notion`, `connect microsoft`, `connect github`, `change plan`, `upgrade plan`, `cancel plan`, `add payment`, `invite member`, `remove member`, `delete user`, `suspend user`, `offboard`, `enable mfa`, `reset password`, `change password`.
- Any modal confirm button preceded by a heading containing `Delete`, `Remove`, `Disconnect`, `Cancel subscription`, `Rotate`, `Regenerate`, `Reset`.

### Output Format

The markdown report shall include:
- Header with timestamp, branch, target URL, runner version (SPEC version).
- A summary line `pass / warn / fail` totals.
- A per-journey table: ordinal, name, status, console errors, slow calls, screenshot path.
- A "Findings" section with the first three console errors of any failing journey, verbatim.
- A "Cleanup verification" section confirming zero `e2e-test-` objects remain.
- A "Confidence" line at the bottom with a numeric score and one-line evidence summary, per the `report-confidence` project rule.

### Exclusions

This SPEC explicitly excludes:
- Any **functional correctness** validation beyond route-rendering and basic API response (no assertion of search relevance, retrieval quality, transcription accuracy, or LLM output content).
- **CI integration** — this is a one-shot interactive runbook, not an automated pipeline test.
- **Test framework** scaffolding (Pytest, Vitest, Playwright Test runner config). The orchestration is performed by Claude inline via MCP calls; if a recurring CI smoke is later desired, that becomes a separate SPEC.
- **Cross-browser** coverage — Chromium-only via the user's existing Playwright session.
- **Performance benchmarking** beyond the >3s slow-call flag.
- **Accessibility audit** — out of scope; covered by other SPECs if any.
- **Authentication flow validation** — login is a manual user step, not a journey.
- **Data fixture seeding** — the runner relies on existing tenant content (R4 is the only write surface).

## Files Affected

This SPEC produces no source-code modifications. The only files created live under:

- `.moai/specs/SPEC-TEST-E2E-001/` — SPEC documents (this directory).
- `.tmp/e2e-reports/` (gitignored) — runtime report output.
- `.tmp/e2e-screenshots/` (gitignored) — runtime screenshot output.

No portal frontend, backend, or infra files are modified.

## MX Tag Plan

No code is written by this SPEC; the MX tag plan is empty. Future work (e.g. converting this runbook into a Playwright Test suite) would re-evaluate MX tags at that point.

## Implementation Notes (2026-04-30 Run)

First execution of this runbook completed on **2026-04-30 ~07:46–08:00 UTC** by `mark.vletter@voys.nl` against the live `getklai.getklai.com` tenant. Detailed report at `.tmp/e2e-reports/smoke-20260430-0945.md` (gitignored — kept locally per SPEC § Files Affected).

### Run summary

| Status | Count | Journeys |
|---|---|---|
| pass | 9 | J0 login, J1 account+locale, J3 KB read tour, J5 templates, J6 meetings, J7 transcribe, J8 scribe, J10 gaps, J12 cleanup |
| warn | 2 | J4 KB write (chat-query inherited from J2), J9 focus (route does not exist in current portal) |
| fail | 2 | J2 chat (502 on chat-iframe SSO), J11 admin tour (500 on `/api/admin/domains` and `/api/admin/join-requests`) |

R4 + R5 invariants both held: zero `e2e-test-` residuals after cleanup, no blocklisted action executed.

### Findings shipped

The two `fail` journeys exposed real production regressions, both resolved within the same day:

- **J2 chat SSO 502** — callback-URL allowlist rejected `chat-{slug}.getklai.com` LibreChat tenant hosts. Resolved by [PR #243](https://github.com/GetKlai/klai/pull/243) (`chat-` prefix strip + `_STATIC_SYSTEM_SUBDOMAINS` enumeration). Follow-up [PR #248](https://github.com/GetKlai/klai/pull/248) derives the allowlist from Zitadel at startup so future host classes don't require a code change. Verified post-deploy: chat iframe renders the LibreChat "Welcome back" UI, 0 console errors, `chat-getklai.getklai.com/api/config` returns 200.
- **J11 admin 500s** — `alembic_version` was at head but the SPEC-AUTH-006 migrations (`23c5c8b48669` add `portal_org_allowed_domains`, `b2c3d4e5f6g7` add `portal_join_requests`) had never run their `upgrade()` body in production. Fixed by applying alembic's offline `--sql` output directly via `psql` on `klai-core-postgres-1` (alembic_version untouched, schema caught up). Default privileges + sequence grants verified end-to-end through a UI POST/DELETE round-trip on `/admin/domains` (201 → 204). The same-shape regression risk is now documented as the `alembic-stamped-past-skipped-migration (HIGH)` pitfall added to `.claude/rules/klai/pitfalls/process-rules.md` in [PR #242](https://github.com/GetKlai/klai/pull/242).

Multi-service alembic drift sweep ran clean: portal-api now matches schema, klai-connector at head `006_add_org_id_to_sync_runs`, scribe-api at head `0007_c5f9e3a4`, all sentinel columns present. Mailer / knowledge-ingest / retrieval-api have no alembic chain (Redis-only or raw-SQL bootstrap) and are not vulnerable to this bug class.

### SPEC scope drift observed

`spec.md` § Journey Inventory listed `/app/focus` as Journey 9, but that route currently redirects to `/app/knowledge` in the live portal. Recorded as `warn` rather than `fail`. Either the route was retired between SPEC-creation (2026-04-29) and execution (2026-04-30), or the SPEC inventory was authored against a stale routes inventory. A v1.0.1 patch could drop J9 from the inventory; deferred until a second run confirms the route really is gone.

### Lifecycle

This is a `spec-first` SPEC: the SPEC describes a one-shot runbook, not a maintained product feature. Status marked `completed`. Future re-runs of the smoke test do NOT require SPEC updates unless the journey inventory or tenant scope changes — in which case bump to v1.1.x with a new HISTORY entry.

### Confidence

`92` — 13/13 journeys executed end-to-end against live prod; both `fail` findings have shipped fixes verified in production (chat-iframe via Playwright, admin endpoints via UI POST/DELETE round-trip); cleanup-roundtrip invariant proved via API responses (KB DELETE 204 + GET 404). `-8` reflects: J4's chat-query step was inherited-skipped (the create+cleanup R4 invariant did pass, but A3-style retrieval validation is still unverified end-to-end), and J9's redirect was not investigated to confirm whether `/app/focus` is intentionally gone or accidentally broken.
