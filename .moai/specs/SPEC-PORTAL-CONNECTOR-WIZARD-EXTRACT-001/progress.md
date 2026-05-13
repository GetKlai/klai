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

These remain explicitly out of this SPEC's scope:

- **F-1**: `useConnectorWizardState` hook extraction (real win, real risk)
- **F-2**: Page god-component split (depends on F-1)
- **F-table row 1**: `TaxonomyTab` god-component split (1 cross-route
  import marked with `eslint-disable-next-line` references this work)
- **F-table other rows**: 7 more god-component candidates documented
  in spec.md § Follow-ups

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
