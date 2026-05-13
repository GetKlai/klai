# Research — SPEC-MCP-AUTH-001

> Codebase-bevindingen die /moai run als beginpunt gebruikt. Concrete file-paths, line-ranges, en patterns die hergebruikt worden voor de OAuth + token-validatie laag.

## 1. Identity verify pattern (template voor `/internal/mcp-token/verify`)

| Onderdeel | Locatie | Hergebruik |
|---|---|---|
| HTTP handler | `klai-portal/backend/app/api/internal.py:1337-1496` (`verify_identity`) | Kopiëren naar `verify_mcp_token` met andere request body + lookup. Behoud `_require_internal_token(request)` guard, `_audit_internal_call`, structlog event-emit. |
| Request schema | `IdentityVerifyRequest` (line 1256-1273) | Naar `McpTokenVerifyRequest(token: str, caller_service: str)`. |
| Success/Deny schemas | `IdentityVerifySuccess` / `IdentityVerifyDeny` (line 1275-1296) | 1-op-1 hergebruiken — zelfde shape `{verified, user_id, org_id, org_slug, cache_ttl_seconds, evidence}`. |
| Service-layer | `app/services/identity_verifier.py` (`verify_identity_claim`, `KNOWN_CALLER_SERVICES`) | Nieuwe sibling `app/services/mcp_token_verifier.py` met `verify_mcp_token(token, caller_service)`. |
| Cache-layer | `app/services/identity_verify_cache.py` (`cache_verified_decision`, `get_cached_decision`, `CacheUnavailable`) | Sibling `app/services/mcp_token_verify_cache.py` met identieke shape — cache-key `mcp_token_verify:<hash>`, TTL 60s, fail-closed op Redis-down (REQ-9 + AC-8). |
| Hash-helper | `_hash_zitadel_id` (line 1299-1309) — 16-hex SHA-256 prefix | Gebruiken voor structlog-veld `token_hash_prefix` zonder de full hash te lekken. |
| JWKS-resolver | `_get_identity_jwks_resolver` (line 1312-1334) | NIET hergebruiken — opaque tokens, geen JWT-validatie. |
| Internal-secret guard | `_require_internal_token` (line 1378) | Hergebruiken op `/internal/mcp-token/verify`. |

**Test referentie:** `klai-portal/backend/tests/api/test_internal_identity_verify.py` (zoek met grep — zelfde test-shape voor mcp-token: success/cached/db-miss/cache-unavailable/unknown-caller-service/expired/revoked).

## 2. IdentityAsserter library extension

| Onderdeel | Locatie | Plan |
|---|---|---|
| Library | `klai-libs/identity-assert/klai_identity_assert/` | Voeg `mcp_token_client.py` toe met `McpTokenAsserter` class — analoog aan `IdentityAsserter` (line 86 in client.py). |
| Pool | `httpx.AsyncClient` met module-level singleton — patroon uit identity-assert | Hergebruiken; één client per knowledge-mcp proces. |
| LRU-cache | per-process lru_cache fallback voor Redis-miss | Zelfde signature; cache-key = SHA-256 van raw token. |
| Test-fixture | `klai-libs/identity-assert/tests/test_client.py:_mock_portal` | Kopiëren voor `test_mcp_token_client.py`. |

## 3. `/api/me/*` endpoint conventies

`klai-portal/backend/app/api/me.py` bevat de canonical patterns voor user-scoped endpoints:
- `Depends(_get_caller_org)` op elke handler — auto-set tenant context
- `Depends(get_db)` na `_get_caller_org` (volgorde belangrijk vanwege RLS pool-GUC pattern uit `portal-backend.md`)
- Response models in `app/schemas/me/*.py`
- Audit-emit via `app.services.audit.emit_audit` (fire-and-forget, independent session)

Nieuwe endpoints volgen exact dit pattern:
- `GET /api/me/mcp-tokens` — lijst tokens (zonder hashes)
- `DELETE /api/me/mcp-tokens/{id}` — revoke (set `revoked_at`, invalidate Redis cache-entry voor `access_token_hash`)

## 4. RLS migration templates

| Categorie | Template | Wat te kopiëren |
|---|---|---|
| Cat-D strict | `alembic/versions/post_deploy_f0a1b2c3d4e5.sql` | `_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id()` policy-pattern |
| Cat-D strict | `alembic/versions/post_deploy_7e2d3c1a9b8f.sql` | DDL-volgorde: ENABLE RLS → CREATE POLICY → GRANT |
| Alembic stub | `1b8736eb6455_add_rls_phase2_user_tables.py` | Python-stub die alleen DDL voor portal_api-owned objecten aanmaakt; RLS-policies in sibling `post_deploy_*.sql` (klai-superuser owns RLS) |
| RLS guard | `app/core/rls_guard.py::RLS_DML_TABLES` set | Toevoegen: `"portal_mcp_tokens"`. `portal_oauth_clients` is Cat-B — niet in deze set. |
| Smoke-test | `scripts/rls-smoke-test.sql` | Volg het bestaande pattern: cross-tenant SELECT/UPDATE/DELETE moet 0 rows hitten met `_rls_current_org_id()` set op andere org. |

