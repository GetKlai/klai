## SPEC-PORTAL-ADMIN-SETTINGS-CLEANUP-001 Progress

- Started: 2026-05-13T14:14:25+02:00
- Harness level: standard
- Development mode: refactor
- Scope: frontend-only refactor of `klai-portal/frontend/src/routes/admin/settings.tsx`

## Implementation Notes

- Reduced `settings.tsx` from 524 lines in the SPEC baseline to 35 lines.
- Moved the shared settings query, extension query, `/api/me` query, and
  settings mutations into `-settings-hooks.ts`.
- Split the page into five sections:
  - language
  - security and auto-accept
  - organization placeholder
  - telemetry
  - extensions
- Preserved endpoint URLs, payload shapes, query keys, logger calls,
  cache updates, staged extension saving, and saved-state flash timing.
- New section files use `-` prefixes so the TanStack Router generator
  excludes them.

## Verification

- CodeIndex `impact(AdminSettingsPage, upstream)`: LOW, no upstream
  dependents.
- CodeIndex `detect_changes`: LOW, no affected execution processes.
- `npm test`: passed, 37 files / 260 tests.
- `npm run lint`: passed.
- `npm run build`: passed. Existing route-tree warnings remain for
  pre-existing helper/test files outside this change; the new settings
  helper files are ignored.
