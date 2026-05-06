# SPEC-TI-004 — RLS + auth-resolver fix op research schema

**Audit ref:** findings **A-10**, **A-11**, **A-12**
**Standards ref:** `standards.md` sections 1, 3, 8, 9, 10
**Priority:** HIGH
**Status:** Ready

## Goal

Drie gecombineerde fixes op klai-focus/research-api:
1. **A-10:** RLS rollout op `research.notebooks/sources/chunks/chat_messages`.
2. **A-11:** Type-fix `research.chat_messages.tenant_id` van VARCHAR(64) → UUID.
3. **A-12:** Auth-resolver in `app/core/auth.py::_get_user_org` MOET JWT resourceowner als bron-van-waarheid; geen "willekeurige eerste rij" meer voor multi-org users.

## Acceptance criteria (EARS)

### A-11 type-fix (eerst — A-10 hangt ervan af)
- **AC-1** Migration `ALTER TABLE research.chat_messages ALTER COLUMN tenant_id TYPE uuid USING tenant_id::uuid` — geen users in prod, dus USING-cast veilig.
- **AC-2** Model `ChatMessage.tenant_id` SQLAlchemy type van `String(64)` naar `UUID(as_uuid=True)`.

### A-10 RLS
- **AC-3** Helper-function `_rls_current_org_id() RETURNS uuid` (research schema heeft tenant_id als UUID).
- **AC-4** ENABLE + FORCE + Cat-D `tenant_isolation` op alle 4 research tabellen.
- **AC-5** Sessie-helpers in `klai-focus/research-api/app/db.py`.
- **AC-6** `entrypoint.sh` voor auto-migrate (vandaag mist).

### A-12 auth-resolver
- **AC-7** `_get_user_org` query MOET `WHERE pu.zitadel_user_id = :uid AND po.zitadel_org_id = :rid LIMIT 1` (zie standards-doc 10).
- **AC-8** `:rid` komt uit JWT `urn:zitadel:iam:org:project:resourceowner` claim.
- **AC-9** Geen rij gevonden → 403 `user_not_in_resourceowner_tenant`. Geen fall-back op LIMIT 1 zonder rid.
- **AC-10** `_get_notebook_or_404` MOET expliciete `Notebook.tenant_id == user.tenant_id` check op personal-scope branch.

### Tests
- **AC-11** `test_research_rls.py`: fail-loud + filter + bypass-blocked + INSERT-blocked
- **AC-12** `test_auth_resolver_multi_org.py`: multi-org user → JWT resourceowner determines tenant; mismatch → 403
- **AC-13** `test_notebooks_personal_scope_tenant_check.py`: personal notebook in vorige tenant niet zichtbaar

## Implementation

1. Type migratie eerst (separate alembic rev).
2. RLS migratie + post_deploy SQL.
3. Auth-resolver refactor (`app/core/auth.py` lines 104-113).
4. `_get_notebook_or_404` aanpassing (`app/api/notebooks.py` lines 71-87).
5. Sessie-helpers + `entrypoint.sh`.

## Operator-step

```bash
ssh core-01 "docker exec -i klai-core-postgres-1 psql -U klai -d klai" < klai-focus/research-api/alembic/versions/post_deploy_<rev>.sql
docker restart klai-core-research-api-1
```

## Worktree

`klai-research-rls` — `feature/SPEC-TI-004-RLS-RESEARCH`.
