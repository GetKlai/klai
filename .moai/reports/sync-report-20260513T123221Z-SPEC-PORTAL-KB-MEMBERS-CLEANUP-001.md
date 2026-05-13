# Sync Report — SPEC-PORTAL-KB-MEMBERS-CLEANUP-001

**Timestamp:** 2026-05-13T12:32:21Z  
**Workflow:** `/moai sync SPEC-PORTAL-KB-MEMBERS-CLEANUP-001`  
**Implementation PR:** #642  
**Merge commit:** `b587af6c`  
**Target:** `origin/main`

## Summary

The KB members cleanup was implemented, merged to `main`, deployed, and
post-deploy smoke tested in the Voys tenant through Playwright MCP.
This sync records the deployment and live verification that happened
after the initial `/moai run` implementation report.

## Implementation

Changed frontend files delivered by PR #642:

- `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/members.tsx`
  - Reduced from 497 lines to 362 lines.
  - Kept route ownership of auth, route params, data queries,
    visibility derivation, owner checks, and confirmation dialog state.
- `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/-members-hooks.ts`
  - Added feature-local mutation hooks for KB visibility updates,
    user/group invite, and user/group removal.
- `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/_components/-InviteSection.tsx`
  - Added shared group/person combobox section rendering.
- `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/_components/-MemberRow.tsx`
  - Added shared group/user member card rendering.
- `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/__tests__/-members-hooks.test.tsx`
  - Added focused mutation-contract coverage.

Architecture decision:

- Followed the existing `$kbSlug` route-local pattern with `-`-prefixed
  helper files so TanStack Router ignores them.
- Did not introduce app-wide helpers under `@/lib/`.
- Did not change backend membership APIs, RBAC, role defaults, or query
  contracts.

## Verification

Local verification before merge:

- `npm ci` — passed; 0 vulnerabilities in portal frontend.
- `npx vitest run 'src/routes/app/knowledge/\$kbSlug/__tests__/-members-hooks.test.tsx'` — passed, 1 file / 3 tests.
- `npm run lint -- --quiet` — passed.
- `npm run i18n:compile && npx tsc -b --force` — passed.
- `npm run security:audit && npm run lint && npm run build` — passed.
- `cd klai-widget && npm ci && npm run build` — passed. Existing
  widget dependency audit still reports 2 moderate findings.
- `python3 scripts/fix-mojibake-locales.py --check` — passed.
- Full `npm test` was attempted but still fails on pre-existing
  admin/user locale expectation mismatches unrelated to this SPEC.

GitHub checks:

- PR #642 `build-deploy` — passed.
- PR #642 `semgrep` — passed.

## Deployment

Merged to `main` via PR #642.

Main workflows:

- `Build and deploy portal-frontend` run `25798876361` — success.
- The workflow completed install, locale check, frontend audit, widget
  bundle build, lint, build, SSH setup, and `Deploy dist to core-01`.

The automatic `E2E prod-tenant` workflow triggered after deploy, but it
failed before application exercise because the required GitHub secrets
were empty (`E2E_BASE_URL`, `E2E_USER_EMAIL`, `E2E_USER_PASSWORD`,
`E2E_TOTP_SECRET`). The same workflow had several consecutive failures
before this SPEC for the same missing-secret condition.

## Voys Smoke Test

Validated with Playwright MCP against `https://voys.getklai.com` after
the main deploy.

Actions:

- Opened `https://voys.getklai.com/`.
- Verified the active Google social-login session by observing redirect
  to `/app` and the authenticated Klai shell.
- Navigated through the UI to `Knowledge`.
- Opened the `Support` knowledge base.
- Loaded `https://voys.getklai.com/app/knowledge/support/members`.
- Verified the members route rendered:
  - `Who can access?` visibility controls.
  - `Team members may also add content` contributor toggle.
  - Group search combobox.
  - Person search combobox.
  - Existing owner row for `mark.vletter@voys.nl`.
- Typed into the person search combobox to exercise the extracted
  `InviteSection` path.

Screenshot:

- `.context/e2e/voys-kb-members-after-main-deploy.png`

No production membership, visibility, contributor, or remove mutations
were performed.

## Documentation Sync

Project-wide documents did not require updates:

- No new dependencies.
- No environment variable changes.
- No backend/API contract changes.
- No top-level directory changes.
- No product-capability changes.

Updated MoAI artifacts:

- `.moai/specs/SPEC-PORTAL-KB-MEMBERS-CLEANUP-001/spec.md`
- `.moai/specs/SPEC-PORTAL-KB-MEMBERS-CLEANUP-001/progress.md`
- `.moai/reports/sync-report-20260513T123221Z-SPEC-PORTAL-KB-MEMBERS-CLEANUP-001.md`

## Status

Ready. The cleanup is implemented, merged to `main`, deployed, and
smoke tested on the Voys tenant through Google social login with
Playwright MCP.
