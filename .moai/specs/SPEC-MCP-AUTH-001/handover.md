# Handover — SPEC-MCP-AUTH-001 (autonomous nachtrun, 2026-05-06)

> Tijdens deze sessie van een paar uur is het backend-pad van de SPEC
> opgebouwd, gecommit op `feature/mcp-auth-001`, en als PR aangeboden voor
> review. Dit document beschrijft EXACT wat klaar is, wat NIET klaar is,
> en welke operator-actions vereist zijn vóór deploy.

## Status overview

| Fase | Status | Commit |
|---|---|---|
| 0  Plan-fase artefacten (spec.md + research.md + plan.md) | ✅ klaar | `ddb7498d` |
| 1  DB foundation (migration + post_deploy SQL + ORM models + RLS guard) | ✅ klaar | `2e3b56de` |
| 2  Portal-api OAuth surface | ✅ klaar (DCR + token-verify + me-endpoints + well-known + consent-flow + authorization_code grant) | `f82d69a7` + `09c0102d` |
| 3  Knowledge-mcp dispatcher + RFC 9728 PRM | ✅ klaar | `346a86ee` |
| 4  Frontend Connected Applications | ✅ klaar (revoke-only lijst met Paraglide NL+EN) | `09c0102d` |
| 5  Caddyfile route + transport hardening | ✅ klaar | `743c5c7c` |
| 6  E2E verificatie | ⚠️ niet uitvoerbaar in deze sessie zonder staging-deploy + DNS (operator-actions) | — |
| Tests | ✅ 25 unit-tests groen (PKCE + dispatcher + allowlist) | `7bdc65fc` |

## Wat NIET klaar is

### A. ~~`/oauth/authorize` consent-flow~~ — KLAAR ✅ (commit `09c0102d`)

Volledige consent-flow geïmplementeerd: GET rendert HTML-template,
POST verifieert CSRF + verwerkt approve/deny + redirect naar
`redirect_uri?code=&state=`. `authorization_code` grant in `/oauth/token`
werkt met PKCE-verify. Geen Jinja2 dependency — pure Python str-replace
met explicit HTML-escape op alle user-controlled values.

**Service-laag is al volledig klaar:** `app/services/mcp_oauth.py` heeft
`create_auth_request`, `approve_auth_request`, `consume_auth_code`,
`verify_pkce_s256`, `issue_token_pair`. De endpoint hoeft alleen die
helpers te orchestreren.

### B. ~~Frontend Connected Applications page~~ — KLAAR ✅ (commit `09c0102d`)

Route `/app/integrations` in `klai-portal/frontend/src/routes/app/integrations.tsx`.
Lijst van actieve tokens met `client_name`, `application_type`, `created_at`,
`last_used_at`, `expires_at`. Loskoppelen-knop met inline confirm-step
(`confirmingId` state) — geen aparte modal nodig. Empty-state toont
`https://mcp.getklai.com/mcp` als copy-paste-bare URL voor setup.
Paraglide i18n strings volledig in NL + EN.

**Niet gedaan:** Playwright E2E test — niet zinvol zonder staging-deploy
+ working DNS. Mark heeft expliciet gezegd "het kan zijn dat je deze
feature niet e2e kunt testen op die manier" wegens Voys storage-state
afhankelijkheden. Volgende sessie of na staging-deploy.

### C. E2E verificatie (Fase 6)

Niet uitvoerbaar in deze sessie omdat:
- `mcp.getklai.com` heeft nog geen DNS-record (operator action vereist —
  zie hieronder)
- Caddy-config moet ge-deployed naar core-01 (operator action)
- `MCP_OAUTH_ISSUER_BASE_URL` + `MCP_OAUTH_RESOURCE_URL` env-vars moeten
  in SOPS landen (operator action)
- Claude Desktop kan niet vanuit dit AI-process worden aangestuurd

Plan voor Fase 6 verificatie staat in `plan.md` § Fase 6.

### D. Integration-tests (FastAPI TestClient + DB fixtures)

In de sessie heb ik 25 unit-tests toegevoegd voor pure-logic helpers (PKCE,
allowlist, dispatcher). Integration-tests die de full flow doorlopen
(`POST /oauth/register` → `POST /internal/mcp-token/verify` met mocked
DB-state) zijn niet geschreven. CI moet wel de bestaande
`test_sec_internal_001.py` regression-suite blijven draaien voor LibreChat-pad.

## Operator-actions vereist VÓÓR deploy

