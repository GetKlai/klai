# SPEC-KB-018 Progress

## Status: COMPLETE

## Implementation Summary

**Knowledge Base Creation Wizard — Multi-Step** — replaced the single-page creation form with a 4-step wizard (Name → Access → Permissions → Confirm). Includes scope picker, visibility cards, member picker with role management, and skip-logic for personal KBs. Backend hardened with centralized system group filtering.

## Completed Tasks

| # | Task | Status |
|---|---|---|
| 1 | Alembic migration: add `default_org_role` column | Done |
| 2 | Backend model: add `default_org_role` to `PortalKnowledgeBase` | Done |
| 3 | Backend API: extend create endpoint with `initial_members` + `default_org_role` | Done |
| 4 | Backend API: add PUT `/default-org-role` endpoint (owner-only) | Done |
| 5 | Frontend: 4-step wizard with step indicator and navigation | Done |
| 6 | Frontend: scope picker (org/personal) with skip-logic | Done |
| 7 | Frontend: visibility card selector (Public/Organization/Restricted) | Done |
| 8 | Frontend: contributor checkbox for Public/Organization modes | Done |
| 9 | Frontend: MemberPicker component (group/user autocomplete + role) | Done |
| 10 | Frontend: summary/confirm step with all choices displayed | Done |
| 11 | Frontend: members tab default org role management | Done |
| 12 | Frontend: KB settings edit and access management tabs | Done |
| 13 | Backend: centralized system group filtering (`_get_non_system_group_or_404`) | Done |
| 14 | Backend: filter system groups from picker, members, and invite endpoints | Done |
| 15 | Frontend: remove dead `is_system` filters and render blocks | Done |
| 16 | i18n: 40+ `knowledge_wizard_*` and `knowledge_sharing_*` keys in EN + NL | Done |

## Files Changed

### Backend
- `klai-portal/backend/alembic/versions/a1b2c3d4e5f6_add_default_org_role.py` (new)
- `klai-portal/backend/app/models/knowledge_bases.py` (modified)
- `klai-portal/backend/app/api/app_knowledge_bases.py` (modified — wizard API, system group filtering, helper)
- `klai-portal/backend/app/services/access.py` (modified)

### Frontend
- `klai-portal/frontend/src/routes/app/knowledge/new.tsx` (rewritten — 4-step wizard)
- `klai-portal/frontend/src/routes/app/knowledge/new._types.ts` (new — shared wizard types)
- `klai-portal/frontend/src/routes/app/knowledge/new._components/MemberPicker.tsx` (new — reusable picker)
- `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/members.tsx` (modified — role management, system group cleanup)
- `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/settings.tsx` (modified — settings tabs)
- `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/route.tsx` (modified)
- `klai-portal/frontend/src/routes/app/knowledge/index.tsx` (modified)

### i18n
- `klai-portal/frontend/messages/en.json` (modified)
- `klai-portal/frontend/messages/nl.json` (modified)

## Quality Checks

- ruff check: All checks passed
- ruff format: All files formatted
- ESLint: Clean
- TypeScript tsc --noEmit: No errors
- CI pipeline: Green (quality → build → deploy)

## Key Decisions

- System groups filtered at SQL query level (not frontend) — 3-layer protection: picker, members display, invite endpoint
- MemberPicker extracted as reusable component with `_components/` pattern
- Wizard state managed via `useState` (no URL changes per step)
- Personal scope skips steps 2-3 directly to confirm

## Date: 2026-04-03
