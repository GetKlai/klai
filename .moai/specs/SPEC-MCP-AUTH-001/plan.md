# Plan — SPEC-MCP-AUTH-001

> File-level executieplan dat /moai run gebruikt als startpunt. Per fase: concrete files, deploy-volgorde, test-strategie, rollback. Dependent op research.md voor pattern-references.

## Dependency graph (fasen)

```
Fase 1 (DB)  ─────►  Fase 2 (portal-api)  ─────►  Fase 3 (knowledge-mcp)
                              │                              │
                              ▼                              ▼
                       Fase 4 (frontend)             Fase 5 (Caddy + transport)
                              │                              │
                              └───────► Fase 6 (E2E + verify) ◄──┘
```

Hard rules:
- Fase 1 MOET volledig ge-deployed zijn vóór Fase 2 (anders crashen portal-api endpoints op missing tables).
- Fase 5 MOET als laatste — opent externe traffic; pas mergen wanneer Fase 1-3 op staging E2E-bewezen zijn.
- Fase 3 dispatcher-refactor MOET met regression-tests op LibreChat-pad in dezelfde PR — geen partial deploy.

---

## Fase 1 — DB foundation

**Files (nieuw):**

| File | Type | Doel |
|---|---|---|
| `klai-portal/backend/alembic/versions/<rev>_add_mcp_oauth_tables.py` | migration | `op.create_table` voor `portal_mcp_tokens` + `portal_oauth_clients` (portal_api eigenaar). Geen RLS hier. |
| `klai-portal/backend/alembic/versions/post_deploy_<rev>_mcp_oauth_rls.sql` | post-deploy SQL | `ALTER TABLE ... OWNER TO klai`, `ENABLE ROW LEVEL SECURITY`, Cat-D policy op `portal_mcp_tokens`, Cat-B policies op `portal_oauth_clients`. Run als klai-superuser via `apply_post_deploy_sql.sh`. |
| `klai-portal/backend/app/models/portal.py` | extend | Nieuwe ORM models `PortalMcpToken`, `PortalOAuthClient`. |

**Files (modify):**

| File | Verandering |
|---|---|
| `klai-portal/backend/app/core/rls_guard.py` | Add `"portal_mcp_tokens"` aan `RLS_DML_TABLES`. |
| `klai-portal/backend/scripts/rls-smoke-test.sql` | Add cross-tenant SELECT/UPDATE/DELETE test op `portal_mcp_tokens`. |

**Test plan:**
- Alembic upgrade-check via `pytest tests/migrations/test_mcp_oauth_tables.py` (smoke: tabel bestaat, kolommen kloppen, FKs aanwezig).
- RLS-smoke-test runt in CI na migratie (zelfde pattern als bestaande RLS-rollouts).
- Geen integration tests deze fase — Fase 2 dekt het.

**Pre-commit checklist:**
- [ ] Migration is additive (geen ALTER TABLE op bestaande tabellen)
- [ ] `IF NOT EXISTS` op alle policy/index DDL (idempotent)
- [ ] post_deploy SQL is idempotent (re-runnable)
- [ ] FKs naar `portal_orgs(id)` en `portal_users(id)` cascade-on-delete behavior expliciet (`ON DELETE CASCADE` of `ON DELETE RESTRICT` per kolom-doel)

**Rollback:**
- Pre-merge: revert PR
- Post-merge: `alembic downgrade -1` + post_deploy SQL handmatig droppen via klai-superuser. Tabellen zijn additive — geen impact op bestaande functionaliteit.

---

## Fase 2 — Portal-api OAuth surface + verify endpoint

**Dependency toegevoegd:** `authlib==1.4.*` aan `klai-portal/backend/pyproject.toml` (zie research.md §7).

**Files (nieuw):**

