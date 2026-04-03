# SPEC-KB-018 Progress

## Status: COMPLETE

## Implementation Summary

**Knowledge Base Sharing & Access Wizard** — replaces the simple visibility dropdown in KB creation with a rich sharing wizard, and adds default org role management to the members tab.

## Completed Tasks

| # | Task | Status |
|---|---|---|
| 1 | Alembic migration: add `default_org_role` column | Done |
| 2 | Backend model: add `default_org_role` to `PortalKnowledgeBase` | Done |
| 3 | Backend API: extend create endpoint with `initial_members` + `default_org_role` | Done |
| 4 | Backend API: add PUT `/default-org-role` endpoint (owner-only) | Done |
| 5 | Frontend: visibility card selector (Public/Organization/Restricted) | Done |
| 6 | Frontend: contributor checkbox for Public/Organization modes | Done |
| 7 | Frontend: member picker with group/user autocomplete (Restricted mode) | Done |
| 8 | Frontend: summary card showing sharing configuration | Done |
| 9 | Frontend: members tab default org role management | Done |
| 10 | i18n: 32 `knowledge_sharing_*` keys in EN + NL | Done |

## Files Changed

### Backend
- `klai-portal/backend/alembic/versions/a1b2c3d4e5f6_add_default_org_role.py` (new)
- `klai-portal/backend/app/models/knowledge_bases.py` (modified)
- `klai-portal/backend/app/api/app_knowledge_bases.py` (modified)
- `klai-portal/backend/app/services/access.py` (modified)

### Frontend
- `klai-portal/frontend/src/routes/app/knowledge/new.tsx` (rewritten)
- `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/members.tsx` (modified)
- `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/-kb-types.ts` (modified)

### i18n
- `klai-portal/frontend/messages/en.json` (modified)
- `klai-portal/frontend/messages/nl.json` (modified)

## Quality Checks

- ruff check: All checks passed
- ESLint: Clean
- TypeScript tsc --noEmit: No errors

## Known Limitations

- The member picker in restricted mode uses admin-only endpoints (`/api/admin/groups`, `/api/admin/users`). Non-admin users creating restricted KBs won't see the group/user autocomplete. A future app-level endpoint may be needed.

## Date: 2026-04-03
