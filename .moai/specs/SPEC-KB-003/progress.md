## SPEC-KB-003 Progress

- Started: 2026-03-25

## Pre-analysis findings

### Already done (from KB-002)
- AC-1: Gitea provisioning on KB creation — `docs_client.provision_and_store` called in `create_app_knowledge_base` ✅
- AC-8: Admin connectors route removed — no `/admin/connectors` routes exist in frontend ✅

### Still to implement
- Phase 2: `portal_user_kb_access` table (migration + model) + update `get_accessible_kb_slugs()` + member API endpoints
- Phase 3: KB detail page refactored to dashboard (docs section, connectors, volume, usage)
- Phase 4: Role-gate connector actions (Owner-only add/edit/delete — currently no role check)
- Phase 5: Members section UI in KB detail page (groups + individual invites)
- Phase 6: Personal named KB scope selector + separate section in index.tsx
- Phase 7: /app/docs shows locked KB cards for inaccessible KBs

### Key files identified
- Backend model: `portal/backend/app/models/knowledge_bases.py`
- Backend API: `portal/backend/app/api/app_knowledge_bases.py`
- Access service: `portal/backend/app/services/access.py` (`get_accessible_kb_slugs`)
- Frontend KB detail: `portal/frontend/src/routes/app/knowledge/$kbSlug.tsx`
- Frontend KB list: `portal/frontend/src/routes/app/knowledge/index.tsx`
- Frontend KB new: `portal/frontend/src/routes/app/knowledge/new.tsx`
- Frontend docs: `portal/frontend/src/routes/app/docs/index.tsx`
- Latest migration: `z2a3b4c5d6e7_add_kb_and_docs_libraries.py`
