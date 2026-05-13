---
id: SPEC-PORTAL-ADMIN-USERS-CLEANUP-001
version: 0.1.0
status: draft
created: 2026-05-13
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

This SPEC is **draft**. Annotation cycle required to identify exact
sub-component boundaries.

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

- New `admin/users/_components/UserRow.tsx`: per-row affordances
  (edit, delete, role-change) currently inlined in the table render
- New `admin/users/_components/InviteUserForm.tsx`: invite form (if
  inlined; verify)
- New `admin/users/-users-hooks.ts`: 6 mutation hooks + relevant
  query hooks
- Modify `index.tsx`: route shell + table orchestration only

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