### Stap 1 — SOPS env-vars toevoegen (klai-infra)

Beide variabelen moeten in `klai-infra/config.sops.env`:

```bash
MCP_OAUTH_ISSUER_BASE_URL=https://my.getklai.com
MCP_OAUTH_RESOURCE_URL=https://mcp.getklai.com
```

Zonder deze variabelen crasht portal-api bij start (fail-closed validator
in `app/core/config.py`). Dat is BY DESIGN — secret-fail-closed-on-empty
rule uit `portal-security-auth.md`.

Optioneel (defaults zijn voldoende):

```bash
MCP_OAUTH_TOKEN_TTL_DAYS=30
MCP_OAUTH_REFRESH_TTL_DAYS=90
MCP_OAUTH_DCR_RATE_LIMIT_PER_HOUR=10
```

### Stap 2 — DNS record voor `mcp.getklai.com`

Hetzner DNS console: voeg `A`-record `mcp.getklai.com` → `65.21.174.162`
(core-01 IP). Propagatie tot 24u. Verifieer met `dig mcp.getklai.com`.

### Stap 3 — Alembic migration uitvoeren

De migration `9f4e2c8a1b7d_add_mcp_oauth_tables.py` is additive (nieuwe
tabellen, geen ALTER op bestaande). Wordt automatisch toegepast bij
portal-api deploy. **Daarna** moet de operator post-deploy SQL handmatig
runnen als `klai`-superuser:

```bash
ssh core-01
docker exec -i klai-core-postgres-1 psql -U klai -d klai \
  < /opt/klai/repo/klai-portal/backend/alembic/versions/post_deploy_9f4e2c8a1b7d.sql
```

Of via de bestaande wrapper-script `apply_post_deploy_sql.sh 9f4e2c8a1b7d`
als die in jullie runbook staat.

**Multi-heads-issue:** alembic heeft 18 unmerged heads in de huidige
codebase. Mijn migration kies `z3a4b5c6d7e8` als parent. Bij merge naar
main moet eventueel een `alembic merge` revision worden gemaakt — zie
`portal-backend.md` § "Alembic stamped past skipped migration".

### Stap 4 — Caddy reload + portal-api deploy

Standaard portal-deploy.sh flow plus Caddy-reload na de Caddyfile-change:

```bash
docker exec klai-core-caddy-1 caddy reload --config /etc/caddy/Caddyfile
```

### Stap 5 — Smoke-test post-deploy

```bash
# 1. /.well-known endpoints reachable
curl -s https://my.getklai.com/.well-known/oauth-authorization-server | jq
curl -s https://mcp.getklai.com/.well-known/oauth-protected-resource | jq

# 2. 401 on unauthenticated MCP request returns WWW-Authenticate
curl -i https://mcp.getklai.com/mcp 2>&1 | grep -i www-authenticate

# 3. DCR rejects bad redirect_uri
curl -X POST https://my.getklai.com/oauth/register \
  -H "Content-Type: application/json" \
  -d '{"client_name":"Test","redirect_uris":["https://attacker.example.com/cb"],"application_type":"web"}'
# Expected: HTTP 400 invalid_redirect_uri

# 4. DCR accepts localhost native
curl -X POST https://my.getklai.com/oauth/register \
  -H "Content-Type: application/json" \
  -d '{"client_name":"Test","redirect_uris":["http://localhost:54321/cb"],"application_type":"native"}'
# Expected: HTTP 201 with client_id field

# 5. LibreChat regression — knowledge-mcp via Docker-internal pad still works
docker exec klai-core-librechat-getklai-1 sh -c \
  'curl -s -H "X-Internal-Secret: $KNOWLEDGE_INGEST_SECRET" \
        -H "X-User-ID: 123" -H "X-Org-ID: 1" -H "X-Org-Slug: getklai" \
        http://klai-knowledge-mcp:8080/mcp/tools/list'
# Expected: tool list returns successfully
```

## Test-resultaten

Lokaal gerund (op de worktree):

