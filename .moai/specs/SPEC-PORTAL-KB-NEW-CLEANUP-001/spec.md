---
id: SPEC-PORTAL-KB-NEW-CLEANUP-001
version: 0.2.0
status: done
created: 2026-05-13
completed: 2026-05-13
author: Mark Vletter
priority: medium
parent: SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001 (god-component § Follow-ups, carved out)
related:
  - SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001 (the connector wizards in $kbSlug_.add-connector.tsx are sibling multi-step wizards — share patterns)
rule:
  - .claude/rules/klai/projects/portal-frontend.md § "File organization for shared types and helpers"
---

# SPEC-PORTAL-KB-NEW-CLEANUP-001 — Split knowledge/new.tsx (713 lines, multi-step KB-creation wizard)

## Goal

Reduce `klai-portal/frontend/src/routes/app/knowledge/new.tsx` from
713 lines to a route shell + per-step sub-components + shared types.
Multi-step KB-creation wizard, similar pattern to the connector
wizards.

Implemented on 2026-05-13. `new.tsx` is now 176 lines and keeps route
orchestration only.

## Motivation metrics

| Metric | Value |
|---|---|
| File line count | 713 |
| useState | 4 |
| useEffect | 0 |
| Inline mutations | 4 |
| Inline queries | (TBD) |
| Git churn last 90 days | 28 commits |
| Last touched | 2 days ago at SPEC creation |
| Production-critical | Yes (KB creation flow — every new KB) |
| Existing colocation | `new._types.ts` already exists |

The relatively low useState count (4) suggests state is already
contained per step. The 713 lines = mostly JSX of multi-step wizard
pages. Likely the right extraction is per-step components (`<DetailsStep>`,
`<ConnectorChoiceStep>`, `<ConfirmStep>`, etc.) similar to how the
connector wizards are now organized.

## Scope (proposed — annotation cycle confirms)

### In

- New `new._components/Step1Details.tsx`, `Step2Members.tsx`, etc.
  — per-step components (exact step naming TBD)
- Existing `new._types.ts` likely needs additions for newly-extracted
  step state shapes
- New `new._wizard-hooks.ts`: 4 mutation hooks (create-KB,
  invite-members, etc.)
- Modify `new.tsx`: route shell + step orchestration only

### Out

- Backend KB-creation API changes
- Adding new wizard steps
- Permission / role-related changes

## Implementation Summary

- Extracted step JSX into route-local components under
  `new._components/`:
  - `-StepName.tsx`
  - `-StepAccess.tsx`
  - `-StepPermissions.tsx`
  - `-StepConfirm.tsx`
- Extracted member queries and the create-KB mutation into
  `new._wizard-hooks.ts`.
- Kept shared route-local state and API shapes in `new._types.ts`.
- Added characterization coverage for create-KB payload mapping in
  `__tests__/-new-wizard-hooks.test.ts`.

## Validation

- `npm run lint -- src/routes/app/knowledge/new.tsx src/routes/app/knowledge/new._wizard-hooks.ts src/routes/app/knowledge/new._types.ts src/routes/app/knowledge/new._components/-StepName.tsx src/routes/app/knowledge/new._components/-StepAccess.tsx src/routes/app/knowledge/new._components/-StepPermissions.tsx src/routes/app/knowledge/new._components/-StepConfirm.tsx src/routes/app/knowledge/__tests__/-new-wizard-hooks.test.ts`
- `npm test -- src/routes/app/knowledge/__tests__/-new-wizard-hooks.test.ts`
- `npx tsc -b --pretty false --force`
- `npm run build`
- Playwright MCP e2e on Voys tenant:
  - created `e2e-kb-new-cleanup-20260513-092032` through
    `/app/knowledge/new`
  - verified redirect to
    `/app/knowledge/e2e-kb-new-cleanup-20260513-092032/sources`
  - deleted the test KB via `/api/app/knowledge-bases/{slug}` and
    verified it no longer appears in the KB list API/page

## Approach

DDD methodology. The wizard pattern is similar to add-connector
(extracted in SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001). Re-use those
patterns:
1. Worktree + characterization tests for full create-KB happy path
2. Extract per-step components into `new._components/`
3. Extract mutation hooks into `new._wizard-hooks.ts`
4. Reduce `new.tsx` to ≤ 200 lines (route + step machine + assembly)
5. Verify gates + Playwright on `/app/knowledge/new`

## Special pattern: `_types.ts` already exists

`new._types.ts` is the precedent for `<route>._<feature>` colocation
in this directory. New colocation files should follow the same pattern:
- `new._components/` (directory of step components)
- `new._wizard-hooks.ts` (alongside `new._types.ts`)

This is the `<route>._<feature>` pattern from the file-organization
rule's decision tree (TanStack ignores `._` infix segments).

## Learnings to apply

- File-organization rule's clause for `<route>._<feature>` colocation
  applies here directly.
- ESLint rule `klai/no-cross-route-import` already prevents
  regressions.
- Connector wizards (SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001) used
  Notion union pattern (Option A — names retained, no mode tag) for
  subtly-divergent shapes. If KB-creation has similar divergent
  shapes (e.g. "personal KB" vs "org KB"), use the same pattern.
- DDD methodology with characterization tests covering full
  happy-path flow.
- scale-the-answer: own SPEC, no bundling.

## See Also

- `.moai/specs/SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001/spec.md` —
  reference for multi-step wizard extraction pattern.
- `klai-portal/frontend/src/routes/app/knowledge/new._types.ts` —
  existing colocation precedent.
- `.claude/rules/klai/projects/portal-frontend.md` § "File
  organization for shared types and helpers"
