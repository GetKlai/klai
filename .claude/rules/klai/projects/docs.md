---
paths:
  - "klai-docs/**"
  - "klai-portal/frontend/src/routes/app/docs/**"
  - "klai-portal/frontend/src/components/kb-editor/**"
  - "klai-portal/frontend/src/lib/kb-editor/**"
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

## KB Editor (portal) — BlockNote persistence (HIGH)

**Never use `blocksToHTMLLossy` for saving page content.** It silently drops empty paragraphs,
nested block structure, and custom inline node props (e.g. WikiLink's `kbSlug`).
It is a display-only serializer, not a persistence format.

**Use:** `JSON.stringify(editor.document)` — BlockNote's native lossless format.

**On load — format detection by prefix:**
```typescript
const trimmed = initialContent.trimStart()
const format = trimmed.startsWith('[') ? 'json'
             : trimmed.startsWith('<') ? 'html'
             : 'markdown'
```
Existing pages stored as HTML or markdown still load correctly via this fallback.

## KB Editor (portal) — Save on navigation (HIGH)

**`beforeunload` fires for full-page navigation (address bar, browser back), NOT for TanStack Router SPA navigation.**

- Full-page unload: use `fetch` with `keepalive: true`. This supports `Authorization` headers and continues after the page unloads. `navigator.sendBeacon` cannot set auth headers — do not use it.
- SPA sidebar navigation: use the `doSaveRef` pattern — layout holds a ref to the child page's `saveNow` function, calls it in `onSelect` before route change.

**These two mechanisms must both be present.** One alone is not enough.