**Cruciale rule (uit `portal-backend.md`):** `op.create_table` voor RLS-tabellen MOET via Alembic (eigenaar = portal_api), maar `ALTER TABLE ENABLE ROW LEVEL SECURITY` + `CREATE POLICY` MOET via klai-superuser via `post_deploy_<rev>.sql`. Tabel-ownership transferren met `ALTER TABLE OWNER TO klai` is een aparte stap in dezelfde post_deploy SQL.

## 5. Caddy upstream pattern

`deploy/caddy/Caddyfile` — zoek naar `app.getklai.com` block (LibreChat) en `my.getklai.com` block (portal). Nieuwe block voor `mcp.getklai.com`:
- `reverse_proxy klai-knowledge-mcp:8080`
- `header /.well-known/* Cache-Control "max-age=300"` (RFC 9728 PRM endpoint mag gecached worden)
- `request_header X-Forwarded-Proto https`
- `log` block voor access-log (consistent met andere services)

**Caddy comment-discrepantie:** huidige `Caddyfile` bevat een comment "klai-knowledge-mcp not internet-reachable" naast de `klai-net` definition — moet bijgewerkt worden in dezelfde PR (anders breekt de `/moai review` consistency-check).

## 6. FastMCP transport security

`klai-knowledge-mcp/main.py:333-349` heeft een `@MX:WARN` met expliciete redenering waarom DNS-rebinding-protection uit staat. Bij internet-exposure flippen we naar `enable_dns_rebinding_protection=True` en de `@MX:WARN` wordt resolved (vervangen door `@MX:ANCHOR` op de FastMCP-init met fan_in-reasoning).

`mcp.server.transport_security.TransportSecuritySettings` ondersteunt extra fields die we moeten activeren bij internet-exposure:
- `enable_dns_rebinding_protection=True`
- `allowed_hosts=["mcp.getklai.com"]` (als beschikbaar in package versie 1.26+)

## 7. OAuth library keuze: `authlib`

Portal-api `pyproject.toml` bevat **geen** OAuth-server library. Twee opties:

| Optie | Pros | Cons |
|---|---|---|
| **`authlib`** (`Authlib==1.4+`) | Mature, FastAPI-compatible via Starlette integration, ingebouwd PKCE+RFC 7591 support, auteur is OAuth-spec-author | +1 dependency; ongeveer 4 nieuwe transitieve packages |
| **Hand-rolled** met `pyjwt`, `cryptography` | Geen deps; volledige controle | Veel test-coverage nodig; hoog security-surface om zelf te onderhouden |

**Aanbeveling: authlib.** Specifiek `authlib.integrations.starlette_client.OAuth` voor het AS-deel + `authlib.oauth2.rfc6749` voor token-flow primitives. Audience-binding (RFC 8707) en PKCE S256 zijn first-class. Gebruik authlib's `AuthorizationServer` als basis — implementeer DCR via `authlib.oauth2.rfc7591.ClientRegistrationEndpoint` extensiepunt.

## 8. Frontend route + design system

- Routes: SvelteKit-style folder structure in `klai-portal/frontend/src/routes/`. Geen bestaande `/settings/integrations` — vers maken volgens `klai-portal/CLAUDE.md` regels (form-pages `max-w-lg`, header `flex items-center justify-between mb-6`, components/ui/, Paraglide voor strings).
- Reference implementation aangeraden: `klai-portal/frontend/src/routes/admin/users/invite.tsx` (genoemd in portal CLAUDE.md).
- Empty-state copy moet bilingual NL/EN via Paraglide messages-bestand `frontend/messages/{nl,en}.json`.

## 9. Test infrastructure

| Service | Locatie | Patterns |
|---|---|---|
| portal-api | `klai-portal/backend/tests/` | `pytest-asyncio`, `httpx.AsyncClient` als `TestClient`, `pytest.fixture` voor sessie-scope DB-rollback. Coverage doel: 85%+. |
| knowledge-mcp | `klai-knowledge-mcp/tests/test_sec_internal_001.py` | `FastMCP.TestClient` patroon — direct tool-invocation met `Context` mock. Reuse `_mock_portal` style voor `mcp_token_verify`. |
| identity-assert | `klai-libs/identity-assert/tests/` | `httpx_mock` voor mock-portal responses. Kopieer voor `test_mcp_token_client.py`. |
| E2E | Playwright tests in `klai-portal/frontend/tests-e2e/` | Reuse storage-state fixture. Voor OAuth-flow: mocking van Zitadel-sessie via `~/.claude/mcp-storageState.json` patroon (uit `lang/testing.md`). |

