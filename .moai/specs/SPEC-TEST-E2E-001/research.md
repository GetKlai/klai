# SPEC-TEST-E2E-001 — Research

Inventory of the Klai portal's user-facing routes, captured by reading `klai-portal/frontend/src/routes/` on 2026-04-29. The smoke test journeys map directly onto this inventory; any route added later that this SPEC does not cover should be added in a v1.1 amendment.

## Auth & Bootstrap Routes (excluded from the journey scope)

These routes are part of the auth flow and are explicitly NOT exercised by the runner (see R5 + Exclusions).

- [routes/index.tsx](klai-portal/frontend/src/routes/index.tsx) — root redirect logic
- [routes/login.tsx](klai-portal/frontend/src/routes/login.tsx)
- [routes/callback.tsx](klai-portal/frontend/src/routes/callback.tsx)
- [routes/logged-out.tsx](klai-portal/frontend/src/routes/logged-out.tsx)
- [routes/no-account.tsx](klai-portal/frontend/src/routes/no-account.tsx)
- [routes/signup.tsx](klai-portal/frontend/src/routes/signup.tsx) and [$locale/signup/](klai-portal/frontend/src/routes/$locale/signup/)
- [routes/verify.tsx](klai-portal/frontend/src/routes/verify.tsx)
- [routes/select-workspace.tsx](klai-portal/frontend/src/routes/select-workspace.tsx)
- [routes/provisioning.tsx](klai-portal/frontend/src/routes/provisioning.tsx)
- [routes/join-request.tsx](klai-portal/frontend/src/routes/join-request.tsx)
- [routes/password/forgot.tsx](klai-portal/frontend/src/routes/password/forgot.tsx) and [routes/password/set.tsx](klai-portal/frontend/src/routes/password/set.tsx)
- [routes/$locale/password/forgot.tsx](klai-portal/frontend/src/routes/$locale/password/forgot.tsx)
- [routes/setup/2fa.tsx](klai-portal/frontend/src/routes/setup/2fa.tsx) and [routes/setup/mfa.tsx](klai-portal/frontend/src/routes/setup/mfa.tsx)

The runner verifies the **absence** of these in the post-login navigation (Journey 0 / R2).

## App Routes (in journey scope)

Located under [routes/app/](klai-portal/frontend/src/routes/app/) — these are the routes journeys 1-10 walk through.

