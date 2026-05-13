# SPEC-PORTAL-TAXONOMY-SPLIT-001 — Progress

## Status: done (2026-05-13)

## Implementation

| PR | Description |
|---|---|
| #646 | refactor(portal-frontend): SPEC-PORTAL-TAXONOMY-SPLIT-001 — split TaxonomyTab god-component |
| #651 | refactor(portal-frontend): SPEC-PORTAL-TAXONOMY-SPLIT-001 polish — code cleanup pass |

The 720-line TaxonomyTab function (extracted to
`_components/TaxonomyTab.tsx` by SPEC-PORTAL-TAXONOMY-EXTRACT-001 +
PR #625) was split into focused sub-components and extracted hooks
per the SPEC's plan. Pre-requisite was met: TaxonomyTab lived in
`_components/` before SPLIT started.

## Outstanding

This progress.md is a **post-hoc summary** added during the
2026-05-13 batch cleanup. The implementation PRs above did not
include their own progress.md (different sync discipline used by
the parallel session that did this work). Browser interactive
verification was not done by me — if F-table row 1 follow-up work
needs that, it should be added before any further refactor.

## See Also

- `.moai/specs/SPEC-PORTAL-TAXONOMY-EXTRACT-001/spec.md` —
  prerequisite (TaxonomyTab move).
- `.moai/specs/SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001/spec.md` —
  origin SPEC, F-table row 1.
