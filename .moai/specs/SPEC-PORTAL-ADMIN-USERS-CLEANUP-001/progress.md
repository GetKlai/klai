## SPEC-PORTAL-ADMIN-USERS-CLEANUP-001 Progress

- Started: 2026-05-13
- Phase 1 complete: reviewed earlier 2026-05-13 portal cleanup specs and existing route split patterns.
- Phase 2 complete: added characterization coverage for admin users list filtering and pending-invite delete behavior.
- Phase 3 complete: split `admin/users/index.tsx` into route orchestration, colocated hooks/types/helpers, and route-owned components.
- Verification complete:
  - `npm run i18n:compile`
  - `npx tsc -b --force`
  - `npm run lint`
  - `npm test -- --run src/routes/admin/users`
  - `npm test`
