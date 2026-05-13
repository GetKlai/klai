---
id: SPEC-PORTAL-KB-MEMBERS-CLEANUP-001
version: 0.1.0
status: draft
created: 2026-05-13
author: Mark Vletter
priority: medium
parent: SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001 (god-component § Follow-ups, carved out)
related: []
rule:
  - .claude/rules/klai/projects/portal-frontend.md § "File organization for shared types and helpers"
---

# SPEC-PORTAL-KB-MEMBERS-CLEANUP-001 — Split $kbSlug/members.tsx (497 lines, 10 inline mutations — densest per line)

## Goal

Reduce `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/members.tsx`
from 497 lines to a route shell + per-row component + extracted
mutation hooks. **Densest mutation-per-line ratio of any candidate**
(10 mutations in 497 lines = 1 mutation per ~50 lines).

This SPEC is **draft**. Low churn (15 commits / 90 days) means it's
not actively painful — but the structural density makes any future
edit risky.

## Motivation metrics

| Metric | Value |
|---|---|
| File line count | 497 |
| useState | 7 |
| useEffect | 0 |
| Inline mutations | **10 (densest mutations-per-line)** |
| Inline queries | (TBD) |
| Git churn last 90 days | 15 commits (low) |
| Last touched | 11 hours ago at SPEC creation |
| Production-critical | Yes (KB membership management — RBAC) |

10 mutations almost certainly cover: invite-user, invite-group,
revoke-user, revoke-group, change-role-user, change-role-group,
accept-invite, decline-invite, leave-kb, transfer-ownership (or
similar 10-action set). Each becomes its own hook.

## Scope (proposed)

### In

- New `_components/MemberRow.tsx`: per-row affordances (role select,
  remove, transfer)
- New `_components/InviteSection.tsx`: invite form/banner
- New `-members-hooks.ts`: 10 mutation hooks (one per action)
- Modify `members.tsx`: route shell + table + invite-section
  composition

### Out

- Backend RBAC / membership API changes
- Adding new member actions (e.g. bulk-invite, group-merge)
- Permission model changes

## Approach

DDD methodology. The 10 mutations form a clear extraction target.
ANALYZE: confirm each mutation's distinct concern (no duplicates
disguised as different names).

PRESERVE: characterization tests for each role transition + invite
flow. RBAC is sensitive — wrong role = wrong permissions in
production.

## Learnings to apply

- File-organization rule + ESLint rule
- DDD characterization tests cover each role-transition path
  (RBAC = test coverage matters)
- `_components/` and `-`-prefixed siblings already established in
  `$kbSlug/` directory (existing precedent)
- Triplicate check vs `-kb-helpers.tsx` (`SyncStatusBadge` etc. live
  there — any new shared symbols may belong there too)
- Live verification on Voys with a real KB
- scale-the-answer: own SPEC

## See Also

- `.moai/specs/SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001/spec.md`
- `.moai/specs/SPEC-PORTAL-KB-OWNERSHIP-001` (recently merged —
  related membership work, verify no overlap)
- `.claude/rules/klai/projects/portal-frontend.md` § "File
  organization for shared types and helpers"