| Route file | Smoke journey |
|---|---|
| [app/route.tsx](klai-portal/frontend/src/routes/app/route.tsx) | Journey 1 — shell layout |
| [app/index.tsx](klai-portal/frontend/src/routes/app/index.tsx) | Journey 0 — bootstrap landing |
| [app/account.tsx](klai-portal/frontend/src/routes/app/account.tsx) | Journey 1 — account |
| [app/chat.tsx](klai-portal/frontend/src/routes/app/chat.tsx) | Journey 2 — chat |
| [app/_components/ChatConfigBar.tsx](klai-portal/frontend/src/routes/app/_components/ChatConfigBar.tsx) | Journey 2 — KB picker / model picker selectors |
| [app/knowledge/index.tsx](klai-portal/frontend/src/routes/app/knowledge/index.tsx) | Journey 3 — KB list |
| [app/knowledge/new.tsx](klai-portal/frontend/src/routes/app/knowledge/new.tsx) | Journey 4 — KB create form |
| [app/knowledge/$kbSlug/index.tsx](klai-portal/frontend/src/routes/app/knowledge/$kbSlug/index.tsx) | Journey 3 — KB landing |
| [app/knowledge/$kbSlug/overview.tsx](klai-portal/frontend/src/routes/app/knowledge/$kbSlug/overview.tsx) | Journey 3 — overview tab |
| [app/knowledge/$kbSlug/members.tsx](klai-portal/frontend/src/routes/app/knowledge/$kbSlug/members.tsx) | Journey 3 — members tab (READ-ONLY) |
| [app/knowledge/$kbSlug/settings.tsx](klai-portal/frontend/src/routes/app/knowledge/$kbSlug/settings.tsx) | Journey 3 — settings tab (READ-ONLY) |
| [app/knowledge/$kbSlug/taxonomy.tsx](klai-portal/frontend/src/routes/app/knowledge/$kbSlug/taxonomy.tsx) | Journey 3 — taxonomy tab |
| [app/knowledge/$kbSlug/advanced.tsx](klai-portal/frontend/src/routes/app/knowledge/$kbSlug/advanced.tsx) | Journey 3 — advanced tab |
| [app/knowledge/$kbSlug_.add-source.tsx](klai-portal/frontend/src/routes/app/knowledge/$kbSlug_.add-source.tsx) | Journey 4 — add text source form |
| [app/knowledge/$kbSlug_.add-connector.tsx](klai-portal/frontend/src/routes/app/knowledge/$kbSlug_.add-connector.tsx) | EXCLUDED — connector OAuth (R5) |
| [app/knowledge/$kbSlug_.edit-connector.$connectorId.tsx](klai-portal/frontend/src/routes/app/knowledge/$kbSlug_.edit-connector.$connectorId.tsx) | EXCLUDED — connector edit (R5) |
| [app/docs/$kbSlug/index.tsx](klai-portal/frontend/src/routes/app/docs/$kbSlug/index.tsx) | Journey 3 — docs viewer landing |
| [app/docs/$kbSlug/$pageId.tsx](klai-portal/frontend/src/routes/app/docs/$kbSlug/$pageId.tsx) | Journey 3 — single page |
| [app/templates/index.tsx](klai-portal/frontend/src/routes/app/templates/index.tsx) | Journey 5 — template list |
| [app/templates/new.tsx](klai-portal/frontend/src/routes/app/templates/new.tsx) | Journey 5 — template create form |
| [app/templates/$slug.edit.tsx](klai-portal/frontend/src/routes/app/templates/$slug.edit.tsx) | Journey 5 — template edit |
| [app/meetings/start.tsx](klai-portal/frontend/src/routes/app/meetings/start.tsx) | Journey 6 — render only, no `Start meeting` click (R5 + blocklist) |
| [app/meetings/$meetingId.tsx](klai-portal/frontend/src/routes/app/meetings/$meetingId.tsx) | Journey 6 — open if a recent meeting exists |
| [app/transcribe/index.tsx](klai-portal/frontend/src/routes/app/transcribe/index.tsx) | Journey 7 — list |
| [app/transcribe/add.tsx](klai-portal/frontend/src/routes/app/transcribe/add.tsx) | EXCLUDED — write |
| [app/transcribe/$transcriptionId.tsx](klai-portal/frontend/src/routes/app/transcribe/$transcriptionId.tsx) | Journey 7 — detail |
| [app/scribe.tsx](klai-portal/frontend/src/routes/app/scribe.tsx) | Journey 8 — render |
| [app/focus.tsx](klai-portal/frontend/src/routes/app/focus.tsx) | Journey 9 — focus root |
| [app/focus/$.tsx](klai-portal/frontend/src/routes/app/focus/$.tsx) | Journey 9 — catch-all focus child |
| [app/gaps/index.tsx](klai-portal/frontend/src/routes/app/gaps/index.tsx) | Journey 10 — gaps list |

## Admin Routes (Journey 11 — R6 conditional)

Located under [routes/admin/](klai-portal/frontend/src/routes/admin/). Render-only — no clicks on rotate/regenerate/delete/invite buttons.

