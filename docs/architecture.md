# Klai Docs — Architecture

## Overzicht

Klai Docs bestaat uit twee lagen:

| Laag | Repo | Rol |
|---|---|---|
| **Editor UI** | `klai-portal` `/app/docs/` | KB's beheren en pagina's schrijven (in de portal SPA) |
| **Backend + reader** | `klai-docs` | Publieke KB-pagina's renderen + API voor editor |

```
Browser (ingelogd in portal)
    │  Zitadel Bearer token (via react-oidc-context)
    ▼
getklai.getklai.com/docs/api/...   ← API calls vanuit portal SPA
    │  Caddy → docs-app:3010
    ▼
docs-app  (Next.js, basePath: /docs)
    │  validateBearer() → Zitadel JWKS
    │  Gitea API voor content CRUD
    ▼
getklai.getklai.com/docs/{kb}/...  ← Publieke reader (SSR, geen auth)
```

---

## Auth: hoe het nu werkt

### Editor (authenticated)

De editor leeft in de portal SPA (`klai-portal`). De portal heeft al een Zitadel OIDC-sessie via `react-oidc-context`. Wanneer de editor een API-call naar docs-app doet:

```typescript
// In de portal SPA
const token = auth.user?.access_token   // Zitadel Bearer token

fetch('/docs/api/orgs/voys/kbs', {
  headers: { Authorization: `Bearer ${token}` }
})
```

docs-app valideert dit token direct via Zitadel's JWKS-endpoint:

```typescript
// lib/auth.ts in docs-app
const JWKS = createRemoteJWKSet(new URL(`${ISSUER}/oauth/v2/keys`))

const { payload } = await jwtVerify(token, JWKS, { issuer: ISSUER })
// payload.sub = Zitadel user ID
// payload["urn:zitadel:iam:user:resourceowner:id"] = Zitadel org ID
```

**Voordeel:** geen dubbele login. Dezelfde sessie die Chat, Scribe en Focus gebruiken, werkt ook voor Docs.

### Publieke reader

De reader (`getklai.getklai.com/docs/{kb}/...`) is een Next.js SSR-pagina. Er is momenteel geen auth voor private KBs in de reader — alle KBs zijn bereikbaar voor wie de URL kent.

**TODO:** voor private KBs moet de reader de Zitadel-sessiecookie valideren. De aanpak: Zitadel zet een sessiescookie op `.getklai.com` na login. De reader leest dit cookie uit en valideert het via de Zitadel userinfo-endpoint (`/oidc/v1/userinfo`).

---

## Auth: hoe het werkt als standalone product

Klai Docs is nu gekoppeld aan de Klai-portal voor de editor UI. Als je het als los product wil draaien — zonder `klai-portal` — heb je twee dingen nodig:

### 1. Eigen OAuth2/PKCE flow voor de editor

De portal-sessie is er niet meer. De editor-UI heeft dan een eigen OAuth2-flow nodig. De cleanste aanpak voor een Next.js standalone editor:

**next-auth** (al eerder geconfigureerd en verwijderd) is precies hiervoor bedoeld:

```typescript
// lib/auth.ts — standalone versie
import NextAuth from "next-auth"

export const { handlers, auth, signIn, signOut } = NextAuth({
  trustHost: true,
  providers: [ZitadelProvider],   // of een andere OIDC provider
  ...
})
```

De middleware beschermt dan `/admin/*` routes en redirect naar de login-pagina:

```typescript
// middleware.ts — standalone versie
if (pathname.startsWith("/admin")) {
  const session = await auth()
  if (!session) redirect to /docs/api/auth/signin
}
```

### 2. Eigen editor UI (BlockNote)

De editor UI leeft nu in de portal SPA. Standalone zou die in `klai-docs` zelf moeten zitten, als een Next.js server component + client component met BlockNote.

De basis was al gebouwd (zie git history vóór commit `a6bebdd`) en kan worden teruggehaald.

### Wat er nodig is voor standalone

| Component | Status | Actie |
|---|---|---|
| NextAuth config | Verwijderd | Toevoegen uit git history |
| Editor UI (BlockNote) | Verwijderd | Toevoegen uit git history |
| Middleware auth guard | Vereenvoudigd | Terugzetten naar auth-versie |
| `app/(editor)/` routes | Verwijderd | Toevoegen uit git history |
| Zitadel OIDC app redirect URI | Aangemaakt (client ID: `364805214877777922`) | Redirect URI uitbreiden voor standalone URL |

Alles wat verwijderd is, staat in git op commit `a50797a` (de laatste commit vóór de standalone→portal migratie).

---

## Bestandsstructuur

```
klai-docs/
  app/
    (reader)/           ← Publieke KB-reader (SSR, geen auth)
      [...path]/
        page.tsx
    api/                ← REST API (vereist Bearer token)
      orgs/[org]/kbs/
        route.ts        ← GET lijst KBs, POST maak KB aan
        [kb]/
          pages/[...path]/route.ts   ← CRUD pagina's
          meta/[...path]/route.ts    ← Reorder nav (_meta.yaml)
          upload/route.ts            ← Upload .md bestand
          tree/route.ts              ← Nav tree ophalen
  lib/
    auth.ts             ← Zitadel Bearer token validatie (jose JWKS)
    db.ts               ← PostgreSQL client (docs schema)
    gitea.ts            ← Gitea REST API client + nav tree builder
    markdown.ts         ← Frontmatter parse/serialize, _meta.yaml helpers
    caddy.ts            ← Tenant caddyfiles voor custom domains (toekomst)
  middleware.ts         ← Org slug resolutie uit hostname → x-docs-org header
  migrations/
    001_docs_schema.sql ← PostgreSQL docs schema
```
