---
paths:
  - "**/backend/**/*.py"
  - "**/api/**/*.py"
---
# Security Pitfalls

> Authentication, authorization, and multi-tenancy mistakes in klai-mono.

---

## security-idor-missing-org-scope

**Severity:** CRIT

**Trigger:** Creating or modifying an endpoint that accesses a resource by ID (e.g. `DELETE /api/knowledge/bases/{kb_slug}`)

Without an org_id scope in the database query, any authenticated user can supply any resource ID and access or delete resources belonging to another tenant (IDOR — Insecure Direct Object Reference).

**Wrong:**
```python
# BAD — no org check, any tenant can hit this
result = await db.execute(
    select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
)
```

**Correct — always use the `_get_{model}_or_404` helper:**
```python
# GOOD — helper enforces org scope before any mutation
kb = await _get_kb_or_404(kb_slug, org.id, db)
```

The helper pattern:
```python
async def _get_kb_or_404(kb_slug: str, org_id: int, db: AsyncSession) -> KnowledgeBase:
    result = await db.execute(
        select(KnowledgeBase).where(
            KnowledgeBase.slug == kb_slug,
            KnowledgeBase.org_id == org_id,
        )
    )
    kb = result.scalar_one_or_none()
    if kb is None:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    return kb
```

**Seen in:** `portal/backend/app/api/knowledge_bases.py` — `revoke_kb_group_access` discarded `org_id` before the delete query, allowing cross-tenant deletion.

**Defense in depth:** PostgreSQL RLS policies enforce org isolation at the database level via `app.current_org_id` (set by `set_tenant()` in every authenticated request). RLS catches what the helper misses; the helper remains explicit and auditable.

**Rules:**
1. Every endpoint that mutates a resource by ID must call `_get_{model}_or_404(id, org.id, db)` before any mutation
2. Never use a resource ID from the URL path without an org-scoped lookup first
3. Junction tables without `org_id` (e.g. `portal_group_kb_access`) must only be accessed AFTER verifying the parent via its helper
4. New helpers must always include `org_id` as a parameter

**See also:** `.claude/rules/klai/multi-tenant-pattern.md` — active enforcement rule with path triggers

---

*(Add more entries here with `/retro "description"` after security incidents.)*