| File | Doel |
|---|---|
| `klai-portal/backend/app/services/mcp_oauth/__init__.py` | service-package |
| `klai-portal/backend/app/services/mcp_oauth/token_issuer.py` | mint access + refresh tokens, hash-storage in DB |
| `klai-portal/backend/app/services/mcp_oauth/token_verifier.py` | verify access-token (Redis-cached, fallback DB) |
| `klai-portal/backend/app/services/mcp_oauth/token_verify_cache.py` | Redis cache (kopie van `identity_verify_cache.py` met andere key-prefix) |
| `klai-portal/backend/app/services/mcp_oauth/dcr.py` | RFC 7591 client-registration met allowlist-validatie |
| `klai-portal/backend/app/services/mcp_oauth/auth_request_store.py` | Redis storage voor pending auth-requests + auth-codes (TTL 10min / 60s) |
| `klai-portal/backend/app/services/mcp_oauth/pkce.py` | S256 code_challenge ↔ code_verifier verificatie |
| `klai-portal/backend/app/api/oauth.py` | endpoints: `GET/POST /oauth/authorize`, `POST /oauth/token`, `POST /oauth/register`, `GET /.well-known/oauth-authorization-server` |
| `klai-portal/backend/app/api/me_mcp_tokens.py` | `GET/DELETE /api/me/mcp-tokens` |
| `klai-portal/backend/app/templates/oauth_consent.html` | Jinja2 template voor consent-page |
| `klai-portal/backend/app/schemas/mcp_oauth.py` | Pydantic v2 models |

**Files (modify):**

| File | Verandering |
|---|---|
| `klai-portal/backend/app/api/internal.py` | Add `POST /internal/mcp-token/verify` (na de bestaande `verify_identity` op line 1337) — gebruikt `_require_internal_token` guard. |
| `klai-portal/backend/app/main.py` | Mount `oauth.router` en `me_mcp_tokens.router`. Add Jinja2Templates env voor consent-page. |
| `klai-portal/backend/app/core/config.py` | 5 nieuwe env-vars met `model_validator(mode="after")` fail-closed (zie research.md §10). |

**Files (modify in shared library):**

| File | Verandering |
|---|---|
| `klai-libs/identity-assert/klai_identity_assert/mcp_token_client.py` | nieuwe `McpTokenAsserter` class |
| `klai-libs/identity-assert/klai_identity_assert/__init__.py` | export `McpTokenAsserter` |
| `klai-libs/identity-assert/tests/test_mcp_token_client.py` | tests |

**Test plan:**
- Unit: PKCE-S256 (round-trip + tampered code_verifier), DCR redirect_uri-allowlist (positieve + 5 negatieve cases), token-issuer hash-storage, verify-cache hit/miss/cache-unavailable, refresh-rotation incl. replay-detectie, audit-emit on issue/revoke.
- Integration (FastAPI TestClient): full OAuth flow `register → authorize → consent-approve → token-exchange → verify → refresh → revoke`.
- Regression: bestaande `/internal/identity/verify` tests blijven groen (test_identity_verify_decision.py).
- Coverage doel: 90%+ op nieuwe service-laag, 85%+ op endpoints.

**Pre-commit checklist:**
- [ ] `hmac.compare_digest` op alle token/secret vergelijkingen (geen `==`); ast-grep `no-secret-eq-compare` groen
- [ ] Alle httpx clients hebben `timeout=` set (per `python-services.md`)
- [ ] CORS middleware blijft outermost (zie `lang/python.md::Starlette middleware registration order`)
- [ ] `ruff check` + `ruff format --check` + `pyright` lokaal groen vóór push
- [ ] Pydantic v2: `model_validate` (niet `parse_obj`), `ConfigDict(extra="forbid")` op alle schemas

**Rollback:** revert PR. Geen DB-impact als Fase 1 al draait. Endpoints worden gewoon weer 404.

---

## Fase 3 — Knowledge-mcp resource server

**Files (nieuw):**

| File | Doel |
|---|---|
| `klai-knowledge-mcp/auth.py` | `verify_via_oauth_token(raw_token) → _VerifiedIdentity` (gebruikt `McpTokenAsserter` uit klai-libs) |
| `klai-knowledge-mcp/well_known.py` | `GET /.well-known/oauth-protected-resource` handler (RFC 9728) |
| `klai-knowledge-mcp/tests/test_auth_dispatcher.py` | dispatcher branch-tests (klai_mcp_/klai_mcp_rt_/Zitadel-JWT/no-auth) |
| `klai-knowledge-mcp/tests/test_oauth_token_path.py` | nieuwe tests voor OAuth pad |

