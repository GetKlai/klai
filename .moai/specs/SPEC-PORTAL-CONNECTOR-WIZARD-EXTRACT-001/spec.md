---
id: SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001
version: 0.2.1
status: done
completed: 2026-05-13
created: 2026-05-12
author: Mark Vletter
priority: medium
parent: SPEC-PORTAL-SOURCES-RENAME-001 (out-of-scope follow-up — same god-component pattern, separate UX surface)
related:
  - SPEC-PORTAL-SOURCES-RENAME-001 (sibling cleanup of bronnen.tsx)
  - SPEC-REFACTOR-001 (origin of the `-kb-helpers.tsx` / `-kb-types.ts` pattern)
rule:
  - .claude/rules/klai/projects/portal-frontend.md § "File organization for shared types and helpers" (added in this SPEC's preflight commit)
---

# SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001 — Extract shared wizard code per the new file-organization rule

## Goal

Eliminate the byte-identical duplication between
`$kbSlug_.add-connector.tsx` (1415 lines) and
`$kbSlug_.edit-connector.$connectorId.tsx` (1272 lines) by applying the
file-organization rule added to `portal-frontend.md`. Concretely:

1. Stop add-connector from re-declaring symbols that already exist in
   `$kbSlug/-kb-helpers.tsx` and `$kbSlug/-kb-types.ts` (which edit
   already consumes). This is pure duplicate elimination.
2. Extract the new wizard-only types, constants, and feedback components
   to **the parent route directory** (`routes/app/knowledge/`), per the
   rule's smallest-shared-scope clause. Neither `$kbSlug/-kb-types.ts`
   (wrong scope — that's for KB-tab routes) nor `@/lib/` (wrong layer
   — that's for app-wide infra).
3. Remove the cross-route import (`edit-connector` imports from
   `add-connector`) by relocating the two feedback components.

Zero behavior change. The wizard renders identical DOM after the SPEC.

This SPEC explicitly does NOT extract the wizard state hook
(`useConnectorWizardState`), refactor the page god-components, or
relocate the existing tactical-legacy cross-directory imports
(add/edit consuming from `$kbSlug/-kb-*`). Those are tracked in the
follow-up list below.

## Motivation

### 1. Triplicate symbols that the codebase already half-fixed

`-kb-helpers.tsx` and `-kb-types.ts` were created in commit `c1e8ed17`
as part of SPEC-REFACTOR-001 to host shared symbols for the KB-tab
routes. Edit-connector later started importing from them
(SPEC-CONNECTOR-INPUT-VALIDATION-001, commit `8e5fc770`). Add-connector
never adopted those imports — it kept local copies. Today:

| Symbol | `-kb-helpers/types` | edit-connector | add-connector |
|---|---|---|---|
| `ASSERTION_MODE_OPTIONS` | exported | imports it | **declares own copy (line 97-104)** |
| `GitHubConfig` | exported | imports it | **declares own copy (line 55-61)** |
| `WebCrawlerConfig` | exported | imports it | **declares own copy (line 63-68)** |

Add a field to `GitHubConfig` and three places must change in sync. No
TypeScript error fires when one is missed. This is the duplication the
SPEC-REFACTOR-001 author tried to prevent; add-connector silently
broke the property.

### 2. Byte-identical wizard-only duplication add ↔ edit

| Symbol | Add (line) | Edit (line) | Diff |
|---|---|---|---|
| `AuthProbeResult` (interface, 5 fields) | 40-45 | 92-97 | identical |
| `AuthGuardSuggestion` (interface, 4 fields) | 70-75 | 78-83 | identical |
| `AuthProbeClassification` (type, 5 variants) | 33-38 | own copy | identical |
| `PreviewClassification` (type, 6 variants) | 47-53 | own copy | identical |
| `WcStep` (type, 5 variants) | 31 | own copy | identical |
| `MARKDOWN_PROSE_CLASSES` (~500-char Tailwind blob) | 124 | 110 | identical |
| `AirtableConfig` ↔ `AirtableEditConfig` (4 fields) | 83-88 | 56-61 | **identical bodies, different names** |
| `ConfluenceConfig` ↔ `ConfluenceEditConfig` (4 fields) | 90-95 | 63-68 | **identical bodies, different names** |
| `NotionConfig` ↔ `NotionEditConfig` (3 fields) | 77-81 | 50-54 | **subtly different — intentional, but invisible to either reader** |

These are NOT in `-kb-*` because they're wizard-specific (auth-probe,
preview-classification, web-crawler step machine, Notion OAuth shape).
The KB-tab routes don't use them. Per the new rule's clause 3
(smallest-shared scope across sibling directories), they belong at the
**parent route directory**:
`klai-portal/frontend/src/routes/app/knowledge/-connector-*.{ts,tsx}`.
Not inside `$kbSlug/`, where the scope is wrong.

### 3. Cross-route import smell

```ts
// $kbSlug_.edit-connector.$connectorId.tsx:21
import {
  AuthProbeFeedback,
  PreviewClassificationFeedback,
} from './$kbSlug_.add-connector'
```

Plus two test files (`__tests__/wizard-feedback.test.tsx`,
`__tests__/edit-wizard-step-deep-link.test.tsx`) that also import
these two components from the route file. The new rule forbids this
exactly: "Cross-route imports. Tests importing from a route file have
the same smell. Extract `X` to a `-`-prefixed sibling file at the
smallest-shared scope."

## Scope

### In

**Preflight** (one commit, lands BEFORE the rest of the SPEC):

- Add the "File organization for shared types and helpers" section to
  `.claude/rules/klai/projects/portal-frontend.md`. (Already done in
  this SPEC's working set; a clean commit pulls it onto main.)

**Frontend** (`klai-portal/frontend/src/routes/app/knowledge/`):

- New file `-connector-types.ts` (parent route directory) containing
  wizard-specific types:
  `AuthProbeClassification`, `AuthProbeResult`, `AuthGuardSuggestion`,
  `PreviewClassification`, `PreviewResult`, `WcStep`, `ConnectorType`,
  `AirtableConfig` (single name, drop `AirtableEditConfig`),
  `ConfluenceConfig` (single name, drop `ConfluenceEditConfig`),
  `NotionAddConfig` + `NotionEditConfig` + `NotionConfig` discriminated
  union (Option A — names retained, no `mode` tag), `StepDeepLink`.

- New file `-connector-constants.ts` (parent route directory) with
  wizard-specific constants:
  `MARKDOWN_PROSE_CLASSES`, `VALID_PRESELECT_TYPES`, `VALID_STEPS`.

- New file `-connector-feedback.tsx` (parent route directory) with
  wizard-specific render helpers:
  `AuthProbeFeedback`, `PreviewClassificationFeedback`.

- Modify `$kbSlug_.add-connector.tsx`:
  - Remove the 9 wizard-specific type/interface declarations (now in
    `./-connector-types`).
  - Remove the 3 wizard-specific constants (now in
    `./-connector-constants`).
  - Remove the 2 feedback component definitions (now in
    `./-connector-feedback`).
  - Remove the 3 **duplicate** declarations that already exist in
    `$kbSlug/-kb-*`: `ASSERTION_MODE_OPTIONS`, `GitHubConfig`,
    `WebCrawlerConfig`. Import them from
    `./$kbSlug/-kb-helpers` and `./$kbSlug/-kb-types` (matching what
    edit-connector already does).
  - Add the new sibling-file imports.

- Modify `$kbSlug_.edit-connector.$connectorId.tsx`:
  - Remove the 9 wizard-specific type/interface declarations (now in
    `./-connector-types`). Rename `AirtableEditConfig` →
    `AirtableConfig`, `ConfluenceEditConfig` → `ConfluenceConfig` at
    every usage site.
  - Remove the 2 wizard-specific constants (now in
    `./-connector-constants`).
  - Replace `import ... from './$kbSlug_.add-connector'` with
    `import ... from './-connector-feedback'`.
  - Update Notion config usage to consume the discriminated union
    shape (`NotionAddConfig` / `NotionEditConfig` retained; field
    semantics unchanged).

- Modify `__tests__/wizard-feedback.test.tsx`:
  - Change import from `'../$kbSlug_.add-connector'` to
    `'../-connector-feedback'`. No assertion changes.

- Modify `__tests__/edit-wizard-step-deep-link.test.tsx`:
  - Change import of `PreviewClassificationFeedback` from
    `'../$kbSlug_.add-connector'` to `'../-connector-feedback'`.

### Out (explicit)

- **`useConnectorWizardState` hook extraction** — 22 shared `useState`
  declarations + `authProbeMutation` + `previewMutation`. Real win,
  real risk (Notion OAuth + 81-line prefill `useEffect` in edit).
  Tracked under § Follow-ups.
- **Splitting `AddConnectorPage` / `EditConnectorPage` into per-step
  sub-components**. Tracked under § Follow-ups.
- **Relocating tactical-legacy cross-directory imports** from
  `$kbSlug/-kb-helpers.tsx` / `-kb-types.ts` upward to the parent
  route directory. The new rule says new code must follow
  smallest-shared scope; existing legacy is grandfathered. A future
  SPEC can hoist them if scope-mismatch becomes painful.
- **TaxonomyTab refactor**, the `insights.tsx` cross-route import,
  and the other 6 god-components below 600 lines. All tracked under
  § Follow-ups.
- **`useCallback` / `useMemo` for re-render hygiene** — folded into
  the future hook-extraction SPEC.
- **ESLint rule preventing future cross-route imports** — nice to
  have. Tracked under § Follow-ups.

### Backend changes summary

None. Frontend-only. No alembic migration, no API change, no env var.

## Requirements (EARS)

### Functional

- **REQ-1**: When a contributor opens `$kbSlug_.add-connector.tsx`
  after this SPEC, the file shall NOT contain inline declarations of
  `AuthProbeResult`, `AuthGuardSuggestion`, `AuthProbeClassification`,
  `PreviewClassification`, `PreviewResult`, `WcStep`,
  `AirtableConfig`/`AirtableEditConfig`,
  `ConfluenceConfig`/`ConfluenceEditConfig`, `NotionConfig`/`NotionEditConfig`,
  `MARKDOWN_PROSE_CLASSES`, `VALID_PRESELECT_TYPES`,
  `ASSERTION_MODE_OPTIONS`, `GitHubConfig`, `WebCrawlerConfig`. Each
  shall be imported from either `./-connector-types`,
  `./-connector-constants`, `./$kbSlug/-kb-helpers`, or
  `./$kbSlug/-kb-types`.

- **REQ-2**: When a contributor opens
  `$kbSlug_.edit-connector.$connectorId.tsx` after this SPEC, the file
  shall NOT contain inline declarations of any symbol listed in REQ-1
  NOR declarations of `AirtableEditConfig`, `ConfluenceEditConfig`
  (those names cease to exist; the shape is named `AirtableConfig` /
  `ConfluenceConfig` going forward), and shall NOT contain
  `import ... from './$kbSlug_.add-connector'`.

- **REQ-3**: When `AddConnectorPage` or `EditConnectorPage` renders
  the auth-probe feedback panel, the system shall render
  `<AuthProbeFeedback>` imported from `./-connector-feedback`. DOM
  output shall be byte-identical to the current behavior (verified by
  the existing snapshot text-match tests in `wizard-feedback.test.tsx`).

- **REQ-4**: When `AddConnectorPage` or `EditConnectorPage` renders
  the preview-classification feedback panel, the system shall render
  `<PreviewClassificationFeedback>` imported from
  `./-connector-feedback`. DOM output shall be byte-identical.

- **REQ-5**: When the Notion-add flow stores OAuth credentials, the
  system shall use the `NotionAddConfig` shape. When the Notion-edit
  flow stores updated credentials, the system shall use the
  `NotionEditConfig` shape. Field semantics: `access_token` (required,
  add) vs `new_access_token` (optional, edit) — current behavior
  preserved. The `NotionConfig` discriminated union exposes both
  shapes for shared consumers.

- **REQ-6**: When the deep-link search-param parser in
  `EditConnectorPage` validates a `step` value, it shall consult
  `VALID_STEPS` imported from `./-connector-constants`. Behavior
  identical: reject any value not in the set.

- **REQ-7**: When the URL-param parser in `AddConnectorPage`
  validates a `type` value, it shall consult `VALID_PRESELECT_TYPES`
  imported from `./-connector-constants`. Behavior identical.

### Non-functional

- **REQ-8**: After this SPEC, `tsc --noEmit` on `klai-portal/frontend`
  shall pass with zero errors. `bun run lint` (which is `eslint .`)
  shall pass with zero errors.

- **REQ-9**: After this SPEC, `vitest run` (the project test command,
  per `package.json`) on the
  `klai-portal/frontend/src/routes/app/knowledge/__tests__/`
  directory shall pass with the same assertion count as the
  pre-extraction run. Every assertion translated 1:1.

- **REQ-10**: After this SPEC, line counts shall satisfy:
  - `$kbSlug_.add-connector.tsx` ≤ 1280 lines (down from 1415)
  - `$kbSlug_.edit-connector.$connectorId.tsx` ≤ 1240 lines (down from 1272)
  - `-connector-types.ts` ≤ 100 lines
  - `-connector-constants.ts` ≤ 50 lines
  - `-connector-feedback.tsx` ≤ 130 lines

  Upper bounds, not targets. Verify the extraction did not drag
  unrelated logic out by accident.

- **REQ-11**: `git grep -nE 'interface (AuthProbeResult|AuthGuardSuggestion|AirtableConfig|AirtableEditConfig|ConfluenceConfig|ConfluenceEditConfig|NotionConfig|NotionEditConfig)\b' klai-portal/frontend/src` shall return matches ONLY in `-connector-types.ts`.

- **REQ-12**: `git grep -nE 'MARKDOWN_PROSE_CLASSES = ' klai-portal/frontend/src` shall return exactly ONE definition site (in `-connector-constants.ts`).

- **REQ-13**: `git grep -nE 'interface (GitHubConfig|WebCrawlerConfig)\b' klai-portal/frontend/src` shall return matches ONLY in `$kbSlug/-kb-types.ts` (canonical location). Add-connector's local copies are removed.

- **REQ-14**: `git grep -nE 'ASSERTION_MODE_OPTIONS\s*[:=]' klai-portal/frontend/src` shall return exactly ONE definition site (in `$kbSlug/-kb-helpers.tsx`).

## Acceptance Criteria

1. The "File organization for shared types and helpers" section is
   present in `portal-frontend.md` in the same PR (preflight commit).
2. Three new files exist:
   `klai-portal/frontend/src/routes/app/knowledge/-connector-types.ts`,
   `-connector-constants.ts`, `-connector-feedback.tsx`.
3. `git grep -n "interface AirtableEditConfig\|interface ConfluenceEditConfig" klai-portal/frontend/src`
   returns zero hits.
4. `git grep -n "from './\$kbSlug_.add-connector'" klai-portal/frontend/src`
   returns zero hits (the cross-route import is eliminated).
5. `git grep -n "ASSERTION_MODE_OPTIONS\s*[:=]" klai-portal/frontend/src/routes/app/knowledge/\$kbSlug_.add-connector.tsx`
   returns zero hits (local duplicate removed; imported from
   `./$kbSlug/-kb-helpers`).
6. `git grep -n "interface GitHubConfig\|interface WebCrawlerConfig" klai-portal/frontend/src/routes/app/knowledge/\$kbSlug_.add-connector.tsx`
   returns zero hits.
7. `wc -l` line-count caps from REQ-10 satisfied.
8. `tsc --noEmit` reports zero errors. `bun run lint` reports zero errors.
9. The two test files in `__tests__/` pass with imports re-pointed to
   `-connector-feedback`. Same assertion count.
10. Playwright smoke (Playwright MCP) on a real KB:
    - Add web_crawler with auth-required URL → cookie step → auth-probe
      → see `<AuthProbeFeedback>` panel render with the expected
      classification message. Same flow on edit. Screenshots compared
      pixel-identical.
    - Add → fill selector → run preview → see
      `<PreviewClassificationFeedback>` panel render. Same on edit.
      Same comparison.
11. `git diff --stat` shows: 1 rule file modified (preflight), 3 files
    added (`-connector-*`), 4 files modified (2 routes + 2 tests).
    `routeTree.gen.ts` byte-identical (no semantic drift; new files
    are `-`-prefixed and ignored).

## Implementation Plan

The SPEC ships as one PR with ordered commits. Each commit
independently green (`tsc --noEmit` + lint + tests).

**Methodology**: DDD (ANALYZE-PRESERVE-IMPROVE). Pure structural
extraction, but the wizards have live behavior — the existing
`wizard-feedback.test.tsx` tests already cover all 5 AuthProbeFeedback
classifications + all 6 PreviewClassificationFeedback cases (verified
2026-05-12). Use them as the characterization gate.

### Phase 0 — Worktree + verify baseline

- `git worktree add ../klai-connector-extract -b feat/SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001 origin/main`
- Verify `vitest run __tests__/wizard-feedback.test.tsx` and
  `vitest run __tests__/edit-wizard-step-deep-link.test.tsx` are
  green against current code.
- Verify `tsc --noEmit` is green.
- Snapshot baseline: capture line counts of the two route files
  (`wc -l`) for later REQ-10 comparison.
- No new tests in this phase. The existing tests are sufficient
  characterization for the components being moved; the route-file
  changes are pure import-rewrites that `tsc --noEmit` validates.

### Phase 1 — Preflight: rule on portal-frontend.md (single commit)

- Commit the new "File organization for shared types and helpers"
  section to `.claude/rules/klai/projects/portal-frontend.md`.
- This commit is independent of the code changes; it documents the
  decision the rest of the SPEC follows.

### Phase 2 — Eliminate triplicates against `-kb-*` (single commit)

- In `add-connector.tsx`:
  - Remove the local `ASSERTION_MODE_OPTIONS` constant (lines 97-104).
  - Remove the local `GitHubConfig` interface (lines 55-61).
  - Remove the local `WebCrawlerConfig` interface (lines 63-68).
  - Add to existing import block:
    `import { ASSERTION_MODE_OPTIONS, joinSeedUrl } from './$kbSlug/-kb-helpers'`
    (joinSeedUrl already imported per SPEC's grep).
    `import type { GitHubConfig, WebCrawlerConfig } from './$kbSlug/-kb-types'`
    (or merge with existing `import type { CookieRow } from './$kbSlug/-kb-types'`).
- `tsc --noEmit` will surface every missed reference.
- Lint + tests green.

This is the most surgical commit and confirms add-connector is now
aligned with edit-connector on what they import from `-kb-*`.

### Phase 3 — Extract wizard-only constants (single commit)

- Create `klai-portal/frontend/src/routes/app/knowledge/-connector-constants.ts`
  with `MARKDOWN_PROSE_CLASSES`, `VALID_PRESELECT_TYPES`, `VALID_STEPS`.
- `add-connector.tsx`: remove the 3 inline definitions, add import.
- `edit-connector.$connectorId.tsx`: remove `MARKDOWN_PROSE_CLASSES`
  and `VALID_STEPS` inline definitions, add import.
- `tsc --noEmit` + lint + tests green.

### Phase 4 — Extract wizard-only types (single commit)

- Create `klai-portal/frontend/src/routes/app/knowledge/-connector-types.ts`.
- Move: `AuthProbeClassification`, `AuthProbeResult`, `AuthGuardSuggestion`,
  `PreviewClassification`, `PreviewResult`, `WcStep`, `ConnectorType`,
  `AirtableConfig`, `ConfluenceConfig`, `NotionAddConfig`,
  `NotionEditConfig`, `NotionConfig` (discriminated union, Option A),
  `StepDeepLink`.
- Update both route files: remove inline declarations, add imports.
- Rename usage sites in `edit-connector.tsx`:
  `AirtableEditConfig` → `AirtableConfig`,
  `ConfluenceEditConfig` → `ConfluenceConfig`. NotionEditConfig keeps
  its name (it's now exported from `-connector-types.ts` as part of
  the union).
- `tsc --noEmit` is the mechanical guarantee that no rename was missed.
- Lint + tests green.

### Phase 5 — Extract feedback components (single commit)

- Create `klai-portal/frontend/src/routes/app/knowledge/-connector-feedback.tsx`
  with `AuthProbeFeedback` (currently in add lines 1315-1350) and
  `PreviewClassificationFeedback` (lines 1358-1414) lifted verbatim.
- `add-connector.tsx`: remove the two component definitions, add
  `import { AuthProbeFeedback, PreviewClassificationFeedback } from './-connector-feedback'`.
  No re-export shim. The two test files are updated in this same
  commit so no soak-window is needed.
- `edit-connector.$connectorId.tsx`: change the
  `'./$kbSlug_.add-connector'` import to `'./-connector-feedback'`.
- `__tests__/wizard-feedback.test.tsx`: change import path to
  `'../-connector-feedback'`.
- `__tests__/edit-wizard-step-deep-link.test.tsx`: change import path
  to `'../-connector-feedback'`.
- `tsc --noEmit` + lint + characterization tests green. Tests should
  pass without any assertion change — if they do not, you've leaked
  logic during the move; revert and restart.

### Phase 6 — QA + ship

- `bun run lint` zero errors.
- `tsc --noEmit` zero errors.
- `bun run build` green.
- `vitest run` zero failures across the whole frontend test suite.
- Playwright smoke (Playwright MCP):
  - Add web_crawler → auth-required → cookie-paste → auth-probe →
    screenshot the feedback panel.
  - Edit the same connector → same flow → screenshot.
  - Compare screenshots: pixel-identical.
  - Add → fill selector → run preview → screenshot the classification
    feedback. Same on edit. Same comparison.
- Open PR with the checklist below.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Notion config rename misses a usage site | Medium | Medium | `tsc --noEmit` catches structural mismatches. Phase 4 commit message lists every renamed identifier; reviewer greps for each. |
| `routeTree.gen.ts` regenerates with noise (`-`-prefix not honored on a non-default config) | Very low — verified `routeFileIgnorePrefix` defaults to `-` and `-kb-helpers.tsx` is already correctly skipped | Low | Verify `routeTree.gen.ts` byte-identical after the PR. If it changes, the prefix convention is misunderstood; revert and use `_components/` instead. |
| Existing tests catch a rendering difference (broken extraction) | Low — verbatim move | Medium | Phase 5 explicitly halts if tests fail. Revert and re-do with finer-grained diff. |
| Phase 4 Notion union design choice creeps to a `mode`-tagged variant mid-PR | Medium | Medium | Phase 4 commit message MUST start with the literal text "Notion union: Option A — names retained, no mode tag". A `mode`-tagged shape is a different SPEC. |
| Cross-directory imports added in Phase 2 (`./$kbSlug/-kb-*`) ARE the smell the new rule warns about | High — they are tactical legacy | Low | Acknowledged. Adding two more import statements in add-connector matches what edit already does, eliminates duplicates today, and waits for the future scope-hoist SPEC to relocate. The alternative (creating a third copy in a fresh file) is worse. |
| Vitest does not pick up new files due to glob mismatch | Very low | Low | `vitest.config.ts` uses default test glob; new files in `__tests__/` work. Verified Phase 0. |
| Hidden coupling: edit-connector relies on a side effect of importing from add-connector | Very low — verified by reading both files; only two named imports, no side effects | High if true | Phase 5 commit MUST be tested with edit-connector page actually loading in dev. If it crashes on load, the import was load-bearing. |

## PR Description Checklist

- [ ] No backend changes. No alembic migration. No API change. No env var.
- [ ] `portal-frontend.md` § "File organization for shared types and helpers" present (preflight commit).
- [ ] Three new files: `routes/app/knowledge/-connector-types.ts`, `-connector-constants.ts`, `-connector-feedback.tsx`.
- [ ] `git grep -n "interface AirtableEditConfig\|interface ConfluenceEditConfig" klai-portal/frontend/src` zero hits.
- [ ] `git grep -n "from './\$kbSlug_.add-connector'" klai-portal/frontend/src` zero hits.
- [ ] `git grep -n "ASSERTION_MODE_OPTIONS\s*[:=]" klai-portal/frontend/src/routes/app/knowledge/\$kbSlug_.add-connector.tsx` zero hits.
- [ ] `git grep -n "interface GitHubConfig\|interface WebCrawlerConfig" klai-portal/frontend/src/routes/app/knowledge/\$kbSlug_.add-connector.tsx` zero hits.
- [ ] `git grep -nE 'interface (AuthProbeResult|AuthGuardSuggestion|AirtableConfig|ConfluenceConfig)\b' klai-portal/frontend/src` returns matches only in `-connector-types.ts`.
- [ ] `wc -l` caps from REQ-10 satisfied.
- [ ] `tsc --noEmit` green. `bun run lint` green. `bun run build` green.
- [ ] Existing `__tests__/wizard-feedback.test.tsx` and `__tests__/edit-wizard-step-deep-link.test.tsx` pass with re-pointed imports. Same assertion count.
- [ ] `routeTree.gen.ts` byte-identical.
- [ ] Playwright smoke verified: auth-probe panel and preview-classification panel render identically on add and edit. Screenshots attached to PR.
- [ ] Notion union design choice: Option A (no `mode` tag).
- [ ] Edit-connector page loads in dev without errors after Phase 5 (verifies no hidden side-effect coupling on the old import).

## Open Questions

1. **`CONNECTOR_TYPES` location.** Today add-only because only the
   add page renders the type-picker grid. If a future SPEC adds a
   "change connector type" affordance on edit, `CONNECTOR_TYPES`
   becomes shared too. For now: leave it in `add-connector.tsx`. If
   extracted later, it goes into `-connector-type-catalog.tsx`
   (separate file because it carries React Icon imports and is
   `.tsx`, not `.ts`).

2. **`StepDeepLink` location** — currently in edit only, declared
   inline. Moved to `-connector-types.ts` because it expresses a
   wizard-wide URL contract; if it ever grows to a multi-step
   deep-link map shared with add, the move pays off. If reviewer
   prefers, leave it in edit and revisit later — low-risk either way.

## Follow-ups

The repo-wide scan during this SPEC's drafting found a systemic
god-component pattern. These are tracked here so the next planner
has a starting list rather than re-discovering them. Each is a
SEPARATE SPEC, not bundled with this one — that would violate the
new rule's "smallest-shared scope" spirit at the SPEC level too.

### Direct follow-ups to this SPEC (same wizard pages)

- **F-1: `useConnectorWizardState` hook extraction.** The 22 shared
  `useState` declarations + `authProbeMutation` + `previewMutation`
  in both wizard pages. Real win, real risk (Notion OAuth flow + the
  81-line prefill `useEffect` in edit, lines 223-304). Probably
  smallest-shared-scope `-connector-state.ts` next to the new
  `-connector-types.ts`. **Estimated effort**: ~1 day implementation,
  needs careful behavior-preservation tests around Notion OAuth
  redirects.

- **F-2: Page god-component split.** `AddConnectorPage` (lines
  145-1307, 1162 lines body) and `EditConnectorPage` (lines 113-1271,
  1158 lines body). Per-step sub-components in a `_components/`
  directory. Pre-requisite: F-1 lands first so the state hook makes
  sub-components feasible.

### Repo-wide god-component candidates (each its own SPEC)

| File | Regels | useState | useEffect | mutations/queries | Profiel | SPEC priority |
|---|---|---|---|---|---|---|
| `$kbSlug/taxonomy.tsx` | 1088 | 16 | 2 | ~12 | `TaxonomyTab` 720 regels, 8 inline mutations, 166-regel inline `proposals.map()` callback. Cross-imported by `insights.tsx`. | High — actual code-review pain |
| `knowledge/new.tsx` | 713 | 4 | 0 | 4 | Multi-step KB-creation wizard; types already in `new._types.ts`. Modest useState count suggests it's reasonable for its size. | Medium — likely manageable refactor |
| `admin/billing.lazy.tsx` | 673 | 11 | 2 | 0 | 11 useState in one component without mutations/queries is unusual — local form state machine. | Medium |
| `setup/mfa.lazy.tsx` | 668 | **19** | 3 | 0 | 19 useState — highest in the codebase. Multi-step MFA setup. Strong refactor candidate. | High — fragility risk |
| `app/transcribe/add.tsx` | 530 | 12 | 4 | 3 | Transcribe upload form. | Medium |
| `admin/settings.tsx` | 524 | 9 | 3 | 9 | 9 mutations inline → mutation-hooks extraction. | Medium |
| `admin/users/index.tsx` | 517 | 5 | 0 | 6 | 6 mutations inline; row-level affordances likely candidate for `_components/UserRow.tsx`. | Medium |
| `$kbSlug/members.tsx` | 497 | 7 | 0 | 10 | 10 mutations is the densest mutation-per-line in the survey; row-level affordances. | Medium-high — touchy area, well-tested |
| `$kbSlug/connectors.tsx` | 425 | 8 | 2 | 5 | Borderline — review value of refactor before SPEC. | Low |

### Architectural smells (no-SPEC-needed; can be one-PR cleanups)

- **F-S1: `insights.tsx` cross-route imports.** `insights.tsx`
  (33 lines) imports `TaxonomyTab` from `./taxonomy` (1088 lines)
  and `KBOverviewSections` from `./overview`. Both are route files.
  Per the new rule this is a smell. Fix: extract `TaxonomyTab` to
  `$kbSlug/-taxonomy-component.tsx` (or co-located inside taxonomy's
  own `_components/`) and `KBOverviewSections` to a similar place.
  Resolves the smell and makes F-table row 1 (`taxonomy.tsx`) easier.

- **F-S2: Duplicate `interface User`-like types.** Out of scope for
  this audit; flagged for next codebase-wide types audit.

- **F-S3: ESLint `no-restricted-imports` rule** preventing
  `import from '../<route-name>.tsx'` patterns. Once the existing
  cross-route imports are eliminated (F-S1 + this SPEC), add the
  rule to prevent regression.

### Convention adoption follow-ups

- **F-C1: Audit existing `-`-prefixed files** against the new rule.
  Five feature-local instances exist today
  (`-kb-*`, `-bronnen-*`, `admin/api-keys/-*`, `admin/widgets/-*`,
  `app/templates/-template-form.tsx`). Verify each matches the rule's
  decision-tree clauses (smallest-shared scope, naming convention).
  Cleanup-only — no new SPECs spawned unless an instance is
  badly-placed.

- **F-C2: Hoist legacy cross-directory imports.** add/edit-connector
  consume from `$kbSlug/-kb-*` because that's where the symbols
  happened to live. Per the new rule's clause 3, the smallest-shared
  scope across `$kbSlug/` (KB-tabs) AND
  `$kbSlug_.{add,edit}-connector.tsx` (wizards) is the parent
  `routes/app/knowledge/`. A future SPEC could relocate the
  wizard-relevant subset (`ASSERTION_MODE_OPTIONS`, `GitHubConfig`,
  `WebCrawlerConfig`, etc.) to parent-level `-knowledge-*` files.
  Not urgent — the legacy imports are tactical and tests catch
  regressions.

## See Also

- `.moai/specs/SPEC-PORTAL-SOURCES-RENAME-001/spec.md` — sibling
  cleanup of `bronnen.tsx`; explicitly listed this SPEC's target
  files as out-of-scope follow-up.
- `.claude/rules/klai/projects/portal-frontend.md` § "File
  organization for shared types and helpers" — the rule this SPEC
  applies. Added in the preflight commit (Phase 1).
- `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/-kb-helpers.tsx`
  + `-kb-types.ts` — origin of the `-`-prefixed sibling-file pattern
  in this directory (created by SPEC-REFACTOR-001 commit `c1e8ed17`).
- `klai-portal/frontend/src/routes/app/knowledge/$kbSlug_.add-source._components/`
  — existing precedent for the `_components/` directory pattern.
- `.claude/rules/klai/lang/typescript.md` — `tsc --noEmit` after
  refactors, search-broadly-when-changing default rules.
