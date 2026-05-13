---
id: SPEC-PORTAL-ADMIN-SETTINGS-CLEANUP-001
version: 0.2.0
status: done
completed: 2026-05-13
created: 2026-05-13
author: Mark Vletter
priority: medium
parent: SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001 (god-component § Follow-ups, carved out)
related: []
rule:
  - .claude/rules/klai/projects/portal-frontend.md § "File organization for shared types and helpers"
---

# SPEC-PORTAL-ADMIN-SETTINGS-CLEANUP-001 — Split admin/settings.tsx (524 lines, 9 inline mutations)

## Goal

Reduce `klai-portal/frontend/src/routes/admin/settings.tsx` from 524
lines to a route shell + per-section components + extracted mutation
hooks. **Densest mutations of any candidate** (9 inline) — primary
extraction target is the mutation set.

Implemented on 2026-05-13. `settings.tsx` is now a 35-line route shell
that composes section components and owns only the page heading plus the
shared settings query.

## Implementation Summary

- Extracted settings API/query/mutation wiring into
  `klai-portal/frontend/src/routes/admin/-settings-hooks.ts`.
- Extracted per-section UI/state into route-local admin components:
  - `_components/-LanguageSettingsSection.tsx`
  - `_components/-SecuritySettingsSection.tsx`
  - `_components/-OrganizationSettingsSection.tsx`
  - `_components/-TelemetrySettingsSection.tsx`
  - `_components/-ExtensionsSettingsSection.tsx`
- Kept behavior-preserving query keys, endpoint URLs, request bodies,
  save flash timing, cache updates, and extension staged-toggle logic.
- Used `-` file prefixes for new helper files so TanStack Router ignores
  them during route generation.

## Validation

- CodeIndex impact check for `AdminSettingsPage`: low risk, no indexed
  upstream dependents.
- `npm test`: passed, 37 files / 260 tests.
- `npm run lint`: passed.
- `npm run build`: passed. Existing route-generator and bundle-size
  warnings remain; the new helper files are ignored through the `-`
  file prefix.

## Motivation metrics

| Metric | Value |
|---|---|
| File line count | 524 |
| useState | 9 |
| useEffect | 3 |
| Inline mutations | **9 (densest in survey)** |
| Inline queries | (TBD) |
| Git churn last 90 days | 26 commits |
| Last touched | 11 hours ago at SPEC creation |
| Production-critical | Yes (admin settings) |

9 mutations in one file is a strong signal that the page is doing too
much. Settings pages typically have logical sections (general,
billing, integrations, etc.). Extract per-section.

## Scope (proposed)

### In

- Per-section sub-components in `admin/_components/`:
  - `<GeneralSettingsSection>`, `<IntegrationsSection>`, etc.
  - Each owns its own state + consumes its own mutation hooks
- New `admin/-settings-hooks.ts`: 9 mutation hooks
- Modify `settings.tsx`: route shell + section composition only

### Out

- Backend admin API changes
- Adding new settings categories

## Approach

DDD methodology. The 9 mutations almost certainly cluster by
logical concern (e.g. 3 mutations for general, 3 for integrations, 3
for billing). ANALYZE phase identifies the clusters → each cluster
becomes its own sub-component with its own mutation hooks.

## Learnings to apply

- File-organization rule + ESLint rule already in place
- DDD characterization tests cover each settings-section's mutation
  paths
- Triplicate check vs `admin/_components/` (existing patterns for
  shared admin UI) and `@/lib/` (admin-wide hooks)
- Live verification per section after deploy
- scale-the-answer: own SPEC

## See Also

- `.moai/specs/SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001/spec.md`
- `.claude/rules/klai/projects/portal-frontend.md` § "File
  organization for shared types and helpers"