| Test-suite | Status | Aantekening |
|---|---|---|
| `klai-portal/backend/tests/test_mcp_oauth_unit.py` | ✅ 19/19 passed | PKCE round-trip, allowlist matrix, prefix dispatch |
| `klai-knowledge-mcp/tests/test_dispatcher_branch.py` | ✅ 6/6 passed | Refresh-token block, JWT fall-through, case-insensitive Bearer |
| `klai-portal/backend/...` ruff check | ✅ groen | alle nieuwe + gewijzigde files |
| `klai-portal/backend/...` ruff format | ✅ groen | idem |
| `klai-knowledge-mcp` ruff check + format | ✅ groen | main.py + dispatcher.py + tests |
| `klai-libs/identity-assert` ruff check + format | ✅ groen | mcp_token_client.py + __init__.py |
| `klai-knowledge-mcp/tests/test_sec_internal_001.py` (LibreChat regression) | ⚠️ niet lokaal gerund | uv-path-resolver issue met klai-libs in worktree; CI moet dit dekken |
| FastAPI TestClient integration tests | ❌ niet geschreven | scope deze sessie |
| Playwright E2E | ❌ niet geschreven | afhankelijk van Fase 4 frontend |

## Beveiligingsmijlpalen die deze run zet

- ✅ Tokens nooit als raw teruggeleverd na issuance (REQ-5)
- ✅ Token-vergelijking via `hmac.compare_digest`, niet `==` (no-secret-eq-compare ast-grep regel)
- ✅ SHA-256 raw-bytes hashing (32-byte LargeBinary, geen hex)
- ✅ RLS Cat-D op `portal_mcp_tokens` met FORCE ROW LEVEL SECURITY
- ✅ Refresh-token rotation met replay-detection (REQ-26)
- ✅ Audience-binding via `resource_uri` kolom (RFC 8707)
- ✅ Strict redirect_uri allowlist — geen wildcards, hardcoded host-list
  voor web (research.md §11 attacker.openai.com.evil.com defense)
- ✅ Per-IP DCR rate-limit (10/h default)
- ✅ Header-strip op Caddy-edge: X-Internal-Secret/X-Caller-Service/
  X-Org-ID/X-User-ID/X-Org-Slug worden niet doorgelaten naar
  klai-knowledge-mcp (defense-in-depth bovenop dispatcher)
- ✅ DNS-rebinding-protection ON (was OFF voor LibreChat-only deploy)
- ✅ Application_type strict-validatie tegen redirect_uri-shape (REQ-13a)
- ✅ Fail-closed Pydantic validators op `MCP_OAUTH_*` env-vars

## Bestanden gewijzigd / toegevoegd

### Nieuw

- `.moai/specs/SPEC-MCP-AUTH-001/{spec.md,research.md,plan.md,handover.md}`
- `klai-portal/backend/alembic/versions/9f4e2c8a1b7d_add_mcp_oauth_tables.py`
- `klai-portal/backend/alembic/versions/post_deploy_9f4e2c8a1b7d.sql`
- `klai-portal/backend/app/models/mcp_oauth.py`
- `klai-portal/backend/app/services/mcp_oauth.py`
- `klai-portal/backend/app/api/mcp_oauth.py`
- `klai-portal/backend/app/api/me_mcp_tokens.py`
- `klai-portal/backend/tests/test_mcp_oauth_unit.py`
- `klai-libs/identity-assert/klai_identity_assert/mcp_token_client.py`
- `klai-knowledge-mcp/dispatcher.py`
- `klai-knowledge-mcp/tests/test_dispatcher_branch.py`

### Gewijzigd

- `klai-portal/backend/alembic/env.py` — `mcp_oauth` model imports
- `klai-portal/backend/app/core/config.py` — 5 nieuwe env-vars + validator
- `klai-portal/backend/app/core/rls_guard.py` — `portal_mcp_tokens` toegevoegd aan RLS_DML_TABLES
- `klai-portal/backend/app/main.py` — mount `mcp_oauth_router` + `me_mcp_tokens_router`
- `klai-portal/backend/app/api/internal.py` — `/internal/mcp-token/verify` endpoint
- `klai-portal/backend/scripts/rls-smoke-test.sql` — Test 8 voor portal_mcp_tokens
- `klai-libs/identity-assert/klai_identity_assert/__init__.py` — `McpTokenAsserter` export
- `klai-knowledge-mcp/main.py` — dispatcher + PRM-endpoint + DNS-rebinding ON + tools refactor
- `deploy/caddy/Caddyfile` — `mcp.getklai.com` upstream + comment-update

## Worktree

Deze branch leeft op een losse worktree zoals gevraagd:

```
/Users/mvletter/Developer/klai-mcp-auth (feature/mcp-auth-001)
```

Geen wijzigingen in `/Users/mvletter/Developer/Klai` (de hoofd-tree
blijft op `docs/audit-ti-2026-05-05`).
