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
| [security-personal-resource-no-ownership-check](#security-personal-resource-no-ownership-check) | HIGH | Resources with per-user ownership need a `created_by` check on every access, not just org scope |
| [security-private-resource-404-not-403](#security-private-resource-404-not-403) | HIGH | Public endpoints must return 404 (not 403) for private/personal resources — never leak existence |

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

## security-personal-resource-no-ownership-check

**Severity:** HIGH

**Trigger:** Adding a `kb_type` / `resource_type` column (e.g. 'org' vs 'personal') to a multi-tenant resource without enforcing per-user ownership

Org-scoping alone (`org_id` in every query) is necessary but not sufficient when a resource can be personal. A personal KB scoped to org X is visible to every member of org X unless there is an additional `created_by` check. Without it, any org member can read, edit, or delete another member's personal resources.

**Why it happens:**
The org-scoping pattern (`_get_{model}_or_404(id, org_id, db)`) is well-established and feels complete. Developers add `kb_type` and `created_by` columns for the creation flow but forget to enforce ownership on read/update/delete — the existing org-scoped helper still returns the resource because the org_id matches.

**The pattern — centralized `checkKBAccess()` helper:**
```typescript
// lib/auth.ts — called AFTER org access is verified
export function checkKBAccess(
  kb: { kb_type: string; created_by: string | null },
  userId: string
): NextResponse | null {
  if (kb.kb_type === "personal" && kb.created_by !== userId) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }
  return null; // access allowed
}

// In every authenticated route handler:
const denied = checkKBAccess(kb, payload.sub);
if (denied) return denied;
```

**The DB query pattern — filter personal resources from listings:**
```sql
-- Only return org KBs + the caller's personal KBs
SELECT * FROM docs.knowledge_bases
WHERE org_id = $1
  AND (kb_type = 'org' OR created_by = $2)
ORDER BY created_at
```

**Prevention:**
1. When adding a `type` column that distinguishes shared vs personal resources, always add a `created_by` column at the same time
2. Create a centralized access-check helper (like `checkKBAccess`) and apply it to ALL route handlers — not just "sensitive" ones
3. Listing queries must filter: return org-wide resources + only the caller's personal resources
4. The ownership check is a second layer on top of org-scoping, not a replacement for it

**Seen in:** klai-docs — `kb_type` ('org'|'personal') and `created_by` columns added to `docs.knowledge_bases`. Without `checkKBAccess()`, any org member could access another member's personal KBs.

**See also:** `security-idor-missing-org-scope` (the first layer: org scoping)

---

## security-private-resource-404-not-403

**Severity:** HIGH

**Trigger:** A public/unauthenticated endpoint encounters a resource that should not be visible (personal KB, draft, private resource)

Returning 403 ("Forbidden") on a public endpoint confirms that the resource exists — an information leak. An attacker can enumerate resource slugs and learn which personal KBs exist, even without accessing their content. The correct response is 404 ("Not found"), making personal resources indistinguishable from non-existent ones.

**Why it happens:**
Developers apply the same access-denied response (403) everywhere. On authenticated endpoints, 403 is correct — the caller already proved their identity, and knowing the resource exists is expected. On public endpoints (SSR pages, unauthenticated API calls), there is no caller identity, so "you don't have access" leaks the existence of the resource.

**Wrong — leaks existence on public endpoints:**
```typescript
// BAD — public SSR page returns 403 for personal KBs
if (kb.kb_type === "personal") {
  return new Response("Forbidden", { status: 403 });
}
```

**Correct — return 404 on public endpoints, 403 on authenticated endpoints:**
```typescript
// Public endpoint (SSR reader, unauthenticated API)
if (kb.kb_type === "personal") notFound(); // → 404

// Authenticated endpoint (editor API with Bearer token)
if (kb.kb_type === "personal" && kb.created_by !== userId) {
  return NextResponse.json({ error: "Forbidden" }, { status: 403 });
}
```

**Decision tree:**
| Endpoint type | Resource is private | Response |
|---|---|---|
| Public / unauthenticated | Yes | 404 |
| Authenticated, caller is not owner | Yes | 403 |
| Authenticated, caller is owner | Yes | 200 (normal) |

**Prevention:**
1. On all public endpoints (no auth required), private/personal resources must return 404
2. On authenticated endpoints, private resources owned by someone else return 403
3. Apply the check early — before any content is fetched or rendered
4. Add the check to `generateMetadata()` / head functions too, not just the page body — metadata can leak titles and descriptions

**Seen in:** klai-docs — public reader page (`app/(reader)/[...path]/page.tsx`) and unauthenticated GET endpoints (`tree`, `pages`) return `notFound()` / 404 for personal KBs. Authenticated endpoints use `checkKBAccess()` which returns 403.

**See also:** `security-personal-resource-no-ownership-check` (the ownership model itself)

---

*(Add more entries here with `/retro "description"` after security incidents.)*
