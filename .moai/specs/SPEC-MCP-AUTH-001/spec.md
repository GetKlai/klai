---
id: SPEC-MCP-AUTH-001
version: "0.2.1"
status: draft
created: 2026-05-06
updated: 2026-05-06
author: Mark Vletter
priority: high
issue_number: null
related:
  - SPEC-SEC-IDENTITY-ASSERT-001 (identity verify pattern, portal-api /internal/identity/verify)
  - SPEC-SEC-INTERNAL-001 (internal-secret enforcement, REQ-9.5 fail-closed-on-empty)
  - SPEC-MCP-TRANSPORT-001 (geplande transport-hardening; future sibling)
  - SPEC-AUTH-008 (FRONTEND_URL / Zitadel login domain — context voor consent-UI hosting)
  - SPEC-SEC-HYGIENE-001 (REQ-20 callback URL allowlist; analoog patroon voor redirect_uri)
---

# SPEC-MCP-AUTH-001: Multi-client MCP authentication via OAuth 2.1 + portal-issued bearer tokens

## HISTORY

| Datum | Versie | Wijziging |
|-------|--------|-----------|
| 2026-05-06 | 0.1.0 | Initial draft. Pad A (UI-knop "Generate token") + Pad B (OAuth) als opties; Pad A primair om snel te kunnen leveren. |
| 2026-05-06 | 0.2.0 | Pad A volledig geschrapt — OAuth-only. Geen "Generate token" UI. Settings → Integrations wordt revoke-only "Connected applications" lijst. Token-creation gaat exclusief via OAuth flow met DCR (RFC 7591). Hierdoor vervalt: `POST /api/me/mcp-tokens` create-endpoint, frontend create-dialog, AC-1. Erbij komt: portal-api OAuth 2.1 authorization-server-laag (`/oauth/authorize`, `/oauth/token`, `/oauth/register`, `.well-known/oauth-authorization-server`), RFC 9728 PRM-endpoint op MCP, refresh-token rotation, server-rendered consent-UI. Reden voor de switch: power-user-token-flow is in deze fase een YAGNI-feature; alle target-clients (Claude Desktop, Cursor, ChatGPT) doen DCR-OAuth en geen handmatige token-paste. Latere SPEC kan een "personal access token" surface toevoegen zonder deze SPEC te raken. |
| 2026-05-06 | 0.2.1 | Twee open questions definitief vastgelegd: (Q1 → A10) `application_type` strikt valideren tegen redirect_uri-shape — native = `localhost`/`127.0.0.1` only, web = pre-approved HTTPS hostnames only. Mismatch = HTTP 400 op DCR. (Q2 → A11) Consent-page is altijd expliciet voor v0.2.1 — geen silent re-authorization, ook niet als dezelfde client_id eerder is approved door dezelfde user. Latere SPEC kan silent re-auth toevoegen zodra fijnmaziger scopes (per-tool of per-KB) worden geïntroduceerd waarbij re-auth-vraag vaker langs zou komen. Bijbehorende REQ-13a toegevoegd voor application_type-validatie. |

---

## Summary

De `klai-knowledge-mcp` server is vandaag bereikbaar voor exact één client: LibreChat, via Docker-internal hostname met een gedeeld `X-Internal-Secret` plus door LibreChat-geïnjecteerde identity-headers. Voor third-party MCP-clients (Claude Desktop, Cursor, ChatGPT custom connectors) zijn alle drie de externe voorwaarden gesloten: geen Caddy-route, geen mogelijkheid om het shared secret te kennen, geen mogelijkheid om identity-headers te vervalsen.

Deze SPEC voegt een tweede authenticatiepad toe naast LibreChat:

1. **Knowledge-mcp** wordt OAuth 2.1 *resource server* (RFC 9728 protected-resource-metadata, `WWW-Authenticate` challenge op 401).
2. **Portal-api** wordt OAuth 2.1 *authorization server* (RFC 8414 metadata, `/oauth/authorize` consent-flow, `/oauth/token` exchange, RFC 7591 dynamic client registration). Hergebruikt de bestaande Zitadel-gebaseerde portal-sessie voor user-authentication — Zitadel ziet geen MCP-clients.
3. **Bearer-tokens** zijn opaque (`klai_mcp_<base64url>`), DB-backed, Redis-gecached. Validatie via `POST /internal/mcp-token/verify` op portal-api — exact het patroon van het bestaande `/internal/identity/verify` endpoint.
4. **LibreChat-pad** blijft byte-voor-byte ongewijzigd. Knowledge-mcp main.py krijgt een dispatcher die routeert op `Authorization: Bearer klai_mcp_*`-prefix.

**Eindgebruiker-flow:** in Claude Desktop "Add custom connector" → URL `https://mcp.getklai.com/mcp` → Claude doet 401-discovery → opent `https://my.getklai.com/oauth/authorize?...` in browser → user is al ingelogd via Zitadel → ziet consent-page met Klai-logo + "Allow [Claude Desktop] to access your knowledge?" → klikt approve → redirect terug → Claude wisselt code in voor access+refresh tokens → tools werken. Geen handmatige paste-flow.

**Settings → Integrations** wordt een revoke-only "Connected applications" lijst: client_name + last_used_at + revoke-knop. Geen creation UI.

---

## EARS Requirements

