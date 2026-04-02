---
paths:
  - "klai-docs/**"
severity_map:
  platform-docs-app-port: { severity: 0.8, confirmed: 1, false_positives: 0 }
  platform-docs-app-basepath: { severity: 0.8, confirmed: 1, false_positives: 0 }
  platform-docs-app-visibility-values: { severity: 0.8, confirmed: 1, false_positives: 0 }
  platform-docs-app-error-logging: { severity: 0.5, confirmed: 1, false_positives: 0 }
  platform-docs-app-zod-strict-422: { severity: 0.8, confirmed: 1, false_positives: 0 }
  platform-docs-app-auto-provision-org-id: { severity: 0.8, confirmed: 1, false_positives: 0 }
---
# Docs-App Pitfalls

> klai-docs (Next.js) integration from portal-api (`docs_client.py`).
> Derived from SPEC-KB-003 integration debugging, 2026-03-25.

## Index
> Keep this index in sync — add a row when adding an entry below.

| Entry | Sev | Rule |
|---|---|---|
| [platform-docs-app-port](#platform-docs-app-port) | HIGH | docs-app runs on port **3010**, not 3000 |
| [platform-docs-app-basepath](#platform-docs-app-basepath) | HIGH | All routes under `/docs/api/...`, not `/api/...` |
| [platform-docs-app-visibility-values](#platform-docs-app-visibility-values) | HIGH | Map portal `internal` → docs-app `private` |
| [platform-docs-app-error-logging](#platform-docs-app-error-logging) | MED | Log status code + response text; catch ConnectError |
| [platform-docs-app-zod-strict-422](#platform-docs-app-zod-strict-422) | HIGH | Never use `.strict()` on Zod API schemas — use `.passthrough()` |
| [platform-docs-app-auto-provision-org-id](#platform-docs-app-auto-provision-org-id) | HIGH | Auto-provisioning must store the real Zitadel org ID, not the slug |

---

## platform-docs-app-port

**Severity:** HIGH

**Trigger:** Calling the docs-app internal API from portal-api (`docs_client.py`)

The docs-app (klai-docs) runs on port **3010**, not 3000. Docker service name is `docs-app`.

**Wrong:**
```python
base_url="http://docs-app:3000"
```

**Correct:**
```python
base_url="http://docs-app:3010/docs"
```

---

## platform-docs-app-basepath

**Severity:** HIGH

**Trigger:** Calling any API endpoint on docs-app

The Next.js app has `basePath: "/docs"` in `next.config.ts`. All routes — including internal API routes — are served under `/docs/api/...`, not `/api/...`.

**Wrong:**
```
POST http://docs-app:3010/api/orgs/{slug}/kbs   → 404 Not Found
```

**Correct:**
```
POST http://docs-app:3010/docs/api/orgs/{slug}/kbs
```

Use `base_url="http://docs-app:3010/docs"` in the httpx client so relative paths resolve correctly.

---

## platform-docs-app-visibility-values

**Severity:** HIGH

**Trigger:** Creating a KB via the docs-app API when the portal visibility is `internal`

The docs-app DB has a check constraint that only accepts `public` or `private`. The portal uses `internal` as a third visibility option. Passing `internal` causes a 500 from docs-app.

**Wrong:**
```python
json={"visibility": "internal"}  # → 500 Internal Server Error
```

**Correct:**
```python
docs_visibility = "public" if visibility == "public" else "private"
json={"visibility": docs_visibility}
```

Map portal `internal` → docs-app `private` before calling the API.

---

## platform-docs-app-error-logging

**Severity:** MEDIUM

**Trigger:** Debugging docs-app integration failures from portal-api logs

Without the response body in the log, all failures look identical (`httpx.HTTPStatusError`). Always log status code + response text.

**Wrong:**
```python
log.exception("Gitea provisioning failed for KB slug=%s", kb_slug)
```

**Correct:**
```python
log.error(
    "Gitea provisioning failed for KB slug=%s: %s %s",
    kb_slug,
    exc.response.status_code,
    exc.response.text[:500],
)
```

Also catch `httpx.ConnectError` separately — it has no `.response` attribute and accessing it raises `AttributeError`.

---

## platform-docs-app-zod-strict-422

**Severity:** HIGH

**Trigger:** Using `.strict()` on Zod schemas in klai-docs API route validation

Zod's `.strict()` mode rejects any fields not explicitly listed in the schema, returning a 422. When the MCP server or portal-api sends new frontmatter fields that the TypeScript type already supports, `.strict()` rejects them because the Zod schema hasn't been updated yet.

**Why it happens:**
`.strict()` is the opposite of forward-compatible. Any field added by a caller that isn't in the schema causes immediate rejection — even if the underlying TypeScript type and database column already support it. This creates tight coupling between caller and schema.

**Wrong:**
```typescript
// BAD — rejects unknown fields, breaks when callers add new ones
const kbSchema = z.object({
  title: z.string(),
  visibility: z.enum(["public", "private"]),
}).strict();
```

**Correct:**
```typescript
// GOOD — validates known fields, passes unknown ones through
const kbSchema = z.object({
  title: z.string(),
  visibility: z.enum(["public", "private"]),
}).passthrough();
```

**Prevention:**
1. Use `.passthrough()` on all API input schemas — validate what you know, ignore what you don't
2. Only use `.strict()` for internal config objects where unknown fields indicate a bug
3. When adding new fields to an MCP tool or API client, check if the receiving API uses `.strict()` before deploying

**Seen in:** klai-docs API routes — MCP `save_to_docs` added new frontmatter fields, docs-app returned 422 because Zod `.strict()` rejected them.

---

## platform-docs-app-auto-provision-org-id

**Severity:** HIGH

**Trigger:** Auto-provisioning an organization in klai-docs when it doesn't exist yet

When klai-docs auto-provisions an org (e.g. a POST to `/kbs` creates the org if not found), it stored the org slug string as `zitadel_org_id` instead of the real Zitadel org ID from the `X-Org-ID` header. After adding org access verification (`requireOrgAccess()`), this caused 403 errors because the stored ID didn't match the real one.

**Why it happens:**
The auto-provisioning code path was written before org access verification existed. It used whatever identifier was convenient (the slug from the URL) rather than the authoritative org ID from the auth header. The mismatch was invisible until the `requireOrgAccess()` check compared `org.zitadelOrgId` against the header value.

**Wrong:**
```typescript
// BAD — stores the slug as zitadel_org_id
await db.insert(organizations).values({
  slug: orgSlug,
  zitadelOrgId: orgSlug,  // this is the slug, not the real Zitadel org ID!
});
```

**Correct:**
```typescript
// GOOD — stores the real org ID from the auth header
const realOrgId = req.headers.get("X-Org-ID");
await db.insert(organizations).values({
  slug: orgSlug,
  zitadelOrgId: realOrgId,
});
```

**Prevention:**
1. Auto-provisioning must always extract the org ID from the trusted auth header (`X-Org-ID`), never from URL parameters
2. After adding any auth verification to an existing system, check if auto-provisioned data matches what the new verification expects
3. If data is already wrong in production: `UPDATE docs.organizations SET zitadel_org_id = '<real-id>' WHERE slug = '<slug>'`

**Seen in:** klai-docs — auto-provisioned orgs had slug as `zitadel_org_id`, causing 403 after IDOR fix. Required manual DB UPDATE to correct.

**See also:** `security-idor-url-org-slug-trusted` in `pitfalls/security.md`

---

## See Also

- `patterns/platform.md` — docs-app integration patterns
- `docs/CLAUDE.md` — docs-app project instructions
