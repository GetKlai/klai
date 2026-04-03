---
paths:
  - "klai-portal/backend/app/api/**/*.py"
---

# Multi-Tenant Security Pattern

**[HARD] Every database query on a tenant-scoped model MUST be scoped to org_id.**

## The pattern

All tenant-scoped models (groups, knowledge_bases, connectors, etc.) must be
accessed via a `_get_{model}_or_404(id, org_id, db)` helper — never via a raw
`select(...).where(Model.id == id)` without org_id.

The canonical examples:

```python
# groups.py — reference implementation
async def _get_group_or_404(group_id: int, org_id: int, db: AsyncSession) -> PortalGroup:
    result = await db.execute(select(PortalGroup).where(
        PortalGroup.id == group_id, PortalGroup.org_id == org_id
    ))
    ...

# knowledge_bases.py
async def _get_kb_or_404(kb_slug: str, org_id: int, db: AsyncSession) -> KnowledgeBase:
    result = await db.execute(select(KnowledgeBase).where(
        KnowledgeBase.slug == kb_slug, KnowledgeBase.org_id == org_id
    ))
    ...
```

## Why this matters

Without org_id in the query, an attacker can supply any `id` and access or
delete resources belonging to another tenant (IDOR). This happened in
`revoke_kb_group_access` where the org was discarded before the delete.

## Personal resource ownership

- Org-scoping alone is insufficient for personal resources. Add `created_by` check.
- Listings: return org-wide resources + only the caller's personal resources.
- Public endpoints: return 404 (not 403) for private resources — never leak existence.
- Authenticated endpoints: return 403 for resources owned by someone else.

## Defense in depth: PostgreSQL RLS

As a second layer, RLS policies on all tenant-scoped tables enforce org isolation
at the database level via `app.current_org_id` (set by `set_tenant()` in every
authenticated request). RLS catches what the helper misses; the helper remains
explicit and auditable.

## RLS + Alembic

- `portal_api` user is NOT table owner — cannot run `ALTER TABLE ENABLE ROW LEVEL SECURITY`.
- Run RLS DDL directly as `klai` superuser via psql. Keep Alembic migration for code history.
- Use `IF NOT EXISTS` in all policy/index creation to make migrations idempotent.

## RLS + SQLAlchemy

- ORM adds implicit `RETURNING` — triggers SELECT policy on inserts.
- Split `ALL` policies into separate `SELECT` and `INSERT` when inserting role differs from reading role.
- Use `text()` raw SQL for audit/analytics inserts on split-policy tables.

## RLS coverage

Strict: `portal_groups`, `portal_knowledge_bases`, `portal_group_products`, `portal_group_memberships`, `portal_group_kb_access`, `portal_kb_tombstones`, `portal_user_kb_access`, `portal_retrieval_gaps`, `portal_taxonomy_nodes`, `portal_taxonomy_proposals`, `portal_user_products`.
Permissive: `portal_users`, `portal_connectors`.
Split (SELECT scoped, INSERT permissive): `portal_audit_log`, `product_events`, `vexa_meetings`.

## Rules for agents

1. When creating a new endpoint that mutates a resource by ID:
   - Call `_get_{model}_or_404(id, org.id, db)` before any mutation
   - Never use the resource ID from the URL path without an org-scoped lookup

2. When creating a new helper:
   - Always include `org_id` as a parameter
   - Mark with `# @MX:ANCHOR fan_in=N` so callers are tracked

3. Code review red flag:
   ```python
   # BAD — no org check
   select(KnowledgeBase).where(KnowledgeBase.slug == kb_slug)

   # GOOD — always use the helper
   kb = await _get_kb_or_404(kb_slug, org.id, db)
   ```

4. Junction tables without org_id (e.g. `portal_group_kb_access`) must be
   accessed only AFTER verifying the parent resource via its helper.
