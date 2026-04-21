# F-038 — BFF → Downstream services architectural gap

**Datum:** 2026-04-19
**Severity:** HIGH (availability, not confidentiality)
**Trigger:** Ontdekt tijdens SEC-012 implementatie

## Context

SPEC-AUTH-008 migreerde portal-frontend van Bearer-token auth (access_token in
`Authorization` header) naar BFF cookie-based auth (`__Secure-klai_session` + CSRF).
De frontend `apiFetch` stuurt sindsdien **alleen cookies**, geen Authorization header.

Caddy routeert drie services direct (strip_prefix, zonder portal-api proxy):

| Route | Target | Auth mechanism | Huidige status |
|---|---|---|---|
| `/research/*` | research-api:8030 | `HTTPBearer` → validates JWT via JWKS | **broken — 401** |
| `/scribe/*` | scribe-api:8020 | `HTTPBearer` → validates JWT via JWKS | **broken — 401** |
| `/docs/*` | docs-app:3010 | docs-app own auth (`validateBearer()`) | **broken — 308 redirect to login** |

Portal-api heeft WEL de BFF-session met een valide Zitadel `access_token` per user.
Frontend stuurt die niet mee (by design — BFF rule: no tokens in JS).

**Netto:** Focus, Scribe en klai-docs modules zijn silent-broken sinds AUTH-008 deploy.

## Waarom silent?

- Frontend toont gewoon foutmeldingen die lijken op "loading" of "empty state"
- Geen user reports (nog)
- Geen Sentry alerts (401 op een backend is not an error per se)

## Fix-opties

| Optie | Aanpak | Belang |
|---|---|---|
| **1. Proxy via portal-api** ✅ | `/api/research/*`, `/api/scribe/*`, `/api/docs/*` achter portal-api. BFF session → Authorization: Bearer op upstream. Services niet meer publiek via Caddy. | Industry-standard BFF; één auth boundary; eenvoudig token-management; makkelijk audit-log |
| 2. BFF cookie delen | Fernet key in SOPS naar alle services. Elke service decrypt cookies. | Breekt single-source-of-truth; hoge blast radius bij key-rotation |
| 3. Token-exchange endpoint | `/api/auth/token` → JS houdt access_token in memory. Pre-BFF model. | Ondermijnt BFF belofte (no tokens in JS) — XSS window opnieuw open |

**Gekozen:** Optie 1.

## SEC-023 — Internal Services BFF Proxy (vervangt SEC-012 research-api scope)

**Scope:**
- portal-api: nieuwe proxy router `app/api/proxy.py` — `/api/research/*`, `/api/scribe/*`, `/api/docs/*` → upstream met BFF token-injection
- Caddy: verwijder publieke `handle /research/*`, `handle /scribe/*`, `handle /docs/*` blocks; alleen internal Docker network
- Frontend: migreer `FOCUS_BASE = '/research/v1'` → `'/api/research/v1'`, scribe idem, docs idem
- Streaming: research-api + scribe-api hebben SSE-streams (chat-synthesis). Proxy MUST be streaming-safe via `httpx.stream()` + `StreamingResponse`

**Acceptatie:**
- Frontend call `/api/research/v1/notebooks` → portal-api retrieves BFF session, forwards to research-api:8030 with `Authorization: Bearer <session.access_token>` → 200
- SSE chat endpoints blijven streamen zonder buffering
- Caddy `/research/*`, `/scribe/*`, `/docs/*` (root-level) geven 404 na merge
- research-api, scribe-api, docs-app alleen nog bereikbaar via `klai-net` intern

**Out of scope (separate SPEC):**
- Audience enforcement per service (SEC-012 blijft openstaand — na proxy-fix is audience-check defense-in-depth ipv must-have)
- docs-app `validateBearer()` volledige analyse (check of die wél al met session werkt)

## Zitadel state

Tijdens SEC-012 onderzoek maakte ik Zitadel API-app aangemaakt:
- **App name:** research-api
- **App ID:** 369294845191127057
- **Client ID (audience):** 369294845191192593

Niet direct gebruikt in SEC-023 (proxy-pattern eist geen per-service audience voor route-to-werken). App blijft staan voor toekomstige SEC-012 defense-in-depth audience check.

## Volgende stappen

SEC-023 wordt **nu** geïmplementeerd in deze sessie. Fasegewijs met commits als rollback-points:
1. Portal-api proxy router + tests
2. Frontend path-migratie
3. Caddy public routes verwijderen
4. Deploy + smoke-test alle drie modules

## CSRF review (post-deploy)

BFF proxy accepts all HTTP methods on `/api/research|scribe|docs/*`. CSRF review
(Task #33, 2026-04-19) concludes the proxy is **already fully CSRF-protected**:

- `__Secure-klai_session` cookie: `SameSite=Lax` (app/core/session.py:23, api/auth.py:213)
  — blocks cross-site form-submit for POST/PUT/DELETE.
- `SessionMiddleware._check_csrf` (middleware/session.py:107) enforces
  `X-CSRF-Token` header on all non-safe methods. Safe list: GET/HEAD/OPTIONS.
  Exempt prefixes: `/api/auth/oidc/*`, `/api/signup`, `/api/health`, `/api/public/`,
  `/internal/`, `/partner/`, `/widget/`. The `/api/research|scribe|docs/*` proxy
  paths are **NOT exempt** — all mutations require a valid CSRF token.
- Token validation uses constant-time `_secure_equal` (middleware/session.py:129).
- Middleware registration order (main.py:170): SessionMiddleware runs BEFORE
  the proxy router → CSRF check happens before upstream request is built.

**No additional CSRF work required for SEC-023.**

## Streaming upload follow-up (Task #32)

Current `proxy.py` reads full request body with `body = await request.body()` and
passes it as `content=body`. For large uploads (knowledge-ingest file imports) this
buffers in portal-api memory. Safe but non-optimal. Follow-up: switch to
`content=request.stream()` or pass the `Receive` callable directly to httpx. Not
urgent — knowledge-ingest uploads currently bypass the BFF proxy entirely (direct
`/api/v1/ingest` under portal-api itself, not under the new `/api/research|scribe|docs`
prefixes). Listed as Task #32 for when streaming through the BFF proxy becomes needed.

## Changelog

| Datum | Wijziging |
|---|---|
| 2026-04-19 | F-038 documented + SEC-023 scope bepaald. Implementation start nu. |
| 2026-04-19 | SEC-023 LIVE on main (commits 9ea58b73 + 0bcbe579). Post-deploy CSRF review: full protection already in place via SessionMiddleware X-CSRF-Token enforcement + SameSite=Lax cookie. End-to-end browser verification outstanding (Task #31). |
