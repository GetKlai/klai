# Klai Docs — Architecture

## Overview

Klai Docs consists of two layers:

| Layer | Repo | Role |
|---|---|---|
| **Editor UI** | `klai-portal` `/app/docs/` | Manage KBs and write pages (inside the portal SPA) |
| **Backend + reader** | `klai-docs` | Render public KB pages + REST API for the editor |

```
Browser (logged in to the portal, session cookie)
    │  /api/docs/...  ← editor calls from the portal SPA
    ▼
portal-api BFF proxy  (klai-portal/backend/app/api/proxy.py)
    │  X-Internal-Secret: <docs secret>
    │  X-Klai-Verified-User-Id / Org-Id / Org-Slug
    ▼
docs-app  (Next.js, port 3010, basePath: /docs)
    │  requireOrgAccess() → docs secret + verified identity
    │  Gitea API for content CRUD
    ▼
acme.example.com/docs/{kb}/...  ← Public reader (SSR, no login)
```

---

## Auth: how it works now

### Editor (authenticated)

The editor lives in the portal SPA (`klai-portal`). The portal is a backend-for-frontend: the browser holds a session cookie, and portal-api keeps the Zitadel tokens server-side (`klai-portal/docs/auth-flows.md`). The editor calls docs-app through portal-api's proxy at `/api/docs/*` (`DOCS_BASE` in `klai-portal/frontend/src/lib/kb-editor/tree-utils.ts:5`). The proxy checks the session and the caller's membership, then forwards the request to `http://docs-app:3010/docs` with the docs secret and the portal-verified identity headers (`klai-portal/backend/app/api/proxy.py:148-197`). The reverse proxy answers 404 for docs-app's own `/docs/api/*` path, so the API is reached only through portal-api (`deploy/caddy/Caddyfile:504-510`).

docs-app identifies a caller only with `X-Internal-Secret` equal to `DOCS_INTERNAL_SECRET`, compared in constant time (`klai-docs/lib/auth.ts:31-62`). With the secret, the `X-Klai-Verified-*` headers name the user and the one organisation the portal verified their membership of; a session token is not read, and a request without the secret identifies nobody.

Service callers send the same secret with an `X-User-ID`, which `requireAuthOrService` accepts as the acting user (`klai-docs/lib/auth.ts:43-62`); portal-api's provisioning client uses this to create a tenant's repository. Org-scoped routes call `requireOrgAccess`, which identifies the caller and resolves the organisation from the slug in the path (`klai-docs/lib/auth.ts:101-120`), and personal KBs are limited to their creator by `checkKBAccess` (`klai-docs/lib/auth.ts:82-90`).

**Advantage:** no double login. The portal session used for Chat and Scribe works for Docs as well.

### Public reader

The reader (`acme.example.com/docs/{kb}/...`) is a Next.js SSR page without login. It serves only public KBs: a personal KB or a KB with `visibility = private` returns 404 (`klai-docs/app/(reader)/[...path]/page.tsx:116`).

---

## Auth: how it works as a standalone product

Klai Docs is currently coupled to klai-portal for the editor UI. To run it as a standalone product — without `klai-portal` — two things are needed:

### 1. Own OAuth2/PKCE flow for the editor

The portal session is no longer available. The editor UI would need its own OAuth2 flow. The cleanest approach for a standalone Next.js editor:

**next-auth** (previously configured and removed) is designed exactly for this:

```typescript
// lib/auth.ts — standalone version
import NextAuth from "next-auth"

export const { handlers, auth, signIn, signOut } = NextAuth({
  trustHost: true,
  providers: [ZitadelProvider],   // or another OIDC provider
  ...
})
```

The middleware then protects `/admin/*` routes and redirects to the login page:

```typescript
// middleware.ts — standalone version
if (pathname.startsWith("/admin")) {
  const session = await auth()
  if (!session) redirect to /docs/api/auth/signin
}
```

### 2. Own editor UI (BlockNote)

The editor UI currently lives in the portal SPA. Standalone, it would need to live in `klai-docs` itself, as a Next.js server component + client component with BlockNote.

The foundation was already built (see git history before commit `a6bebdd`) and can be restored.

### What is needed for standalone

| Component | Status | Action |
|---|---|---|
| NextAuth config | Removed | Restore from git history |
| Editor UI (BlockNote) | Removed | Restore from git history |
| Middleware auth guard | Simplified | Restore auth version |
| `app/(editor)/` routes | Removed | Restore from git history |
| Zitadel OIDC app redirect URI | Created (client ID: `364805214877777922`) | Extend redirect URI for standalone URL |

Everything that was removed is in git at commit `a50797a` (the last commit before the standalone-to-portal migration).

---

## File structure

```
klai-docs/
  app/
    (reader)/           ← Public KB reader (SSR, no auth)
      [...path]/
        page.tsx
    api/                ← REST API (requires the docs secret)
      orgs/[org]/kbs/
        route.ts                      ← GET list KBs, POST create KB
        [kb]/
          pages/[...path]/route.ts    ← CRUD pages
          page-rename/[...path]/route.ts ← Rename page + update _sidebar.yaml + track redirects
          page-index/route.ts         ← List all pages with id/slug/title (for wikilink picker)
          sidebar/route.ts            ← GET/PUT _sidebar.yaml navigation manifest
          meta/[...path]/route.ts     ← Legacy: reorder nav (_meta.yaml fallback)
          upload/route.ts             ← Upload .md file
          tree/route.ts               ← Nav tree (builds from _sidebar.yaml)
  lib/
    auth.ts             ← caller identity: docs secret with the portal's verified identity or a service user
    db.ts               ← PostgreSQL client (docs schema)
    gitea.ts            ← Gitea REST API client + nav tree builder
    markdown.ts         ← Frontmatter parse/serialize, _sidebar.yaml helpers, slugify
    caddy.ts            ← Tenant caddyfiles for custom domains (future)
  middleware.ts         ← Org slug resolution from hostname → x-docs-org header
  migrations/
    001_docs_schema.sql ← PostgreSQL docs schema
  docs/
    architecture.md     ← This file
```
