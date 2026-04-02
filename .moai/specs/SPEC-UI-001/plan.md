# SPEC-UI-001: Implementation Plan

## Task Decomposition

### Phase 1: Data Layer (high impact, low risk)

| Task | Files | Effort |
|------|-------|--------|
| R2: Global staleTime | `main.tsx` | 5 min |
| R3: Create apiFetch helper | New `lib/apiFetch.ts`, update 37 files | 2 hours |
| R1: Remove token from queryKeys | All files using useQuery | 1 hour (after R3) |
| R10: Remove dead deps | `package.json` | 10 min |

### Phase 2: Performance

| Task | Files | Effort |
|------|-------|--------|
| R4: Lazy load routes | 4 route files + router config | 1 hour |

### Phase 3: Component Quality

| Task | Files | Effort |
|------|-------|--------|
| R5: Split god components | 3 route files → 8+ component files | 3 hours |
| R6: QueryErrorState component | New component + 55 route files | 2 hours |

### Phase 4: i18n and Auth

| Task | Files | Effort |
|------|-------|--------|
| R7: Paraglide strings | Message files + ~10 component files | 1.5 hours |
| R8: useCurrentUser hook | New hook + Sidebar, ProductGuard, route layouts | 1 hour |

### Phase 5: Accessibility

| Task | Files | Effort |
|------|-------|--------|
| R9: a11y fixes | Sidebar, wikilink modal, billing toggle | 1 hour |

## Dependencies

- R3 (apiFetch) must be done before R1 (token removal) — the helper centralizes token handling
- R5 (component splitting) can be done independently of everything else
- R8 (useCurrentUser) depends on R2 (staleTime) being set

## Risk Analysis

- **R1 + R3 (token removal + apiFetch):** Largest blast radius — touches 37+ files. Must test thoroughly with token refresh and navigation scenarios.
- **R4 (lazy loading):** TanStack Router lazy() has specific requirements for route exports. Test that all lazy routes load correctly.
- **R5 (god components):** Extracting components may break local state. Test each split route end-to-end.
