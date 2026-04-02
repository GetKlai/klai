---
paths:
  - "**/backend/**/*.py"
  - "**/api/**/*.py"
  - "klai-docs/**"
---
# Security Pitfalls

> Authentication, authorization, and multi-tenancy mistakes in klai-mono.

## Index
> Keep this index in sync — add a row when adding an entry below.

| Entry | Sev | Rule |
|---|---|---|
| [security-idor-missing-org-scope](#security-idor-missing-org-scope) | CRIT | Every resource lookup must include org_id scope |
| [security-idor-url-org-slug-trusted](#security-idor-url-org-slug-trusted) | CRIT | Never trust org_slug from URL — verify caller belongs to that org via token |

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

## security-idor-url-org-slug-trusted

**Severity:** CRIT

**Trigger:** Any multi-tenant API where the org identifier comes from the URL path (e.g. `/api/orgs/{org_slug}/...`)

The klai-docs API trusted the `org_slug` from the URL path without verifying the caller actually belongs to that organization. All `/api/orgs/{org}/...` endpoints were affected — any authenticated user could access any org's knowledge bases by changing the slug in the URL.

**Why it happens:**
URL path parameters are user-controlled input. Without explicit verification, the API assumes the caller is authorized for any org they name in the URL. This is a classic IDOR: the "direct object reference" is the org slug.

**Wrong:**
```typescript
// BAD — trusts org_slug from URL, no ownership check
export async function GET(req: Request, { params }: { params: { org: string } }) {
  const org = await db.query.organizations.findFirst({
    where: eq(organizations.slug, params.org),
  });
  // proceeds to return org's data...
}
```

**Correct — centralized `requireOrgAccess()` helper:**
```typescript
// lib/auth.ts — checks payload.org_id matches the org's zitadel_org_id
export async function requireOrgAccess(
  req: Request,
  orgSlug: string,
): Promise<Organization> {
  const orgId = req.headers.get("X-Org-ID");
  const org = await db.query.organizations.findFirst({
    where: eq(organizations.slug, orgSlug),
  });
  if (!org || org.zitadelOrgId !== orgId) {
    throw new HttpError(403, "Not authorized for this organization");
  }
  return org;
}

// In every route handler:
const org = await requireOrgAccess(req, params.org);
```

**Seen in:** klai-docs — all 8+ route files under `app/docs/api/orgs/[org]/` were vulnerable. Fixed by adding `requireOrgAccess()` to every route handler.

**Prevention:**
1. Centralize org access verification in a single helper — never inline the check
2. Apply the helper to ALL org-scoped routes, not just "sensitive" ones
3. The helper must compare a trusted source (auth token / header from reverse proxy) against the DB record — never compare URL params against each other

**See also:** `security-idor-missing-org-scope` (same class of bug in Python/FastAPI)

---

*(Add more entries here with `/retro "description"` after security incidents.)*
