# Sync Report — SPEC-PORTAL-CONNECTORS-TAB-CLEANUP-001

**Timestamp:** 2026-05-13T11:14:54Z  
**Workflow:** `/moai sync SPEC-PORTAL-CONNECTORS-TAB-CLEANUP-001`  
**Branch:** `mvletter/connectors-tab-cleanup`  
**Delivered commit:** `0d043ccb` (`refactor portal connectors tab`)  
**Target:** `origin/main`

## Summary

The connectors tab cleanup was implemented and delivered to `main`.
The original borderline SPEC was resolved as a narrow behavior-preserving
split rather than a larger state-machine or app-wide helper refactor.

## Implementation

Changed frontend files:

- `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/connectors.tsx`
  - Reduced from 425 lines to 266 lines.
  - Kept route ownership of data fetching, ownership checks, OAuth
    banners, live progress query assembly, and dialogs.
- `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/-connectors-row.tsx`
  - Added row-level connector table rendering and row actions.
- `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/-connectors-hooks.ts`
  - Added feature-local hooks for delete, sync, and reconnect actions.
- `.moai/specs/SPEC-PORTAL-CONNECTORS-TAB-CLEANUP-001/spec.md`
  - Marked `done` with the architecture decision and outcome.

Architecture decision:

- Followed the existing `$kbSlug/sources.tsx` pattern: feature-local
  `-connectors-*` sibling files at the smallest shared scope.
- Did not introduce app-wide helpers under `@/lib/`.
- Did not use `_components/` for this small row split because the
  adjacent sources tab already uses sibling `-sources-row.tsx` /
  `-sources-hooks.ts`, and the extracted surface is route-specific.

## Verification

Local frontend checks after implementation:

- `npm run lint` — passed.
- `npm run i18n:compile && npx tsc -b --force` — passed.
- `npm run test` — passed: 35 files, 255 tests.

Pre-change CodeIndex impact checks:

- `ConnectorsTab` upstream impact: low, no dependent processes.
- `handleSync` upstream impact: low, direct caller only `ConnectorsTab`.
- `handleReconnect` upstream impact: low, direct caller only
  `ConnectorsTab`.

## Deployment

Pushed directly to `origin/main` as requested. GitHub branch protection
reported bypassed rules for the direct main push.

GitHub checks for `0d043ccb`:

- `SAST — Semgrep` — success.
- `Build and deploy portal-frontend` — success.

Later main pushes occurred after this commit, so subsequent workflow runs
in the GitHub list may reference newer commits.

## Voys Smoke Test

Validated with the Playwright MCP session described in
`docs/setup/mcp-servers.md`, using the shared
`~/.claude/mcp-storageState.json` storage-state flow.

Actions:

- Refreshed MCP storage-state through Google SSO using the Voys Google
  account.
- Verified `/api/me` returned 200 for `mark.vletter@voys.nl` in the
  Voys tenant.
- Opened `https://voys.getklai.com/app/knowledge/support/connectors`.
- Verified Support KB connectors table rendered 3 rows:
  - `Notion Nerds/Voys`
  - `Redcactus`
  - `Voys Help NL`
- Verified status/document counts rendered.
- Verified per-row Sync/Edit/Delete affordances were present.
- Clicked the first row Edit action, verified navigation to the edit
  connector page, then returned to the connectors tab with 3 rows still
  present.

Screenshot:

- `.context/voys-connectors-tab-2026-05-13.png`

No destructive actions were performed. No connector sync was triggered.

## Documentation Sync

Project-wide documents did not require updates:

- No new dependencies.
- No environment variable changes.
- No backend/API contract changes.
- No new top-level directories.
- No user-facing product feature change.

SPEC document already reflects the implementation outcome.

## Status

Ready. The cleanup is implemented, pushed to `main`, deployed, and smoke
tested in the Voys tenant through the correct Playwright MCP session.