### Ubiquitous (altijd actief)

**REQ-1.** De portal-api **shall** een tabel `portal_mcp_tokens` onderhouden (RLS Category-D) met kolommen: `id`, `org_id` (FK→`portal_orgs`), `user_id` (FK→`portal_users`), `client_id` (FK→`portal_oauth_clients`), `access_token_hash` (BYTEA, SHA-256), `refresh_token_hash` (BYTEA, SHA-256, NULLABLE), `scopes` (JSONB array), `created_at`, `last_used_at` (NULLABLE), `expires_at`, `refresh_expires_at` (NULLABLE), `revoked_at` (NULLABLE).

**REQ-2.** De portal-api **shall** een tabel `portal_oauth_clients` onderhouden (RLS Category-B: SELECT public, mutaties scoped) met kolommen: `id`, `client_id` (TEXT, unique), `client_name` (TEXT), `redirect_uris` (JSONB array), `grant_types` (JSONB array), `token_endpoint_auth_method` (TEXT, default `none`), `application_type` (TEXT, `native`|`web`), `created_at`, `created_by_ip` (INET, NULLABLE — voor DCR audit).

**REQ-3.** Beide tabellen **shall** opgenomen zijn in `RLS_DML_TABLES` in `app/core/rls_guard.py` en in `scripts/rls-smoke-test.sql`. Patroon volgt `portal-security.md` § "Adding a new RLS-enabled table".

**REQ-4.** Access tokens **shall** het formaat `klai_mcp_<base64url>` hebben, refresh tokens `klai_mcp_rt_<base64url>`. Suffix is exact 43 ASCII-karakters base64url uit 32 random bytes (`secrets.token_urlsafe(32)`). Prefixes zijn mechanisch bedoeld voor secret-scanners (GitGuardian) en voor de knowledge-mcp dispatcher branch (REQ-15).

**REQ-5.** De portal-api **shall** raw tokens éénmalig retourneren bij issuance (response op `/oauth/token`) en daarna alleen hashes opslaan. Geen API-pad mag de raw token na issuance nog terugleveren.

**REQ-6.** De portal-api **shall** SHA-256 hash gebruiken als lookup-sleutel; hash-vergelijking via `hmac.compare_digest`. Bestaande ast-grep regels `no-secret-{eq,neq,eq-rhs}-compare` blijven groen.

**REQ-7.** De portal-api **shall** `GET /.well-known/oauth-authorization-server` exposen volgens RFC 8414, met velden: `issuer=https://my.getklai.com`, `authorization_endpoint`, `token_endpoint`, `registration_endpoint`, `jwks_uri=null` (we gebruiken opaque tokens), `scopes_supported=["mcp:knowledge"]`, `response_types_supported=["code"]`, `grant_types_supported=["authorization_code","refresh_token"]`, `code_challenge_methods_supported=["S256"]`, `token_endpoint_auth_methods_supported=["none"]` (public clients met PKCE).

**REQ-8.** De knowledge-mcp **shall** `GET /.well-known/oauth-protected-resource` exposen volgens RFC 9728, met velden: `resource=https://mcp.getklai.com`, `authorization_servers=["https://my.getklai.com"]`, `scopes_supported=["mcp:knowledge"]`, `bearer_methods_supported=["header"]`.

**REQ-9.** De portal-api **shall** een Redis-cache (TTL = 60 seconden) onderhouden voor `(access_token_hash → verify_result)`. Cache-miss = DB lookup; cache-unavailable = fail-closed met HTTP 503 en `reason="cache_unavailable"`. Patroon spiegelt `/internal/identity/verify` REQ-1.6.

**REQ-10.** De knowledge-mcp **shall** alle 401-responses voorzien van `WWW-Authenticate: Bearer realm="klai-mcp", resource_metadata="https://mcp.getklai.com/.well-known/oauth-protected-resource", scope="mcp:knowledge"`. Geen `error_description` (info-leak prevention).

**REQ-11.** De knowledge-mcp **shall** het bestaande LibreChat-pad (`X-Internal-Secret` + `X-User-ID/X-Org-ID/X-Org-Slug` + optionele Zitadel-JWT) byte-voor-byte ongewijzigd laten draaien. Tools-niveau is een dispatcher (REQ-15).

**REQ-12.** Beide authenticatiepaden **shall** uitmonden in dezelfde dataclass `_VerifiedIdentity` met `user_id`, `org_id`, `org_slug` voordat enige tool-handler upstream-calls doet. De bestaande tool-bodies (`save_personal_knowledge`, `save_org_knowledge`, `save_to_docs`) raken niet aan in upstream-call-logic — alleen identity-extractie aan de top wordt vervangen door één dispatcher-call.

**REQ-13.** De portal-api **shall** PKCE S256 verplicht stellen voor authorization-code flow. Een `/oauth/authorize` request zonder geldige `code_challenge` + `code_challenge_method=S256` **shall** falen met HTTP 400 `error="invalid_request"`.

