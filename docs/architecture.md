# Klai Docs — Architecture

## Overview

Klai Docs consists of two layers:

| Layer | Repo | Role |
|---|---|---|
| **Editor UI** | `klai-portal` `/app/docs/` | Manage KBs and write pages (inside the portal SPA) |
| **Backend + reader** | `klai-docs` | Render public KB pages + REST API for the editor |

```
Browser (logged in to portal)
    │  Zitadel Bearer token (via react-oidc-context)
    ▼
getklai.getklai.com/docs/api/...   ← API calls from portal SPA
    │  Caddy → docs-app:3010
    ▼
docs-app  (Next.js, basePath: /docs)
    │  validateBearer() → Zitadel JWKS
    │  Gitea API for content CRUD
    ▼
getklai.getklai.com/docs/{kb}/...  ← Public reader (SSR, no auth)
```

---

## Auth: how it works now

### Editor (authenticated)

The editor lives in the portal SPA (`klai-portal`). The portal already has a Zitadel OIDC session via `react-oidc-context`. When the editor makes an API call to docs-app:

```typescript
// In the portal SPA
const token = auth.user?.access_token   // Zitadel Bearer token

fetch('/docs/api/orgs/getklai/kbs', {
  headers: { Authorization: `Bearer ${token}` }
})
```

docs-app validates this token directly against Zitadel's JWKS endpoint:

```typescript
// lib/auth.ts in docs-app
const JWKS = createRemoteJWKSet(new URL(`${ISSUER}/oauth/v2/keys`))

const { payload } = await jwtVerify(token, JWKS, { issuer: ISSUER })
// payload.sub = Zitadel user ID
// payload["urn:zitadel:iam:user:resourceowner:id"] = Zitadel org ID
```

**Advantage:** no double login. The same session used by Chat, Scribe, and Focus works for Docs as well.

### Public reader

The reader (`getklai.getklai.com/docs/{kb}/...`) is a Next.js SSR page. There is currently no auth for private KBs in the reader — all KBs are accessible to anyone with the URL.

**TODO:** for private KBs, the reader needs to validate the Zitadel session cookie. Approach: Zitadel sets a session cookie on `.getklai.com` after login. The reader reads this cookie and validates it via the Zitadel userinfo endpoint (`/oidc/v1/userinfo`).

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
    api/                ← REST API (requires Bearer token)
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
    auth.ts             ← Zitadel Bearer token validation (jose JWKS)
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
