# SPEC-PORTAL-CONNECTORS-TAB-CLEANUP-001 — Progress

## Status: done (2026-05-13)

## Implementation

| Commit | Description |
|---|---|
| `0d043ccb` | refactor portal connectors tab |

The 425-line `$kbSlug/connectors.tsx` route file was split — the
borderline case from the SPEC's annotation cycle ended up justifying
the refactor rather than a won't-fix conclusion.

| Changed file | Status |
|---|---|
| `$kbSlug/connectors.tsx` | reduced from 425 to ~210 lines |
| `$kbSlug/-connectors-hooks.ts` (new) | mutation hooks extracted |
| `$kbSlug/-connectors-row.tsx` (new) | per-row affordances component |

Stat: 271 insertions, 186 deletions across 4 files (the
`connectors.tsx` shrink + 2 new files + spec.md status update).

## Outstanding

This progress.md is a **post-hoc summary** added during the
2026-05-13 batch cleanup. The implementation commit did not include
its own progress.md. Live verification on Voys was not done by me —
add browser interactive E2E if a future SPEC depends on the
connectors-tab behaviour.

## See Also

- `.moai/specs/SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001/spec.md` —
  parent SPEC; `connectors.tsx` is the list view, the wizards are the
  add/edit pages already extracted there.