**REQ-13a.** De portal-api **shall** `application_type` strikt valideren tegen `redirect_uris` tijdens DCR (`POST /oauth/register`):
- `application_type="native"`: alle `redirect_uris` MOETEN matchen op `http://localhost:*` of `http://127.0.0.1:*` (REQ-20-allowlist subset voor native).
- `application_type="web"`: alle `redirect_uris` MOETEN HTTPS zijn én matchen op de pre-approved hostname-allowlist (REQ-20-allowlist subset voor web: `*.openai.com`, `chat.openai.com`, `claude.ai`).
- `application_type` ontbreekt = HTTP 400 `error="invalid_request"`. Geen default-naar-web (OIDC default) en geen default-naar-native — expliciet vereist om mismatch onmogelijk te maken.

Mismatch (bv. `application_type="native"` met een HTTPS-redirect_uri, of `application_type="web"` met een localhost-redirect_uri) **shall** falen met HTTP 400 `error="invalid_redirect_uri"`. Een DCR-request mag niet beide categorieën mixen in één `redirect_uris` array.

**REQ-13b.** De portal-api **shall** elke autorisatie expliciet vragen op de consent-UI (`GET /oauth/authorize`), ook wanneer dezelfde `(client_id, user_id)`-combinatie eerder een token kreeg. Geen silent re-authorization in v0.2.1. Een gebruiker die Claude Desktop in januari approved, krijgt in juni opnieuw de consent-page wanneer Claude Desktop een nieuwe authorization-flow start (bv. na revoke of expiry van het refresh-token).

**REQ-14.** De portal-api **shall** RFC 8707 resource-indicators ondersteunen: het `resource` parameter in `/oauth/authorize` en `/oauth/token` requests **shall** `https://mcp.getklai.com` zijn (de canonieke knowledge-mcp URI). Tokens worden gebonden aan deze resource. Tokens uitgegeven voor andere resources zijn niet geldig op knowledge-mcp.

### State-driven (conditional)

**REQ-15.** **While** een request binnenkomt op knowledge-mcp met `Authorization: Bearer klai_mcp_<...>` (access token prefix, NIET refresh-token prefix `klai_mcp_rt_`), **shall** de dispatcher het OAuth-token-pad activeren — anders het bestaande LibreChat-pad. De dispatcher branch:
```
if auth_header.startswith("Bearer klai_mcp_") and not auth_header.startswith("Bearer klai_mcp_rt_"):
    use OAuth token path
else:
    use LibreChat internal-secret path
```

**REQ-16.** **While** een token een `revoked_at` waarde heeft, **shall** de portal-api alle verify-calls voor die token afwijzen met HTTP 403 `reason="token_revoked"`. Cache-invalidatie volgt REQ-22 (event-driven).

**REQ-17.** **While** een access token een `expires_at` waarde heeft die in het verleden ligt, **shall** de portal-api alle verify-calls afwijzen met HTTP 403 `reason="token_expired"`. Default `expires_at = created_at + INTERVAL '30 days'`. Refresh token default `refresh_expires_at = created_at + INTERVAL '90 days'`.

**REQ-18.** **While** de bron-user (`portal_mcp_tokens.user_id`) niet meer een actieve `portal_users`-rij heeft, **shall** de portal-api alle verify-calls voor tokens van die user afwijzen met HTTP 403 `reason="user_inactive"`.

**REQ-19.** **While** een org in `provisioning_status='deprovisioning'` of `'deprovisioned'` is, **shall** de portal-api alle verify-calls voor tokens van die org afwijzen met HTTP 403 `reason="org_deprovisioning"`. Spiegelt het bestaande `_get_caller_org`-pattern.

**REQ-20.** **While** een DCR-request (`POST /oauth/register`) een `redirect_uri` claimt die niet matcht aan de allowlist (REQ-26), **shall** de portal-api de registratie afwijzen met HTTP 400 `error="invalid_redirect_uri"`. Allowlist:
- `http://localhost:*` (any port — Claude Desktop, Cursor, native MCP clients)
- `http://127.0.0.1:*`
- `https://*.openai.com/*`, `https://chat.openai.com/*` (ChatGPT custom connectors)
- `https://claude.ai/*` (Claude.ai custom connectors)
- Verder: alleen pre-registered clients (admin-only via portal-admin endpoint, out of scope voor v0.2.0).

**REQ-21.** **While** een user op `/oauth/authorize` aankomt zonder geldige Zitadel-portal-sessie (geen BFF cookie of expired), **shall** de portal-api redirecten naar `/login?return_to=/oauth/authorize?<original-query>`. Na succesvolle Zitadel-login keert de browser terug op `/oauth/authorize` en gaat de consent-flow door.

### Event-driven (trigger-based)

**REQ-22.** **When** een token wordt uitgegeven (succesvolle `/oauth/token` response), gerevoke'd (`DELETE /api/me/mcp-tokens/{id}`), of refreshed (`grant_type=refresh_token`), **shall** de portal-api alle Redis-cache-entries voor de betreffende `access_token_hash`(es) invalideren binnen 1 seconde. Voor expiry: cache TTL = 60s is de fallback-bound; geen achtergrondjob nodig.

**REQ-23.** **When** een succesvolle verify-call plaatsvindt, **shall** de portal-api `last_used_at = NOW()` updaten op de bijhorende `portal_mcp_tokens`-rij. Update is asynchroon (fire-and-forget achter `asyncio.create_task` met independent `AsyncSessionLocal`-sessie). Update mag maximaal 1× per minuut per token plaatsvinden (rate-limit via Redis-key `mcp_last_used:<token_hash>`).