**Files (modify):**

| File | Verandering |
|---|---|
| `klai-knowledge-mcp/main.py` | (a) Refactor: extract `_identify_request(ctx) → _VerifiedIdentity` als single dispatcher (REQ-15). (b) Tools `save_personal_knowledge`, `save_org_knowledge`, `save_to_docs` vervangen identity-extractie aan top met `verified = await _identify_request(ctx)`. (c) `enable_dns_rebinding_protection=True` + `allowed_hosts=["mcp.getklai.com"]`. (d) `@MX:WARN` weghalen (situatie resolved). (e) Mount `well_known.py` route op FastMCP ASGI app. (f) WWW-Authenticate header op alle 401's. |
| `klai-knowledge-mcp/main.py` (env) | Extend `os.environ[...]`: `MCP_OAUTH_RESOURCE_URL`. Module-load fail-closed. |
| `klai-knowledge-mcp/pyproject.toml` | Bump `klai-identity-assert` dep version (na Fase 2 publish). |

**Test plan:**
- Regression-eerst: alle bestaande `klai-knowledge-mcp/tests/test_sec_internal_001.py` cases blijven groen — LibreChat-pad ongewijzigd.
- Dispatcher branch-tests: 4 input-shapes (klai_mcp_<...>, klai_mcp_rt_<...>, eyJ...-JWT, geen auth), elke route naar correct pad.
- OAuth-pad tests: success, revoked, expired, audience-mismatch, unknown-token, internal-secret-leak (PORTAL_INTERNAL_SECRET niet in error-body).
- PRM-endpoint: GET `/.well-known/oauth-protected-resource` retourneert correct JSON-shape (resource, authorization_servers, scopes_supported, bearer_methods_supported).

**Pre-commit checklist:**
- [ ] `ruff check klai-knowledge-mcp/` + `pyright klai-knowledge-mcp/` groen
- [ ] `klai-knowledge-mcp/tests/test_sec_internal_001.py` 100% groen (regressie)
- [ ] Dispatcher mechanisch onmogelijk te conflicten — test verifieert dat `Bearer klai_mcp_rt_<...>` NIET als access-token wordt gerouteerd
- [ ] `_identify_request` is enige call-site; tools roepen 'm aan via `await _identify_request(ctx)` in tools-bodies
- [ ] DNS-rebinding-protection-flip is in dezelfde commit als de Caddy-route addition (Fase 5) — anders sluit knowledge-mcp z'n eigen LibreChat-pad af lokaal omdat `allowed_hosts` hostname-restrictie op LibreChat's interne hostname `klai-knowledge-mcp:8080` mismatcht; ofwel `allowed_hosts` includes BOTH `mcp.getklai.com` en `klai-knowledge-mcp` ofwel het patroon is `Host`-header check waarvan we het Caddy-rewrite-gedrag eerst valideren

**Rollback:** revert main.py-commits. Het LibreChat-pad blijft draaien — ook de partial state is veilig omdat `_identify_request` eerst LibreChat-pad probeert wanneer prefix niet matcht.

---

## Fase 4 — Frontend "Connected applications"

**Files (nieuw):**

| File | Doel |
|---|---|
| `klai-portal/frontend/src/routes/settings/integrations/+page.tsx` | route entry |
| `klai-portal/frontend/src/routes/settings/integrations/ConnectedAppsList.tsx` | lijst-component |
| `klai-portal/frontend/src/routes/settings/integrations/RevokeConfirmDialog.tsx` | revoke-bevestiging |
| `klai-portal/frontend/src/lib/api/mcp-tokens.ts` | client-side API-wrapper rond `/api/me/mcp-tokens` |
| `klai-portal/frontend/messages/nl.json` | NL Paraglide strings |
| `klai-portal/frontend/messages/en.json` | EN Paraglide strings |
| `klai-portal/frontend/tests-e2e/settings-integrations.spec.ts` | E2E test |

**Files (modify):**

