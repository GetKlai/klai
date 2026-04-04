---
paths:
  - "klai-docs/**"
---
# Docs App (klai-docs) Pitfalls

## Connection
- Port: **3010** (not 3000). Base path: `/docs` (`next.config.ts basePath`).
- Correct base URL: `http://docs-app:3010/docs`
- All API routes: `/docs/api/orgs/{slug}/...` — not `/api/...`.

## Visibility mapping
- Portal `internal` → docs-app `private`. DB constraint only accepts `public` | `private`.
- Map before calling: `docs_visibility = "public" if visibility == "public" else "private"`.

## Zod schemas
- Never use `.strict()` on API input schemas — rejects unknown fields, breaks forward compatibility.
- Use `.passthrough()` — validates known fields, ignores unknown.

## Auto-provisioning
- When auto-creating an org, store the real Zitadel org ID from `X-Org-ID` header, not the slug.
- Wrong: `zitadelOrgId: orgSlug`. Correct: `zitadelOrgId: req.headers.get("X-Org-ID")`.

## Error logging
- Always log status code + response body before returning generic error messages.
- Catch `httpx.ConnectError` separately (no `.response` attribute).

## Security (shared with portal)
- `requireOrgAccess()` on ALL org-scoped routes — verify `X-Org-ID` matches DB record.
- Personal KBs: `checkKBAccess(kb, userId)` on authenticated routes.
- Public endpoints: return 404 for personal KBs (never 403 — leaks existence).