**REQ-24.** **When** een token wordt uitgegeven, **shall** de portal-api een `portal_audit_log`-rij toevoegen met `event_type='mcp_token.issued'`, `properties={"token_id":..., "client_id":..., "client_name":..., "scopes":..., "expires_at":...}` (geen raw token of hash), en een `product_events`-rij voor analytics.

**REQ-25.** **When** een token wordt gerevoke'd of vervangen via refresh-rotatie, **shall** de portal-api een `portal_audit_log`-rij toevoegen met `event_type='mcp_token.revoked'` resp. `event_type='mcp_token.refreshed'`. Tool-call events (per request van Claude → MCP) worden NIET per call gelogd; `last_used_at` is voldoende.

**REQ-26.** **When** een refresh-token wordt gebruikt op `/oauth/token`, **shall** de portal-api **rotation** afdwingen: het oude refresh-token wordt direct gerevoke'd, een nieuw refresh-token wordt uitgegeven (RFC 6819 § 5.2.2.3). Hergebruik van een al-gerotee'rd refresh-token (replay) **shall** alle tokens van die client+user revoke'n als security-respons (RFC 6749 § 10.4 + OAuth 2.1 § 4.3.1).

**REQ-27.** **When** een DCR-request slaagt, **shall** de portal-api een `portal_audit_log`-rij toevoegen met `event_type='oauth_client.registered'`, `properties={"client_id":..., "client_name":..., "redirect_uris":..., "source_ip":...}`. Per-IP rate-limit: max 10 DCR-registraties per uur per IP (Redis-counter).

### Optional Features

**REQ-28.** De portal-api **may** inactive client pruning ondersteunen: `portal_oauth_clients` zonder enige `portal_mcp_tokens`-rij in 180 dagen worden soft-deleted via een achtergrondjob. Out of scope voor v0.2.0; design open voor latere SPEC.

**REQ-29.** De frontend **may** een per-token "in afgelopen 30 dagen niet gebruikt" badge tonen in de Settings → Integrations pagina, gebaseerd op `last_used_at < NOW() - INTERVAL '30 days'`. Geen automatische revoke; alleen UI-hint.

---

## Architecture decisions

### A1. Token-formaat: opaque vs JWT

**Keuze: opaque** (`klai_mcp_<base64url>`). Drie redenen:

1. **Revocation-eenvoud.** Een gerevoke'de JWT vereist een blacklist of korte TTL met refresh-flow. Opaque tokens revoke je door één DB-rij te updaten (`revoked_at = NOW()`) plus cache-invalidatie.
2. **Geen JWKS-issuer-rol voor portal-api.** Portal-api heeft een PyJWKClient voor *Zitadel* JWTs — een eigen JWT-issuer-rol toevoegen betekent eigen keypair, key-rotation runbook, JWKS-endpoint. Voor low-volume internal traffic met 60s cache TTL is DB-lookup voldoende.
3. **OAuth 2.1 staat opaque expliciet toe.** RFC 6749 + OAuth 2.1 spec. De `/oauth/token` response retourneert `token_type: "Bearer"` ongeacht of het token JWT of opaque is.

### A2. Client registration: DCR (RFC 7591) als primair pad

**Keuze: anonymous DCR met strikte redirect_uri-allowlist** (REQ-20). Drie alternatieven afgewogen:

| Optie | Pros | Cons |
|---|---|---|
| **Pre-registered only** | Volledig controleerbaar, geen SSRF-risico | Werkt niet met Claude Desktop's "Add custom connector" zonder advanced settings; nieuwe clients = ops-werk |
| **DCR (RFC 7591)** | Werkt out-of-the-box met Claude/Cursor/ChatGPT | Confused-deputy risico; spam-gevoelig; vereist redirect_uri-allowlist |
| **Client ID Metadata Documents** | Portabel; geen registration-state | SSRF-risico (portal moet client_id-URL fetchen); minder mature ecosystem; Zitadel ondersteunt het niet — past wel bij portal als eigen AS |

**Reden voor DCR:** alle target-clients voor v0.2.0 (Claude Desktop, Cursor, ChatGPT) doen DCR als primaire flow. De redirect_uri-allowlist neutraliseert het meeste van het confused-deputy risico — alleen `localhost`/`127.0.0.1` + bekende SaaS-domeinen worden geaccepteerd. Localhost-impersonatie risico (MCP spec § "Localhost Redirect URI Risks") wordt gemitigeerd door het consent-screen prominent de `client_name` + `redirect_uri` te tonen.

**Geen Client ID Metadata Documents in v0.2.0.** SSRF-risico op portal-api zou een tweede SSRF-allowlist vereisen (zie `docker-socket-proxy.md` REQ-5 — portal-api staat NIET op de no-fly-list maar fetched vandaag geen user-URLs). Latere SPEC kan CIMD toevoegen wanneer de MCP-spec ecosystem stabieler is.

### A3. Cache-locatie: Redis vs in-process

**Keuze: Redis** (zelfde Redis-cluster die `identity_verify_cache` gebruikt). Reden: knowledge-mcp draait single-instance vandaag, maar wordt potentieel multi-instance. In-process cache leidt tot inconsistente revocation. Redis = single source of truth.

### A4. RLS-categorieën voor nieuwe tabellen

