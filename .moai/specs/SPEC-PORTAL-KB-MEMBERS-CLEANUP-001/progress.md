## SPEC-PORTAL-KB-MEMBERS-CLEANUP-001 Progress

- Started: 2026-05-13T12:14:49Z
- Harness level: standard
- Development mode: tdd
- Scope: frontend-only refactor of `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/members.tsx`

## Implementation Notes

- Reduced `members.tsx` from 497 lines in the SPEC baseline to 362 lines.
- Extracted mutation contracts into `-members-hooks.ts`.
- Extracted the repeated group/person invite combobox section into `_components/-InviteSection.tsx`.
- Extracted shared group/user member row rendering into `_components/-MemberRow.tsx`.
- Kept the route responsible for auth, route params, data queries, visibility derivation, ownership checks, and confirmation dialog state.
- Preserved existing endpoint URLs, request bodies, query invalidation intent, role defaults, owner-only removal rules, and personal-KB/read-only behavior.

## Verification

- `npm ci`: passed; 0 vulnerabilities.
- `npx vitest run 'src/routes/app/knowledge/\$kbSlug/__tests__/-members-hooks.test.tsx'`: passed, 1 file / 3 tests.
- `npm run lint -- --quiet`: passed.
- `npm run i18n:compile && npx tsc -b --force`: passed.
- `npm run security:audit && npm run lint && npm run build`: passed. Existing TanStack route-tree warnings remain outside this SPEC's new files.
- `cd klai-widget && npm ci && npm run build`: passed. `npm ci` reports 2 existing moderate audit findings in widget dependencies.
- `python3 scripts/fix-mojibake-locales.py --check`: passed.
- Full `npm test` was also attempted after the test file rename; it still fails on existing admin/user locale expectations unrelated to this SPEC (rendered EN labels vs tests expecting NL labels).
- CodeIndex pre-change `impact({target: "members.tsx", direction: "upstream"})`: LOW risk; direct importer is `routeTree.gen.ts`.
- CodeIndex post-change `detect_changes`: reported LOW risk / no affected processes, but its changed-symbol listing appears stale relative to `git status`.

## Not Performed

- Live Voys tenant smoke for KB membership management.