| File | Verandering |
|---|---|
| `klai-portal/frontend/src/routes/settings/+layout.tsx` | Add "Integrations" nav-item (volgt bestaande settings-nav patroon) |

**Test plan:**
- Component tests (Vitest): list-render, empty-state, revoke-dialog interaction
- E2E (Playwright, gebruikt `~/.claude/mcp-storageState.json`): mock-OAuth flow start → consent approve → token verschijnt in lijst → revoke → token weg

**Pre-commit checklist:**
- [ ] Volgt `klai-portal/CLAUDE.md`: form-pages `max-w-lg`, header `flex items-center justify-between mb-6`, components/ui/, color tokens (geen `text-red-600`), Paraglide voor strings
- [ ] Geen create-form (REQ-out-of-scope) — alleen list + revoke
- [ ] Empty-state copy: "Verbind Klai met Claude Desktop, Cursor, of ChatGPT door `https://mcp.getklai.com/mcp` toe te voegen als custom connector. Je wordt hierheen teruggeleid om goed te keuren."

**Rollback:** revert PR. Alleen frontend, geen backend impact.

---

## Fase 5 — Caddy + transport hardening

**Files (modify):**

| File | Verandering |
|---|---|
| `deploy/caddy/Caddyfile` | Nieuwe block voor `mcp.getklai.com` → `reverse_proxy klai-knowledge-mcp:8080`, log-block, request-headers. Update bestaande comment "klai-knowledge-mcp not internet-reachable" naar de nieuwe situatie. |
| `deploy/docker-compose.yml` | Geen wijziging aan knowledge-mcp service-block (blijft op `klai-net`, NIET op `socket-proxy` per `docker-socket-proxy.md` REQ-5). Alleen verifieer in PR-review. |
| `klai-knowledge-mcp/main.py` | DNS-rebinding-flip + `allowed_hosts` reeds in Fase 3 commit; geen extra wijzigingen hier. |

**Files (nieuw):**

| File | Doel |
|---|---|
| `scripts/smoke-test-mcp-oauth.sh` | post-deploy smoke: `curl -i https://mcp.getklai.com/mcp` retourneert 401 + WWW-Authenticate header met `resource_metadata=...`; `curl -s https://mcp.getklai.com/.well-known/oauth-protected-resource` retourneert valide JSON; `curl -s https://my.getklai.com/.well-known/oauth-authorization-server` idem. |

**Test plan:**
- Smoke-script draait post-deploy als laatste GitHub Action step
- SSRF-isolation smoke (`scripts/smoke-ssrf-isolation.sh`) blijft groen — knowledge-mcp blijft op no-fly-list
- DNS-rebinding-protection smoke: HTTP request met `Host: evil.example.com` naar Caddy → 421 misdirected (Caddy-niveau) en niet route-d naar knowledge-mcp

**Pre-commit checklist:**
- [ ] Caddy-config diff alleen in `mcp.getklai.com` block + comment-update — geen andere services geraakt
- [ ] `caddy validate /etc/caddy/Caddyfile` lokaal groen vóór deploy
- [ ] DNS-record `mcp.getklai.com` IN A → core-01 IP (Hetzner DNS) is gezet vóór merge — anders 404 bij first request

**Rollback:** comment-out van `mcp.getklai.com` block in Caddyfile + reload. Knowledge-mcp blijft via Docker-internal hostname bereikbaar voor LibreChat.

---

## Fase 6 — End-to-end verificatie

**Manual flows die moeten werken vóór sync naar main:**

1. **Claude Desktop:** "Add custom connector" → URL `https://mcp.getklai.com/mcp` → DCR succeed → consent-page render correct (client_name "Claude Desktop", redirect_uri zichtbaar) → approve → tools werken op user's eigen org-data.
2. **Cursor:** zelfde flow.
3. **ChatGPT custom connectors:** zelfde flow (als beschikbaar 2026-Q2).
4. **LibreChat regression:** `klai-portal-pattern` flows (save_personal_knowledge, save_org_knowledge, save_to_docs) blijven werken voor een test-tenant.
5. **Revoke flow:** Settings → Integrations → revoke → tweede tool-call met dezelfde access-token retourneert 401, refresh-attempt retourneert 400 invalid_grant.
6. **Refresh-rotation replay:** simuleer refresh-token reuse via `curl` na succesvolle rotation → verwacht: alle bijhorende tokens revoked, telemetrie-event in `portal_audit_log`.
7. **Cross-tenant isolation:** user van org A doet OAuth-flow, gebruikt token om data van org B te lezen → MCP-tool returnt 0 results (RLS) of 403.

