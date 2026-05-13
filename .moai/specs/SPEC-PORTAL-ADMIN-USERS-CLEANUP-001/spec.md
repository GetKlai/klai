---
id: SPEC-PORTAL-ADMIN-USERS-CLEANUP-001
version: 0.1.1
status: done
created: 2026-05-13
completed: 2026-05-13
author: Mark Vletter
priority: medium-high
parent: SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001 (god-component § Follow-ups, carved out)
related:
  - SPEC-PORTAL-TAXONOMY-EXTRACT-001 (sibling cleanup pattern reference)
rule:
  - .claude/rules/klai/projects/portal-frontend.md § "File organization for shared types and helpers"
---

# SPEC-PORTAL-ADMIN-USERS-CLEANUP-001 — Split admin/users/index.tsx (517 lines, 6 inline mutations)

## Goal

Reduce `klai-portal/frontend/src/routes/admin/users/index.tsx` from 517
lines to a route-shell + sub-components + extracted mutation hooks.
Pattern: same as TaxonomyTab (SPEC-PORTAL-TAXONOMY-EXTRACT-001 +
SPEC-PORTAL-TAXONOMY-SPLIT-001).

This SPEC is complete. The implementation followed the existing portal
cleanup pattern: colocated `-users-*` route helpers/hooks/types plus
route-owned `_components/`, with no backend behavior changes.

## Motivation metrics

| Metric | Value |
|---|---|
| File line count | 517 |
| useState | 5 |
| useEffect | 0 |
| Inline mutations | 6 |
| Inline queries | (TBD — verify in ANALYZE phase) |
| Git churn last 90 days | 33 commits (#2 across all candidates) |
| Last touched | 3 hours ago at SPEC creation |
| Production-critical | Yes (admin user management) |

The high churn signals active developer pain. The 6 inline mutations
suggest natural extraction targets (`useInviteUser`, `useUpdateRole`,
`useDeleteUser`, etc.).

## Scope (proposed — needs annotation)

### In

- New `admin/users/_components/UserActions.tsx`: per-row affordances
  (edit, delete, role-change, suspend/reactivate, offboard, leave
  workspace) extracted from the inline table render.
- New `admin/users/_components/UserBadges.tsx`: profile, account-type,
  and status badges extracted from the route file.
- New `admin/users/_components/UsersTable.tsx`: TanStack table render
  extracted from the route file.
- New `admin/users/-users-hooks.ts`: admin-users query plus resend
  invite, delete user, change profile, and leave-workspace mutations.
- New `admin/users/-users-types.ts` and `-users-helpers.ts`: route-owned
  user types, labels, date formatting, display-name, count-label, and
  search filtering helpers.
- Modified `index.tsx`: reduced to route orchestration, search state,
  column definitions, and dialog orchestration.

### Out

- Backend changes (admin user API stays as-is)
- Adding new functionality
- shadcn/ui table component refactor (separate concern)

## Approach

DDD methodology. Reference implementation:
SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001 followups. Phase order:
1. Worktree + characterization tests for existing user-management flows
2. Extract mutation hooks → `-users-hooks.ts`
3. Extract `<UserRow>` (likely the densest sub-component)
4. Extract `<InviteUserForm>` (if applicable)
5. Reduce `index.tsx` to route + table assembly
6. Verify gates + Playwright on `/admin/users`

## Learnings to apply (from SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001)

- **File-organization rule** + **klai/no-cross-route-import** ESLint
  rule are already in place — re-use, don't reinvent.
- **`_components/` for sub-components, `-`-prefixed for hooks/types/constants**.
- **Triplicate elimination check**: grep for any `User*` types or
  user-mutation hooks already exported elsewhere (`@/lib/`, other
  admin routes) before creating new ones.
- **DDD methodology**: characterization tests first, refactor in
  small commits.
- **Live verification** on production after deploy (any Klai admin
  tenant works).
- **scale-the-answer**: do NOT bundle with other god-component
  cleanups. This SPEC is scoped to one file.

## See Also

- `.moai/specs/SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001/spec.md`
- `.moai/specs/SPEC-PORTAL-TAXONOMY-EXTRACT-001/spec.md` — sibling
  cleanup pattern.
- `.claude/rules/klai/projects/portal-frontend.md` § "File
  organization for shared types and helpers"
