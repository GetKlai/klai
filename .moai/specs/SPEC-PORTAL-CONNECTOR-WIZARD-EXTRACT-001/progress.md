# SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001 — Progress

## Status: done (2026-05-13)

## Phases shipped

| Phase | Commit | Description |
|---|---|---|
| 1 | `5b514e9b` (in PR #613) | Preflight: rule on portal-frontend.md + SPEC v0.2.0 |
| 2 | `6c60a048` | Eliminate triplicate symbols vs `$kbSlug/-kb-*` |
| 3 | `fef50b58` | Extract wizard-only types to `-connector-types.ts` |
| 4 | `c4bf13cb` | Extract wizard-only constants to `-connector-constants.ts` |
| 5 | `aa0ca9fe` | Extract feedback components, kill cross-route import |

PR #613 admin-merged 2026-05-13 02:36 CEST → commit `9c1b4d77` on main.
Deploy CI #25774863654 green at 02:39 CEST (after PR #617 hotfix unblocked
the inherited billing.lazy.tsx lint failure from PR #614).

Live verification on `voys.getklai.com`:
- AuthProbeFeedback panel rendered correctly on add-connector wizard
  (classification `auth_failed_still_walled`)
- AuthProbeFeedback panel rendered correctly on edit-connector wizard
  (classification `auth_failed_unreachable`)
- Edit-connector page loaded clean — pre-fill `useEffect` populated form
  values, zero console errors across full flow
- Cross-route import dead — verified by `git grep`
- Screenshots saved: `add-connector-auth-probe-panel.png`,
  `edit-connector-auth-probe-panel.png`

## Follow-up cleanups landed in this branch

After v0.2.0 shipped, a self-review identified loose ends. The
2026-05-13 followups branch addressed them:

| # | Commit | Item |
|---|---|---|
| 1 | `c623ffd3` | F-C2 partial: hoist `GitHubConfig` + `WebCrawlerConfig` from `$kbSlug/-kb-types.ts` to `-connector-types.ts` |
| 2 | `edc81f81` | F-C2 cont.: hoist `ASSERTION_MODE_OPTIONS` + `joinSeedUrl` from `$kbSlug/-kb-helpers.tsx` to `-connector-constants.ts`. Removed dead `roleBadge` (no consumer found). Renamed test file. |
| 3 | `d6818af0` | F-S3: new ESLint rule `klai/no-cross-route-import` + 13 unit tests. Extract `KBOverviewSections` to `_components/` to satisfy the rule. `TaxonomyTab` import in `insights.tsx` marked with explicit deferred-fix marker (eslint-disable-next-line + TODO referencing F-table row 1). |
| 4 | `41ee1a3f` | Item 5 from self-review: add `adminLogger.error` / `adminLogger.warn` calls to billing breakdown fetch. Restores Sentry diagnostic trail that the void-prefix hotfix had not added. |
| 5 | `<this commit>` | Item 7 from self-review: retro entry `previous-deploy-failure-blocks-yours` added to `process-rules.md`. SPEC sync: status flipped to `done`, this progress.md added. |

## Outstanding follow-ups (per § Follow-ups)

**UPDATE 2026-05-13 (post-sync):** All 9 god-component candidates from
the F-table have been promoted to their own SPEC documents. The
`§ Follow-ups` section in `spec.md` has been updated to reference each
new SPEC-ID with current status. Below is the canonical pointer list:

### Direct followups to this SPEC (still TODO, no new SPECs created)

- **F-1**: `useConnectorWizardState` hook extraction (real win, real risk).
  Stays as a follow-up, not promoted to its own SPEC yet — needs a
  concrete trigger (a feature touching the wizard state) to be worth
  scheduling.
- **F-2**: Page god-component split for AddConnectorPage / EditConnectorPage.
  Depends on F-1. Same logic.

### Repo-wide god-component cleanup SPECs (new, this commit)

| Source area | New SPEC | Initial status |
|---|---|---|
| `taxonomy.tsx` (move) | `SPEC-PORTAL-TAXONOMY-EXTRACT-001` | ready (small, pickable now) |
| `taxonomy.tsx` (interior split) | `SPEC-PORTAL-TAXONOMY-SPLIT-001` | draft (DDD, post-EXTRACT) |
| `admin/users/index.tsx` | `SPEC-PORTAL-ADMIN-USERS-CLEANUP-001` | draft |
| `$kbSlug/connectors.tsx` | `SPEC-PORTAL-CONNECTORS-TAB-CLEANUP-001` | draft (borderline) |
| `knowledge/new.tsx` | `SPEC-PORTAL-KB-NEW-CLEANUP-001` | draft |
| `admin/settings.tsx` | `SPEC-PORTAL-ADMIN-SETTINGS-CLEANUP-001` | implemented |
| `admin/billing.lazy.tsx` | `SPEC-PORTAL-BILLING-CLEANUP-001` | draft (coordinate with PRICING-PER-USER-001) |
| `app/transcribe/add.tsx` | `SPEC-PORTAL-TRANSCRIBE-ADD-CLEANUP-001` | draft |
| `setup/mfa.lazy.tsx` | `SPEC-PORTAL-MFA-SETUP-CLEANUP-001` | draft |
| `$kbSlug/members.tsx` | `SPEC-PORTAL-KB-MEMBERS-CLEANUP-001` | draft |

Each carved-out SPEC includes:
- Source SPEC's metrics (lines, useState, churn) verbatim
- Reference to the file-organization rule + ESLint guard already in place
- Reference to the proven KBOverviewSections / TaxonomyTab extraction
  pattern as precedent
- Explicit "scale-the-answer" caveat: don't bundle multiple cleanups
  in one SPEC, even if they look similar
- Required learnings section pointing to this SPEC for context

### Architectural smells (closed in followups commit)

- **F-S1**: `insights.tsx` cross-route imports — PARTIALLY CLOSED
  (`KBOverviewSections` extracted; `TaxonomyTab` left with explicit
  marker, will close in `SPEC-PORTAL-TAXONOMY-EXTRACT-001`).
- **F-S3**: ESLint rule for cross-route imports — DONE (PR #620 +
  cross-reference doc PR #622).

### Convention adoption

- **F-C2**: Hoist legacy cross-directory imports — DONE in PR #620
  for the wizard-only subset. Genuinely-shared symbols (CookieRow,
  ConnectorSummary) correctly stayed in `-kb-types.ts`.
- **F-C1**: Audit all 5 `-`-prefixed files — still open.

## Final state

- 12 source/test files modified
- 4 new files created (`-connector-types.ts`, `-connector-constants.ts`,
  `-connector-feedback.tsx`, `_components/KBOverviewSections.tsx`)
- 2 new docs files (`portal-frontend.md` rule section,
  `process-rules.md` retro entry)
- 1 new ESLint rule + 1 unit test file (13 cases)
- 1 file renamed (`kb-helpers.test.ts` → `connector-helpers.test.ts`)
- 0 SPEC requirements unmet
- 0 outstanding lint errors
- 0 console errors in live Voys verification
