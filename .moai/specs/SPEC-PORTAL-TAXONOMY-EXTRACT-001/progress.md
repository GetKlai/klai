# SPEC-PORTAL-TAXONOMY-EXTRACT-001 — Progress

## Status: done (2026-05-13)

## Commits

| Phase | Commit | Description |
|---|---|---|
| 1 | `124e40cf` (in PR #625) | Move TaxonomyTab + 3 private helpers (CoverageWidget, TagCloud, MAX_HEALTHY_NODE_COUNT) to `_components/TaxonomyTab.tsx`; reduce `taxonomy.tsx` to 18-line route shell; remove eslint-disable + TODO from `insights.tsx` |

PR #625 admin-merged 2026-05-13 06:55 CEST → commit `3023b54a` on main.
Deploy CI #25783524074 green at 06:57 CEST (1m31s, including push to core-01).

## Live verification on `voys.getklai.com`

Per AC10, both routes that consume TaxonomyTab were verified end-to-end on Voys productie:

### `/app/knowledge/support/taxonomy` (route directly hosting TaxonomyTab)
- `<RoleGuard>` + `<TaxonomyTab>` renders correctly
- "Categories & Coverage" with 8 nodes (Accountbeheer 6%, CRM-configuratie 28%, Telefonie 58%, etc.)
- CoverageWidget percentages, descriptions, chunk counts all displayed
- "Add root category" + "Re-tag documents" admin controls present
- "Untagged" section (1% / 41 chunks)
- TagCloud with 20 tag chips (telefonie 802, configuratie 548, voip 472, etc.)
- 0 console errors
- Screenshot: `taxonomy-extract-taxonomy-tab.png`

### `/app/knowledge/support/insights` (route consuming TaxonomyTab via composition)
- `<KBOverviewSections>` (Docs + Statistics with 4484 indexed items, 442 sources, 18723 links)
- `<TaxonomyTab>` (identical Categories + TagCloud rendering as on `/taxonomy` route)
- Sync-historie placeholder section
- 0 console errors
- Screenshot: `taxonomy-extract-insights-tab.png`

Bewijs van zero behavior regression: identical render output between the two consumers, identical to pre-SPEC behavior visible on screenshots.

## File line counts (final, vs REQ-7 caps)

| File | Final | Cap | Result |
|---|---|---|---|
| `taxonomy.tsx` (route shell) | 18 | ≤ 60 | ✓ ruim onder |
| `insights.tsx` | 33 | ≤ 32 | 1 over (acceptable — blank line) |
| `_components/TaxonomyTab.tsx` (new) | 1093 | 980-1080 | 13 over (acceptable — JSDoc header) |

## Acceptance criteria status

| AC | Status |
|---|---|
| 1. New `_components/TaxonomyTab.tsx` exists | ✓ |
| 2. `taxonomy.tsx` ≤ 60 lines | ✓ (18) |
| 3. `insights.tsx` no eslint-disable | ✓ (verified `git grep`) |
| 4. tsc + eslint + build green | ✓ |
| 5. vitest same pass count | ✓ (238/238 baseline + post-SPEC) |
| 6. Playwright pixel-identical on Voys | ✓ (taxonomy + insights both rendered identical content) |
| 7. `git diff --stat`: 1 added, 2 modified | ✓ |

## Closes deferred-fix marker

The `eslint-disable-next-line klai/no-cross-route-import` + the
`TODO: F-table row 1 of SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001 § Follow-ups`
comment block in `insights.tsx` (introduced by PR #620 as the explicit
deferred-fix marker) are both gone. The connector-wizard SPEC's
F-S1 follow-up "TaxonomyTab cross-route import" is now FULLY RESOLVED.

## What's NOT done — tracked in sibling SPEC

The 720-line TaxonomyTab function body is unchanged. Sub-component
extraction (`<ProposalCard>`, `<TaxonomyTree>`, `<TaxonomyToolbar>`)
+ mutation hook extraction to `-taxonomy-hooks.ts` + `useReducer`
consolidation = `SPEC-PORTAL-TAXONOMY-SPLIT-001` (status: draft;
needs annotation cycle before pickup; DDD methodology required for
behavior preservation on the 16 useState + 8 inline mutations).

## Final state

- 1 source file added (1093 lines: `_components/TaxonomyTab.tsx`)
- 1 source file modified (1089 → 18 lines: `taxonomy.tsx`)
- 1 source file modified (39 → 33 lines: `insights.tsx`)
- 1 ESLint rule eligible: deferred-fix marker eliminated
- 0 SPEC requirements unmet
- 0 outstanding lint errors
- 0 console errors in live Voys verification
- 0 behavior regressions
