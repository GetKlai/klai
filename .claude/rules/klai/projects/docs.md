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

## Two separate apps serve docs content — public reader vs authenticated editor (HIGH)

`klai-docs/` (Next.js 15, port 3010) and `klai-portal/frontend/src/routes/app/docs/` are
two completely separate applications that both read from the same Gitea repos.

| App | Auth | URL scheme | Purpose |
|---|---|---|---|
| `klai-docs/` | None (public) | `/docs/{kb-slug}/{page-slug}` | Public SSR reader |
| `klai-portal/frontend` | Required | `/app/docs/{kb-slug}/{page-uuid}` | Authenticated editor |

Caddy routes `/docs/*` to `docs-app:3010`. The portal editor is served from the portal SPA.
`collectSlugs()` and page index building exist in **both** codebases — intentional duplication
because they serve different runtime environments (SSR vs SPA).

**Why this matters:** When investigating routing or URL questions, searching only
`klai-portal/frontend/src/routes/` will miss the public reader entirely. A route not found in
the portal is not evidence that the feature does not exist.

**Prevention:** For any routing or URL investigation, always check:
1. `deploy/caddy/Caddyfile` — what does Caddy route where?
2. `klai-docs/` — public Next.js reader
3. `klai-portal/frontend/src/routes/` — authenticated SPA editor

---

## resolveSlug: use strict equality, not startsWith (MED)

When resolving a page by its ID from a URL param, use strict equality (`===`) not
`startsWith`. A `startsWith` guard designed for short 8-char prefix lookups becomes
functionally equivalent to equality when full UUIDs are used — but it introduces a
theoretical false-positive risk if two UUIDs share the same prefix, and the misleading
name/comment implies partial matching is still intended.

**Prevention:** After any ID format change (prefix → full UUID), replace `startsWith(id)`
with `=== id` and update the comment.

---

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

## KB Editor (portal) — Gitea SHA conflict causes 500 PushRejected on concurrent saves (HIGH)

Gitea requires the current file SHA with every PUT. If two concurrent saves both fetch the SHA
server-side, they get the same value — the second write fails with 500 PushRejected.

**Fix: client-owned SHA + promise queue**

1. GET `/pages/[...path]` returns `sha` in the response — client seeds `shaRef` on page load
2. Every PUT body includes `sha: shaRef.current` — server uses `sha ?? file?.sha` (client takes precedence, falls back to server-fetched on first save)
3. PUT response returns the new SHA — client updates `shaRef` for the next save
4. `saveQueueRef` (promise chain) ensures no two PUTs are ever in-flight simultaneously:
   ```ts
   const thisSave = saveQueueRef.current.catch(() => {}).then(async () => { /* PUT */ })
   saveQueueRef.current = thisSave
   ```
5. `scheduleSave` must use `void doSave().catch(() => {})` — not bare `void doSave()` — to prevent unhandled rejection when no subsequent save chains onto the queue.

**Why:** Server no longer fetches SHA from Gitea on existing-page PUTs. Client tracks the latest SHA
and sends it, closing both the race window AND the extra Gitea round-trip.

**Rule:** Never allow concurrent PUTs to the same Gitea file. Promise queue + client SHA is the
canonical pattern. Do not replace with `isSavingRef` / `pendingSaveRef` booleans — they have a
scheduling race where a save that starts after the guard check still runs concurrently.