---

## TRUST 5 alignment per fase

| Fase | Tested | Readable | Unified | Secured | Trackable |
|---|---|---|---|---|---|
| 1 | Migration smoke + RLS-smoke | Comments op DDL | post_deploy SQL pattern | RLS Cat-D + ENABLE | Conventional commit ref SPEC |
| 2 | 90%+ unit + integration | structlog event-emit | authlib-conventions + portal patterns | PKCE+DCR allowlist+audience+rotation | Audit-log on token-issue/revoke |
| 3 | Regression + dispatcher branch | dispatcher-functie geïsoleerd | FastMCP-conventions | DNS-rebinding+allowed_hosts | structlog event op verify-fail |
| 4 | E2E + component tests | Paraglide + components/ui/ | klai-portal CLAUDE.md design | Geen secrets in frontend | n/a |
| 5 | Smoke-script post-deploy | Caddyfile-comments | Caddy-block patterns | TLS via Caddy + Hetzner DNS | Caddy access-log per request |

## @MX tags die toegevoegd worden

| File | Tag-type | Reden |
|---|---|---|
| `klai-knowledge-mcp/main.py` (FastMCP-init) | `@MX:ANCHOR` (vervangt huidige WARN) | fan_in via 3 tools die allen door `_identify_request` gaan |
| `klai-knowledge-mcp/main.py::_identify_request` | `@MX:ANCHOR` | nieuwe high-fan-in dispatcher (3 tools = 3 callers) |
| `klai-portal/backend/app/api/internal.py::verify_mcp_token` | `@MX:ANCHOR` | aangeroepen door alle MCP-instances; cross-service contract |
| `klai-portal/backend/app/services/mcp_oauth/token_issuer.py::issue_access_token` | `@MX:ANCHOR` | enige plek waar tokens gemint worden — security-relevant invariant |
| `klai-portal/backend/app/services/mcp_oauth/dcr.py::register_client` | `@MX:WARN` | `@MX:REASON: redirect_uri allowlist is enige defense tegen confused deputy; wijziging vereist threat-modeling` |

## SOPS env-var rollout-volgorde

1. **Vóór Fase 2 deploy:** `MCP_OAUTH_ISSUER_BASE_URL`, `MCP_OAUTH_TOKEN_TTL_DAYS`, `MCP_OAUTH_REFRESH_TTL_DAYS`, `MCP_OAUTH_DCR_RATE_LIMIT_PER_HOUR` in SOPS — anders crash portal-api start (validator-env-parity).
2. **Vóór Fase 3 deploy:** `MCP_OAUTH_RESOURCE_URL` in beide portal-api én knowledge-mcp environment-blocks in `deploy/docker-compose.yml` — anders crash knowledge-mcp start.
3. SOPS GitHub Action auto-syncen verwerkt dit; geen manuele acties op core-01.

## Open questions die /moai run kan wegwerken (geen merge-blockers)

Uit spec.md Open questions sectie:
1. Client_name spoofing: minimaal — log alle DCR's prominent. Latere SPEC voor reserved-names.
2. Audit-retention: standaard.
3. Token-name semantiek: UI toont `client_name`, niet user-name. Geen actie.

Plus uit research.md §12:
4. Cache-TTL env-var: één gedeelde `IDENTITY_VERIFY_CACHE_TTL_SECONDS` of twee aparte (`IDENTITY_VERIFY_TTL` + `MCP_TOKEN_VERIFY_TTL`)? Aanbeveling /moai run: **één gedeelde**, default 60. Spaart 1 SOPS-var en garandeert symmetrische fail-closed semantics.