| Route file | Smoke journey | Notes |
|---|---|---|
| [admin/route.tsx](klai-portal/frontend/src/routes/admin/route.tsx) | J11 shell | |
| [admin/index.tsx](klai-portal/frontend/src/routes/admin/index.tsx) | J11 landing | |
| [admin/users/](klai-portal/frontend/src/routes/admin/users/) | J11 users | No suspend/offboard clicks |
| [admin/groups/](klai-portal/frontend/src/routes/admin/groups/) | J11 groups | |
| [admin/api-keys/](klai-portal/frontend/src/routes/admin/api-keys/) | J11 api-keys | List + 1 detail tab render only; no rotate/regenerate (R5) |
| [admin/widgets/](klai-portal/frontend/src/routes/admin/widgets/) | J11 widgets | List + 1 detail tab render only; no embed copy/rotate (R5) |
| [admin/mcps/](klai-portal/frontend/src/routes/admin/mcps/) | J11 mcps | |
| [admin/domains.tsx](klai-portal/frontend/src/routes/admin/domains.tsx) | J11 domains | No add/remove domain |
| [admin/join-requests.tsx](klai-portal/frontend/src/routes/admin/join-requests.tsx) | J11 join-requests | No approve/reject |
| [admin/templates/](klai-portal/frontend/src/routes/admin/templates/) | J11 templates (admin) | |
| [admin/billing.tsx](klai-portal/frontend/src/routes/admin/billing.tsx) and [admin/billing.lazy.tsx](klai-portal/frontend/src/routes/admin/billing.lazy.tsx) | J11 billing | Render only, no plan change (R5) |
| [admin/settings.tsx](klai-portal/frontend/src/routes/admin/settings.tsx) | J11 settings | Render only, no save (R5) |

## Key Components Referenced

- [components/layout/Sidebar.tsx](klai-portal/frontend/src/components/layout/Sidebar.tsx) — primary nav, also exposes admin-role visibility for R6 detection.
- [components/help/HelpButton.tsx](klai-portal/frontend/src/components/help/HelpButton.tsx) — present on most pages; passive element, not exercised.
- [components/ui/LocaleSwitcher.tsx](klai-portal/frontend/src/components/ui/LocaleSwitcher.tsx) — Journey 1 locale-switch test target.

## Vite / Env Configuration

From [klai-portal/frontend/vite.config.ts](klai-portal/frontend/vite.config.ts) (grepped during research):

- Sentry org URL: `https://errors.getklai.com`
- API proxy default target (dev): `https://getklai.getklai.com`
- OIDC authority (`.env.local`): `https://auth.getklai.com`

This confirms `getklai.getklai.com` is a valid first-party tenant in the dev/prod posture and matches the SPEC's target URL.

## Backend Surface (out of journey scope, listed for context)

The runner only interacts via the portal UI and does not call backend services directly. For traceability the relevant services are:

- portal-api ([klai-portal/backend](klai-portal/backend)) — auth, KB metadata, templates, meetings.
- klai-knowledge-ingest — background ingestion (the runner relies on the UI's status indicator).
- klai-retrieval-api — chat retrieval (exercised indirectly via Journey 2).
- klai-connector — connector OAuth (out of scope).
- klai-mailer — email (out of scope).
- klai-research-api — notebooks (only touched if a Journey opens `/app/notebook` — currently not in the inventory).

If the SPEC is later expanded to cover notebooks or scribe deeper functionality, this section should be updated.

## Open Questions Resolved Before File Creation

1. **Which Playwright MCP — `mcp__playwright` or `mcp__playwright-isolated`?**
   Decision: `mcp__playwright` (shared session). The user logs in once and that session must persist; isolated would require headless re-auth which violates the simplicity of the runbook. Captured in spec.md § Environment.
2. **Should the runner attempt admin journeys if the user is not admin?**
   Decision: skip with `warn` per R6, do not abort the run. Captured in R6 + A6.
3. **What if the tenant has zero KBs?**
   Decision: Journey 3 records `warn` with reason `"no KB available"`. Captured in plan.md § Failure Modes.
4. **What happens if KB indexing for the throwaway source takes longer than the budget?**
   Decision: 60-second timeout; if not ready, skip the chat-against-it sub-step with `warn` but still register for cleanup. Captured in plan.md.
5. **Where do screenshots and reports live?**
   Decision: `.tmp/e2e-screenshots/` and `.tmp/e2e-reports/`. Both should be in `.gitignore` (verified before run; if not, the runner will add a one-line gitignore entry — that is the only source-tree change permitted by this SPEC).