## 10. SOPS env vars die deze SPEC introduceert

Alle te toevoegen aan `klai-infra/config.sops.env` met `validator-env-parity` check (zie `infra/sops-env.md`):

| Var | Service | Waarde | Doel |
|---|---|---|---|
| `MCP_OAUTH_ISSUER_BASE_URL` | portal-api | `https://my.getklai.com` | issuer-claim in /.well-known metadata |
| `MCP_OAUTH_RESOURCE_URL` | portal-api + knowledge-mcp | `https://mcp.getklai.com` | RFC 8707 audience-binding canonical URI |
| `MCP_OAUTH_TOKEN_TTL_DAYS` | portal-api | `30` | access-token default lifetime |
| `MCP_OAUTH_REFRESH_TTL_DAYS` | portal-api | `90` | refresh-token default lifetime |
| `MCP_OAUTH_DCR_RATE_LIMIT_PER_HOUR` | portal-api | `10` | per-IP DCR throttle |

Pydantic-settings `model_validator(mode="after")` voor alle 5 vars — fail-closed-on-empty volgens `secret-fail-closed-on-empty` rule, ook al zijn ze niet allemaal secrets (consistent dev/staging fail-fast).

## 11. Edge cases & subtle defenses

Onderwerpen waar de implementatie expliciet voor moet plannen:

| Edge case | Mitigatie |
|---|---|
| **CSRF op `POST /oauth/authorize`** | Hidden CSRF-token in consent-form, gevalideerd tegen Redis-key `oauth:auth_request:<request_id>`. Same-Site cookie-policy uit bestaande BFF-config blijft. |
| **Authorization-code replay** | Code is single-use, TTL 60s in Redis (`oauth:auth_code:<code>`). Tweede gebruik = revoke alle bijhorende `(client_id, user_id)` tokens (analoog aan refresh-token replay-detectie REQ-26). |
| **PKCE state binding** | `code_challenge` opgeslagen bij `oauth:auth_request:<request_id>` tijdens consent-flow; bij `/oauth/token` exchange wordt `code_verifier` gehashed met S256 en vergeleken — mismatch = 400 invalid_grant. |
| **Subdomain-wildcard exploit (`attacker.openai.com.evil.com`)** | Geen `*.openai.com` regex; gebruik harde lijst: `chat.openai.com`, `chatgpt.com`, `claude.ai`. Latere SPEC voor wildcard-ondersteuning vereist tldextract-gebaseerde validatie. |
| **DCR-rate-limit per-IP achter Caddy** | `X-Forwarded-For` header alleen vertrouwen als de directe peer-IP in `klai-net` zit; anders falen naar peer-IP. |
| **Localhost port-collision** | `http://localhost:*` matcht alleen `localhost` of `127.0.0.1` als hostname én een willekeurige numerieke port. Gebruik Python `urllib.parse.urlsplit` voor parsing — geen regex. |
| **Concurrent DCR-requests** | `client_id = secrets.token_urlsafe(16)` (128 bits entropie); UNIQUE constraint op `portal_oauth_clients.client_id`; conflict = 500 (heel zeldzaam, geen retry). |
| **Refresh-token theft via Claude Desktop config-file leak** | Refresh-token-rotation (REQ-26) maakt diefstal eenmalig succesvol, daarna replay-detectie revoket alle tokens van het paar. User ziet "Reconnect Claude Desktop" prompt. |
| **`portal_oauth_clients` zwerf-clients** | Pruning na 7 dagen no-token-issued: achtergrond-job soft-deleted onbenutte DCR-clients (REQ-28 — out of scope v0.2.1, planeren als follow-up). |

## 12. Cross-service contracts

Drie contracten die mechanisch consistent moeten zijn (mismatch = silent fail):

1. **Token-prefix `klai_mcp_` vs `klai_mcp_rt_`.** Knowledge-mcp dispatcher (REQ-15) checkt op access-prefix; refresh-tokens worden NOOIT als bearer naar MCP gestuurd. Verify-endpoint accepteert alleen access-token-hashes als input — refresh-tokens hebben aparte endpoint-pad (`/oauth/token` met `grant_type=refresh_token`).

2. **Resource URI casing.** RFC 8707 vereist case-sensitivity bij audience-vergelijking. `MCP_OAUTH_RESOURCE_URL` moet exact `https://mcp.getklai.com` zijn (lowercase, no trailing slash). Knowledge-mcp's PRM-endpoint retourneert exact dezelfde string. Verify-endpoint matcht audience-claim case-sensitief.

3. **Cache TTL = 60s op beide caches.** `identity_verify_cache` en `mcp_token_verify_cache` hebben dezelfde TTL — anders krijg je verschillende fail-closed semantics tijdens een Redis-blip. Beide `Settings.identity_verify_cache_ttl_seconds = 60` (één env-var deelnemen of twee aparte met dezelfde default — zie open question 4).