- **`portal_mcp_tokens` = Category D (strict).** Alle access-paden hebben tenant-context geset (auth'd `/api/me/mcp-tokens` lijst+revoke endpoints, en het verify-endpoint draait in service-context na claim-verificatie).
- **`portal_oauth_clients` = Category B.** SELECT public (DCR-flow heeft geen tenant-context — clients zijn org-overstijgend), mutaties scoped (toekomstige admin-CRUD via portal-admin endpoint).

### A5. Dispatcher in knowledge-mcp main.py

**Keuze: explicit branch op header-prefix** (REQ-15). Het bestaande LibreChat-pad accepteert vandaag óók een optionele `Authorization: Bearer <Zitadel-JWT>`. Een naïeve dispatcher op "is er een Authorization header?" zou Zitadel-JWTs misrouten. Branch op `klai_mcp_`-prefix is mechanisch onmogelijk te conflicten — de prefix is reserved.

### A6. Caddy-exposure en DNS-rebinding-protection

**Keuze: nieuwe Caddy-routes** voor `mcp.getklai.com` → `klai-knowledge-mcp:8080`, en in FastMCP `enable_dns_rebinding_protection=True`. De huidige @MX:WARN comment in main.py wordt resolved. Caddyfile-comment ("klai-knowledge-mcp not internet-reachable") wordt bijgewerkt in dezelfde PR — anders is dat een review-discrepantie.

### A7. Consent-UI: server-rendered minimal HTML

**Keuze: server-rendered HTML response op `GET /oauth/authorize`**, geen aparte frontend SPA-route. Vier redenen:

1. OAuth consent is een browser-intermediate, niet een primary UX surface. Volledige SPA-routing is overkill.
2. Hergebruik van bestaande Zitadel-portal-sessie (BFF cookies) — geen aparte auth voor de consent-page.
3. Geen i18n-tooling nodig — de page heeft 3 strings ("Allow [client_name] to access your Klai knowledge?", "Approve", "Deny"). Mag in fase 2 ge-i18n'd worden via Paraglide.
4. POST `/oauth/authorize` (form-submit van approve/deny) handled de redirect naar de client.

Layout volgt `klai-portal/CLAUDE.md` design-conventies (form-pages `max-w-lg`, components/ui/, color tokens). Page is een Jinja2 template of FastAPI HTMLResponse.

### A8. Refresh-token rotation

**Keuze: rotation met replay-detectie** (REQ-26). RFC 6819 + OAuth 2.1 § 4.3.1 require dit voor public clients. Implementatie: refresh-token gebruik markeert oude token `revoked_at = NOW()`, geeft nieuw refresh-token uit. Replay-detectie: poging tot gebruik van een al-gerevoke'd refresh-token revoket alle tokens van die `(client_id, user_id)`-paar — vermijdt dat een attacker met een gestolen refresh-token onderwater kan blijven werken.

### A9. Zitadel blijft volledig buiten OAuth-flow

**Keuze: portal-api is de OAuth authorization server, Zitadel is alleen identity provider voor portal-login.** Zitadel weet niets van MCP-clients of OAuth-issued MCP-tokens. Reden: Zitadel ondersteunt geen RFC 7591 DCR (issue #9810 open zonder roadmap), en de natural separation is "Zitadel = wie ben je", "portal = wat mag je extern delen". Beide flows zijn industry-standard voor B2B SaaS → AI-tool integraties.

### A10. Application_type strikt scheiden van redirect_uri-shape

**Keuze: `application_type` is verplicht bij DCR en moet strikt matchen op de redirect_uri-vorm.** Een DCR-aanvraag met `application_type="native"` mag uitsluitend `localhost`/`127.0.0.1` redirect_uris dragen; `application_type="web"` mag uitsluitend HTTPS-redirect_uris naar pre-approved hostnames dragen (REQ-13a).

**Reden:** een native app die naar een publieke HTTPS-URL wijst is verdacht (mogelijk phishing-aanvulflow waarbij de attacker de authorization code op zijn eigen domein opvangt). Een web app die naar `localhost` wijst is verdacht (mogelijk attacker die op de eigen-machine van het slachtoffer luistert). De OIDC-default (`application_type="web"` als hij ontbreekt) is een bekend voetje-bal — de spec zegt "default to web", wat in DCR-flows betekent dat een client die `application_type` vergeet stilletjes als web-app wordt behandeld. Wij vereisen het veld expliciet om die fail-open-mode dicht te timmeren.

**Tradeoff:** legitieme MCP-clients moeten `application_type` correct invullen. Claude Desktop, Cursor en ChatGPT custom connectors doen dit standaard goed; het is een 0-cost-eis voor target-clients en een sterke afweer voor edge-cases.

### A11. Consent altijd expliciet (geen silent re-authorization)

**Keuze: elke `/oauth/authorize`-request leidt tot de consent-UI, ook bij eerder approved `(client_id, user_id)`-paar.** Geen silent re-auth in v0.2.1.

**Reden:** met scope `mcp:knowledge` als enige scope is een re-auth-vraag relatief zeldzaam (alleen na refresh-token expiry of revoke — typisch elke 90 dagen). Eén klik op die momenten is laag-friction en geeft de gebruiker een handvest om "wacht, ik gebruik Claude Desktop niet meer"-momenten te vangen. Silent re-auth zou pas waarde toevoegen bij fijnmazigere scopes (per-tool, per-KB) waar re-auth vaker langs komt — die scope-uitbreiding is out-of-scope voor v0.2.1 en zou bovendien een correct doordachte silent-policy vereisen (welk scope-subset is silent OK, welk niet). Latere SPEC behandelt beide samen.

**Tradeoff:** marginaal hoger UX-friction op refresh-token-expiry. Acceptabel omdat de frequentie laag is en de transparantie hoog.

---

## Out of scope

1. **Personal access tokens via UI-knop.** Geen "Generate token" dialog. Alle token-issuance via OAuth-flow. Latere SPEC kan een PAT-surface toevoegen voor power users die scripts buiten een MCP-client willen draaien.

2. **LibreChat migratie.** LibreChat blijft op `X-Internal-Secret` + identity-headers. Dispatcher in knowledge-mcp ondersteunt beide paden simultaan.

3. **Per-tool of per-KB scopes.** Vandaag één scope `mcp:knowledge`. Token erft volledige read+write access van issuer-user. Granulariteit per tool/KB komt in latere scope-design SPEC.

4. **Client ID Metadata Documents.** Latere SPEC.

5. **Pre-registered third-party clients (admin-CRUD endpoint).** DCR is voldoende voor v0.2.0. Pre-registered (admin handmatig) komt zodra een enterprise-klant die niet door DCR-allowlist past.

6. **OIDC-style id_tokens / userinfo-endpoint.** We zijn pure OAuth 2.1 — geen id_tokens, geen `/userinfo`. Een MCP-client weet alleen "ik heb een access token dat tegen `mcp.getklai.com` werkt"; user-identity is impliciet via de tokens-bound user_id.

7. **Multi-resource tokens.** Een token is gebonden aan exact één `resource=https://mcp.getklai.com`. Latere SPEC kan andere resources toevoegen (e.g. `retrieval-api.getklai.com`).

8. **Knowledge MCP transport hardening (SPEC-MCP-TRANSPORT-001).** Aparte sibling SPEC. Deze SPEC raakt het minimum (REQ van A6).

---

## Implementation plan (Phases)

### Fase 1 — DB foundation (portal-api migrations)

1. Migration: `portal_mcp_tokens` + `portal_oauth_clients` tabellen + RLS policies (Cat D resp. Cat B) via `post_deploy_<rev>.sql` (klai-superuser owns RLS-tables).
2. SQLAlchemy models in `app/models/portal.py`.
3. RLS_DML_TABLES + rls-smoke-test entries.

### Fase 2 — Token issuance & verify (portal-api OAuth surface)

1. Service-layer `app/services/mcp_oauth.py`:
   - `register_client(redirect_uris, client_name, application_type, source_ip) → ClientCredentials`
   - `start_authorization(client_id, code_challenge, ...) → authorization_request_id`
   - `complete_authorization(request_id, user_id, org_id, decision) → authorization_code`
   - `exchange_code(code, code_verifier) → AccessTokenResponse`
   - `refresh_access_token(refresh_token) → AccessTokenResponse` (met rotation)
   - `verify_access_token(token) → VerifyResult` (Redis-cached)
   - `revoke_token(token_id, user_id, org_id) → None`
2. Pydantic schemas voor alle request/response shapes.
3. Endpoints:
   - `GET /.well-known/oauth-authorization-server` (RFC 8414)
   - `POST /oauth/register` (RFC 7591 DCR)
   - `GET /oauth/authorize` (consent UI render)
   - `POST /oauth/authorize` (approve/deny submit)
   - `POST /oauth/token` (code-exchange + refresh)
   - `POST /internal/mcp-token/verify` (called by knowledge-mcp; `_require_internal_token` guard)
   - `GET /api/me/mcp-tokens` (list user's connected applications)
   - `DELETE /api/me/mcp-tokens/{id}` (revoke)
4. Audit + product_events emits.
5. Tests: 85%+ coverage; OAuth-flow integration tests; PKCE-failure tests; redirect_uri allowlist tests; refresh-rotation replay-detection tests.

### Fase 3 — knowledge-mcp resource server

1. Nieuw bestand `klai-knowledge-mcp/auth.py` met `verify_via_oauth_token(raw_token: str) → _VerifiedIdentity`.
2. main.py refactor: `_identify_request(ctx) → _VerifiedIdentity` als single entry point. De bestaande `_validate_incoming_secret` + `_get_claimed_identity` + `_verify_identity` flow wordt één van de twee branches (REQ-15).
3. `GET /.well-known/oauth-protected-resource` endpoint (REQ-8).
4. WWW-Authenticate header op 401's (REQ-10).
5. Tests: tools blijven werken via LibreChat-pad (regression); nieuwe tests voor OAuth-token-pad (success, revoked, expired, invalid format, wrong audience, unknown token).

### Fase 4 — Frontend "Connected applications"

1. Route `/settings/integrations` in `klai-portal/frontend/src/routes/settings/integrations/`.
2. Components: `ConnectedAppsList` (rendert tokens met `client_name`, `last_used_at`, `expires_at`, revoke-knop), `RevokeConfirmDialog`.
3. **Geen create-flow.** Helpfull empty-state copy: "Connect Klai to Claude Desktop, Cursor, or ChatGPT by adding `https://mcp.getklai.com/mcp` as a custom connector. You will be redirected here to approve."
4. Paraglide strings.
5. E2E test (Playwright): start van een mock-OAuth-flow → consent approve → token in lijst → revoke → token weg uit lijst.

### Fase 5 — Caddy exposure + transport hardening

1. Caddy-routes voor `mcp.getklai.com` → `klai-knowledge-mcp:8080`.
2. FastMCP `enable_dns_rebinding_protection=True` in `klai-knowledge-mcp/main.py`.
3. Update @MX:WARN comment (resolved).
4. Update Caddyfile comment "klai-knowledge-mcp not internet-reachable" → reflecteert nieuwe situatie.
5. SSRF-isolation smoke-test: knowledge-mcp blijft op klai-net, blijft niet op socket-proxy network (`docker-socket-proxy.md` REQ-5).

### Fase 6 — End-to-end Claude Desktop verificatie

1. Manual test: Claude Desktop "Add custom connector" → URL `https://mcp.getklai.com/mcp` → DCR success → consent → tools werken.
2. Idem voor Cursor.
3. Idem voor ChatGPT custom connectors (als beschikbaar in 2026-Q2).
4. Failure-modes: revoke-tijdens-actieve-sessie, refresh-token-replay, redirect_uri-mismatch.

---

## Acceptance criteria

1. **AC-1.** Een user logged in op `my.getklai.com` voegt in Claude Desktop `https://mcp.getklai.com/mcp` toe als custom connector. Claude initieert DCR, opent browser op portal-consent-page, user ziet `client_name="Claude Desktop"` + redirect_uri prominent, klikt Approve, Claude wisselt code in voor tokens, en kan tools draaien op de juiste org-data (RLS-isolation bewezen via cross-tenant test).

2. **AC-2.** In `https://my.getklai.com/settings/integrations` toont de "Connected applications" lijst de Claude-Desktop-koppeling met `client_name`, `last_used_at`, en revoke-knop. Geen create-flow zichtbaar.

3. **AC-3.** Token-revoke via UI invalideert de cache binnen 1 seconde. Een tweede tool-call van Claude met dezelfde access token retourneert HTTP 401 met `WWW-Authenticate` header. Claude doet automatic refresh-attempt, krijgt HTTP 400 `error="invalid_grant"`, en signaleert disconnect.

4. **AC-4.** LibreChat blijft volledig functioneel: `X-Internal-Secret` + identity-headers → tools werken zoals voor de SPEC. Geen regressie in `klai-knowledge-mcp/tests/test_sec_internal_001.py` of soortgelijke tests.

5. **AC-5.** Refresh-token rotation: een succesvolle `/oauth/token` met `grant_type=refresh_token` retourneert nieuwe access+refresh tokens en revokes de oude refresh token. Replay van het oude refresh-token revoket alle tokens van die `(client_id, user_id)`-paar.

6. **AC-6.** Een token voor een gerevoke'de user (deleted, deactivated) returnt HTTP 403 `reason="user_inactive"` ook als `revoked_at IS NULL` op de token-rij.

7. **AC-7.** Een token voor een org in `provisioning_status='deprovisioning'` returnt HTTP 403 `reason="org_deprovisioning"`.

8. **AC-8.** Cache-unavailable (Redis down) returnt HTTP 503 `reason="cache_unavailable"` op zowel `/internal/identity/verify` (regressie-check) als `/internal/mcp-token/verify`.

9. **AC-9.** RLS-smoke-test bevat checks op cross-tenant isolation voor `portal_mcp_tokens`. RLS_DML_TABLES bevat beide nieuwe tabellen.

10. **AC-10.** `mcp.getklai.com` is bereikbaar via Caddy met `enable_dns_rebinding_protection=True`. `GET https://mcp.getklai.com/.well-known/oauth-protected-resource` retourneert valid RFC 9728 JSON. `GET https://my.getklai.com/.well-known/oauth-authorization-server` retourneert valid RFC 8414 JSON.

11. **AC-11.** DCR met een redirect_uri buiten de allowlist (e.g. `https://attacker.example.com/callback`) faalt met HTTP 400 `error="invalid_redirect_uri"`. DCR met `http://localhost:54321/oauth/callback` slaagt.

12. **AC-12.** Per-IP rate-limit: 11e DCR-request binnen een uur vanaf hetzelfde IP retourneert HTTP 429.

13. **AC-13.** ast-grep regels `no-secret-{eq,neq,eq-rhs}-compare` blijven groen — alle token-vergelijkingen via `hmac.compare_digest`.

14. **AC-14.** PKCE niet-S256 of ontbrekend faalt met HTTP 400 `error="invalid_request"`.

15. **AC-15.** Token uitgegeven met `resource=https://other.example.com` faalt op knowledge-mcp validation met HTTP 401 `reason="audience_mismatch"`.

---

## Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Confused-deputy via DCR (attacker registreert client met victim-uitziende `client_name`) | medium | medium (user wordt gephished tijdens consent) | Consent-page toont prominent `redirect_uri` + `created_by_ip`; clients ouder dan 5 min worden expliciet als "newly registered" gemarkeerd; `client_name` wordt gerendered met text-only (geen HTML/markdown injection) |
| Localhost redirect_uri impersonatie (MCP spec § "Localhost Redirect URI Risks") | medium | medium | Consent-page toont `redirect_uri` hostname expliciet; gebruiker waarschuwen bij `localhost`; toekomstig: per-client name+icon-attestation als spec verder evolueert |
| LibreChat-pad regressie door dispatcher-refactor | low | high | Dispatcher-branch op explicit prefix-match (A5); regression tests draaien beide paden; Fase 3 PR raakt alleen identity-extractie, niet tool-bodies |
| Cross-org token (token van org A geldig op org B's data) | very low | critical | RLS Category-D + verify_token retourneert org_id van token-row; knowledge-mcp set_tenant op verified.org_id vóór elke upstream-call (zelfde patroon als LibreChat-pad vandaag); RFC 8707 audience-binding |
| Refresh-token diefstal van Claude Desktop config-file | medium | high | Refresh-token-rotation (REQ-26): gestolen refresh-token werkt eenmaal, dan trip wire op replay-detectie en revoket alle tokens; user ziet "Reconnect Claude Desktop" prompt; access-token TTL is 30 dagen i.p.v. typische 60 min — tradeoff voor lagere refresh-frequency, accepteerbaar omdat cache-revoke-bound 60s is |
| DCR-spam (botnet registreert 1000+ clients) | medium | low (geen security-impact, wel DB-pollution) | Per-IP rate-limit (REQ-27); pruning van inactive clients (REQ-28, future SPEC); admin-dashboard kan in nood `portal_oauth_clients` truncate'n |
| Migratie raakt running portal-api | low | high | Beide migrations zijn additive (nieuwe tabellen); RLS policies via separate `post_deploy_*.sql` als klai-superuser |
| OAuth-spec evolueert (CIMD, RFC 8707 strict mode) | medium | low | Spec-versie pinned op huidige draft; review na elke Anthropic-MCP-spec-update; major bumps zijn een aparte SPEC |

---

## Open questions

1. **Client_name spoofing.** Voorbeeld: attacker registreert via DCR met `client_name="Klai Official"`. We doen geen string-matching tegen reserved names. Aanbeveling: minimal — log alle DCR's prominent, latere SPEC kan een reserved-names allowlist toevoegen wanneer dit een probleem wordt.

2. **Audit-retention voor `oauth_client.registered`.** Standaard `portal_audit_log` retention (90 dagen?) of langer voor security-relevante events? Aanbeveling: standaard.

3. **Token-name semantiek.** Bij OAuth-flow heeft een token geen user-supplied `name`; het erft `client_name` van de registered client. UI toont dus `client_name` ("Claude Desktop") niet user-name. Geen actie nodig — alleen scherp houden in UI-design.

> v0.2.1 sloot de eerdere Q1 (application_type strict separation) en Q2 (consent silent re-auth) als definitieve architecture decisions A10 + A11 met bijhorende REQ-13a + REQ-13b.

---

## References

- [klai-knowledge-mcp/main.py](../../klai-knowledge-mcp/main.py)
- [klai-portal/backend/app/api/internal.py](../../klai-portal/backend/app/api/internal.py) (`/internal/identity/verify`)
- [klai-libs/identity-assert/](../../klai-libs/identity-assert/)
- [.claude/rules/klai/projects/portal-security.md](../../.claude/rules/klai/projects/portal-security.md) — RLS Category-framework
- [.claude/rules/klai/projects/portal-security-auth.md](../../.claude/rules/klai/projects/portal-security-auth.md) — `secret-fail-closed-on-empty`, `no-secret-eq-compare`, `allowlist-must-enumerate-all-host-classes` (REQ-20 redirect_uri-allowlist case)
- [.claude/rules/klai/projects/portal-backend.md](../../.claude/rules/klai/projects/portal-backend.md) — fire-and-forget writes, Alembic + RLS
- [.claude/rules/klai/platform/docker-socket-proxy.md](../../.claude/rules/klai/platform/docker-socket-proxy.md) — knowledge-mcp no-fly-list
- [.claude/rules/klai/infra/observability.md](../../.claude/rules/klai/infra/observability.md) — `request_id` propagatie
- [MCP Authorization specification (draft)](https://modelcontextprotocol.io/specification/draft/basic/authorization)
- [RFC 7591 — OAuth 2.0 Dynamic Client Registration](https://datatracker.ietf.org/doc/html/rfc7591)
- [RFC 8414 — OAuth 2.0 Authorization Server Metadata](https://datatracker.ietf.org/doc/html/rfc8414)
- [RFC 8707 — Resource Indicators for OAuth 2.0](https://datatracker.ietf.org/doc/html/rfc8707)
- [RFC 9728 — OAuth 2.0 Protected Resource Metadata](https://datatracker.ietf.org/doc/html/rfc9728)
- [RFC 6819 — OAuth 2.0 Threat Model and Security Considerations](https://datatracker.ietf.org/doc/html/rfc6819) (refresh-token rotation)
- [Scalekit migration guide](https://www.scalekit.com/blog/migrating-from-api-keys-to-oauth-mcp-servers)
- [Notion workspace-token deprecation](https://developers.notion.com/changelog/space-level-integrations-will-be-deprecated-soon-migrate-your-oauth-flows)
