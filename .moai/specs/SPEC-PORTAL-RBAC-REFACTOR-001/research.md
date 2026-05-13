# SPEC-PORTAL-RBAC-REFACTOR-001 — Research

> Onderzoeksdocument voor SPEC-PORTAL-RBAC-REFACTOR-001 v0.2.0-research.
> Bevindingen uit zeven parallel uitgevoerde threads. Synthese onderaan.

## Status per thread

| Thread | Onderwerp | Status |
|---|---|---|
| A | Platform-locked feature model | ✓ |
| B | MCP per-user tool-gating (industry + internal) | ✓ |
| C | LibreChat-internal-secret pad in klai-knowledge-mcp | ✓ |
| D | Partner-API + chat-widgets inventory + grandfathering | ✓ |
| E | Phase 2 splitsen per domein (impact-analyse) | ✓ |
| F | Characterization-test strategie | ✓ |
| G | Frontend impact-analyse | ✓ |

---

## Thread A — Platform-locked feature model

### Bestaand mechanisme (bevestigd)

- `_require_platform_admin(caller_org)` in `klai-portal/backend/app/api/admin/__init__.py:100-115`
- Werkt via `caller_org.slug == settings.platform_org_slug` (default `getklai`)
- RLS GUC `app.is_platform_admin` gezet in `_get_caller_org` regel ~89
- Gebruikt op: `retry_provisioning.py`, `deprovision_org.py`

### Bestaande tenant-toggle-laag (parallel-mechanisme)

- Kolom `portal_orgs.enabled_addons text[]` (`models/portal.py:84-89`)
- Endpoint `PATCH /api/admin/settings/addons` (settings.py:152-201) — tenant-admin
- Features: `scribe`, `docs`
- Derivatie via `derive_user_products(role, plan, enabled_addons)` (`core/features.py`)

### Aanbevolen datamodel: nieuwe kolom

```sql
ALTER TABLE portal_orgs
ADD COLUMN platform_unlocked_features text[] NOT NULL DEFAULT '{}';
```

Parallel aan `enabled_addons`. Onderscheid:
- `enabled_addons` — tenant-admin togglet, voor features waar Klai geen poortwachter wil zijn (scribe, docs)
- `platform_unlocked_features` — alleen platform-admin togglet, voor features waar Klai expliciet per-tenant moet beslissen (partner_api, widgets, custom_mcps)

Endpoints: `GET /api/admin/orgs/{slug}/platform-unlocks`, `PATCH /api/admin/orgs/{slug}/platform-unlocks` — beide gegate op `_require_platform_admin`.

Audit via bestaande `tenant_lifecycle_events` (`actor_type IN ('owner', 'platform_admin', 'system')` — al voorzien).

---

## Thread D — Inventarisatie + grandfathering

### Partner-API
- Endpoints: `GET /partner/v1/knowledge-bases`, `POST /partner/v1/chat/completions`, `POST /partner/v1/knowledge` (`api/partner.py`, `partner_dependencies.py`)
- Gate vandaag: `get_partner_key(PartnerAuthContext)` — Bearer `pk_...` token, SHA-256 lookup in `partner_api_keys`
- Geen tenant-level toggle — als key bestaat, mag het
- Default: keys staan uit per nieuwe tenant (admin moet er een aanmaken)
- **Grandfathering: HOOG.** Bestaande keys werken vandaag. Bij introductie platform-lock moeten tenants met actieve keys initieel `partner_api` in `platform_unlocked_features` krijgen. Query: `SELECT DISTINCT org_id FROM partner_api_keys WHERE active = true`.

### Chat-widgets
- Endpoints: `POST/GET/PATCH/DELETE /api/admin/widgets[/{id}]` (`admin_widgets.py:160-338`)
- Gate vandaag: `_require_admin(caller_user)`
- Default: geen widgets totdat admin aanmaakt
- **Grandfathering: LAAG.** Pilot-fase. Query `SELECT DISTINCT org_id FROM widgets` — naar verwachting handvol tenants. Initieel unlock voor die tenants, rest dicht.

### Custom MCPs (tenant-admin toegevoegd)
- Endpoints: `GET /api/mcp-servers`, `PUT /api/mcp-servers/{server_id}` (`mcp_servers.py:92-137`)
- Stored in `portal_orgs.mcp_servers` JSON blob
- Gate vandaag: `_require_admin(caller_user)`
- **Onderscheid:** managed MCPs (catalog-based, `managed: true` flag) blijven altijd beschikbaar. Custom MCPs (admin-defined) komen achter platform-lock.
- **Grandfathering: MEDIUM.** Query tenants met `mcp_servers` waar `managed != true` en `enabled = true`. Initieel unlock voor die tenants.

### Aanbeveling

Drie nieuwe gates bovenop de bestaande:

| Feature | Huidige gate | Nieuwe gate |
|---|---|---|
| Partner-API | partner_api_key | + `partner_api` in `platform_unlocked_features` |
| Chat-widgets | `_require_admin` | + `widgets` in `platform_unlocked_features` |
| Custom MCPs | `_require_admin` | + `custom_mcps` in `platform_unlocked_features` (managed bypass) |

Implementatie: één pure helper `assert_platform_unlocked(org, feature)` in `core/permissions.py`, plus FastAPI-dependency `Depends(require_platform_unlocked("widgets"))` als declaratieve variant.

---

## Thread B — MCP per-user tool-gating

### Industry-state (samenvatting van externe research)

**MCP protocol:** Geen verplichte standaard, twee actieve drafts ([SEP-1881](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1881) — scope-filtered tool discovery; [SEP-1300](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1300) — groups/tags filtering met `listChanged`-notificaties). Beide zijn MAY, niet MUST.

**Productie-referentie — GitHub MCP** ([scope-filtering.md](https://github.com/github/github-mcp-server/blob/main/docs/scope-filtering.md)):
- Classic PAT: server doet HEAD-request bij startup, leest `X-OAuth-Scopes`, **filtert `tools/list`** zodat de LLM disallowed tools nooit ziet
- OAuth-flow: alle tools blijven zichtbaar, server gebruikt **scope-challenges** — bij `tools/call` vraagt het de extra scope on-demand

**LangChain/LangGraph:** `@auth.on`-decorators retourneren filters voor list-operaties of rejecten requests. User-identity in `config["configuration"]["langgraph_auth_user"]`.

**Enterprise pattern:** MCP Gateway (Traefik Hub, IBM context-forge) plaatst proxy met JWT-claim policies vóór de MCP-server.

### Aanbeveling voor klai-knowledge-mcp

**Dubbele gating: filter `tools/list` per user + check opnieuw in `tools/call`.**
- `tools/list` filtering = UX-laag. Een `personal`-user moet `save_org_knowledge` nooit zien — anders stuurt de LLM het als plausibele optie en krijgt user mysterieuze 403's.
- `tools/call` her-checken = security-laag. Tool-list kan stale zijn; rebellious clients kunnen niet-gelijste tools alsnog aanroepen. Defense-in-depth.

**Effective_role propagation door internal-secret pad:** gebruik bestaande `klai_identity_assert` library (SPEC-SEC-IDENTITY-ASSERT-001). Caller signt korte JWT `{user_id, org_id, effective_role, exp}` met shared secret. MCP verifieert + leest role. Geen nieuwe primitive; zelfde key-rotation, zelfde audit.

### Custom MCP toevoegen — risicostack (industry-research)

| Vector | Bron | Mitigatie |
|---|---|---|
| Tool poisoning via `description` veld | [Invariant Labs](https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks), [Snyk](https://labs.snyk.io/resources/prompt-injection-mcp/), [arxiv MCPTox](https://arxiv.org/html/2508.14925v1) (5.5% van publieke MCPs vandaag kwetsbaar) | Description-sanitization, admin-approval per tool, rate-limit additions |
| SSRF via tenant-supplied URL | [GHSA-7r34-79r5-rcc9](https://github.com/sooperset/mcp-atlassian/security/advisories/GHSA-7r34-79r5-rcc9), CVE-2026-39974 | URL-allowlist publieke domains, egress firewall (block 169.254.169.254 + RFC1918), DNS-resolution check |
| Cross-server hijack — malicious MCP injecteert prompts naar klai-knowledge-mcp | [Docker WhatsApp story](https://www.docker.com/blog/mcp-horror-stories-whatsapp-data-exfiltration-issue/) | Per-server isolation client-side; geen shared context zonder explicit consent |
| Data-exfiltratie via tool-output | [CyberArk](https://www.cyberark.com/resources/threat-research-blog/poison-everywhere-no-output-from-your-mcp-server-is-safe) | Audit-log alle outbound calls; user-visible disclosure; per-tenant kill-switch |
| Pre-use exfil via OAuth callback | [Trail of Bits](https://blog.trailofbits.com/2025/04/21/jumping-the-line-how-mcp-servers-can-attack-you-before-you-ever-use-them/) | Klai-tokens nooit doorgeven aan custom MCPs; eigen scope-isolation per server |

Mitigatie-stack matcht aanbevelingen [Microsoft](https://developer.microsoft.com/blog/protecting-against-indirect-injection-attacks-mcp) + [Elastic Security Labs](https://www.elastic.co/security-labs/mcp-tools-attack-defense-recommendations): allowlist + egress firewall + content-policy + audit + kill-switch + verplichte description-review. **Klai's keuze (platform-locked + default-off) is consistent met enterprise-recommendation.**

### Open architectuurvragen

- **`listChanged` notifications:** rol-changes live propageren of client-reconnect? Claude Desktop / LibreChat support is wisselend.
- **Caching-strategie:** `tools/list` per-user-rendering. Acceptabel of cache per `(role, tenant)`-tuple?
- **OAuth-migratie-pad:** blijft internal-secret bestaan voor LibreChat, of migreren naar OAuth 2.1 resource-server pattern op termijn?

---

## Thread C — LibreChat-internal-secret pad

### Hoe identity vandaag werkt

**OAuth pad** (`klai-knowledge-mcp/main.py:356-388`):
- Authorization: `klai_mcp_<token>`
- Verified via portal-api `/internal/mcp-token/verify`
- Returns `_VerifiedIdentity` met `user_id`, `org_id`, `org_slug`, `client_id`
- **Geen role-info**

**Internal-secret pad** (`klai-knowledge-mcp/main.py:391-411`):
- LibreChat stuurt: `X-Internal-Secret`, `X-User-ID`, `X-Org-ID`, `X-Org-Slug`
- `_validate_incoming_secret` checkt secret tegen `KNOWLEDGE_INGEST_SECRET`
- `_get_claimed_identity` leest claim
- `_verify_identity` POST naar portal-api `/internal/identity/verify` (`klai_identity_assert` lib)
- **Ook geen role**

### LibreChat user-koppeling

Pad: LibreChat MongoDB ObjectId → `openIdId` (Zitadel sub) → `portal_users.zitadel_user_id` → `PortalUser` (incl. role).

Lazy mapping in `internal.py:613-705` (`GET /v1/users/{librechat_user_id}/feature/knowledge`): bij eerste call doet portal-api Mongo-lookup, schrijft `portal_users.librechat_user_id = ObjectId`. Daarna fast path.

### Aanbeveling

**Optie B — internal endpoint, niet DB-lokaal in MCP.**

Nieuw: `GET /internal/users/{zitadel_user_id}/permissions` in `klai-portal/backend/app/api/internal.py`. Response = serialized `UserPermissions` (uit Phase 1 resolver). Auth: bestaande `_require_internal_token` (Bearer `PORTAL_INTERNAL_SECRET`).

**Waarom B niet A:**
- A (DB-lokaal in MCP-container) vereist `PORTAL_DB_URL` + encryption-key in MCP — verdubbeling van credential-surface, verkeerde boundary.
- B leunt op bestaand patroon `/internal/users/{id}/products` (al in `internal.py`); minimale toevoeging.

Audit-trail via bestaande `_audit_internal_call`.

### Open vragen
- Ook role-claim in `klai_identity_assert` JWT meegeven (zoals Thread B aanbeveelt) → MCP hoeft dan niet eens internal call te doen voor de happy path. Trade-off: JWT moet vaker geroteerd om stale role-info te voorkomen.

---

## Thread E — Phase 2 splitsen per domein

### File-inventaris (admin-gates)

8 sub-PRs aanbevolen om de huidige geplande "ene grote Phase 2" reviewable te maken. Onderverdeling op file-domein + endpoint-count:

| Sub-PR | Files | Endpoints | LOC | Risico |
|---|---|---|---|---|
| **2a** Admin: Users & core tenant | `admin/users.py`, `admin/__init__.py`, `admin/settings.py`, `admin/products.py`, `admin/retry_provisioning.py` | ~18-22 | 400-550 | Laag |
| **2b** Admin: Audit, provisioning | `admin/audit.py`, `admin/deprovision_org.py`, `admin/join_requests.py` | ~8-10 | 200-280 | Laag |
| **2c** Admin: API keys & widgets | `admin_api_keys.py`, `admin_widgets.py` | 10 | ~750 | Laag |
| **2d** Billing | `billing.py` | 5 | ~225 | Laag |
| **2e** KB CRUD | `knowledge_bases.py`, `app_knowledge_sources.py`, `app_templates.py`, `knowledge.py` + CRUD-helft van `app_knowledge_bases.py` | ~15-18 | 450-550 | Medium |
| **2f** Taxonomy & advanced KB ops | `taxonomy.py` + advanced helft van `app_knowledge_bases.py` | ~16-20 | 700-850 | Medium |
| **2g** Connectors & MCPs | `connectors.py`, `mcp_servers.py`, `app_gaps.py` | 13 | 600-750 | Medium |
| **2h** Groups & me-tokens | `groups.py`, `me_mcp_tokens.py` | ~15 | 650-750 | Medium |

### Volgorde-aanbeveling

1. **Phase 1** (foundation): `dependencies.py` → `core/permissions.py`, central resolver, typed deps
2. **Phase 2a → 2b** (admin foundation, sequentieel)
3. **Phase 2c + 2d** parallel (geïsoleerde domains)
4. **Phase 2e** (KB CRUD)
5. **Phase 2f** (waar 2e op leunt)
6. **Phase 2g + 2h** parallel

Totale doorlooptijd: 4-5 weken bij 1 PR/week + paralleliseerbare paren.

### Open vragen
- `app_knowledge_bases.py` (1453 LOC) splits-grens tussen 2e en 2f exact specificeren
- `meetings.py` (782 LOC) — los of in 2h (2 admin-checks)
- `app_account.py` — los of meeliften met 2h

---

## Thread F — Characterization-test strategie

### Bestaande rol-coverage

| File | Coverage |
|---|---|
| `tests/test_admin_users.py` | invite_user, offboard_user (mocked DB) |
| `tests/test_spec_portal_admin_ui_001.py` | admin UI SPEC |
| `tests/test_plan_limits.py` | plan/role matrix |
| `tests/test_profiles.py` | profile-functies (incl. `test_personal_role_skips_default_org_role_kbs` — pinning op functie-niveau, NIET endpoint-niveau) |

### Risico-set: endpoints met `_require_admin` zonder rol-test

- `admin_widgets.py` (alle 6 widget-endpoints)
- `billing.py`
- `admin_api_keys.py`
- `mcp_servers.py`
- `groups.py` (gedeeltelijk)
- `app_gaps.py`
- `taxonomy.py`

Voor elk: snapshot-test schrijven VOOR de gate-rewrite.

### Snapshot-template

```python
@pytest.mark.asyncio
async def test_endpoint_role_matrix_snapshot(client, auth_admin, auth_company, auth_unauth):
    """Pin HTTP-status per rol vóór refactor.

    Verifieert dat declaratief Depends(get_caller_at_least(...)) identiek
    gedraagt aan imperatief _require_admin(caller).
    """
    resp_admin = await client.get("/api/admin/widgets",
        headers={"Authorization": f"Bearer {auth_admin.token}"})
    assert resp_admin.status_code == 200

    resp_company = await client.get("/api/admin/widgets",
        headers={"Authorization": f"Bearer {auth_company.token}"})
    assert resp_company.status_code == 403

    resp_unauth = await client.get("/api/admin/widgets")
    assert resp_unauth.status_code == 401
```

### Bestaande fixtures
- `respx_zitadel` (conftest) — gemockte Zitadel userinfo
- `AsyncMock` patterns voor `db.execute`, `db.commit`
- **Geen pre-built role-factory** — Phase 1 zou een `make_user(role=...)` factory in conftest moeten toevoegen.

---

## Thread G — Frontend impact-analyse

### Lees-locaties per veld

| Veld | Locaties | Phase-impact |
|---|---|---|
| `user.effective_role` | `hooks/useProtectedRoute.ts:75` (route-gating) | Canoniek vanaf Phase 2 — geen wijziging |
| `user.role` | `routes/admin/users/index.tsx:296,305`; `routes/admin/users/$userId/edit.tsx:77-79` | Legacy alias-fase OK; follow-up SPEC migreert naar `effective_role` |
| `user.products` | `routes/admin/settings.tsx:104-105`; sidebar via `useCurrentUser()` | Blijft werken in alias-fase |
| `user.capabilities` / `user.effective_capabilities` | **Geen directe lees-locaties.** FE gebruikt `user.isAdmin` / `user.isGroupAdmin` helpers (afgeleid van rol) | Veilig — alias-wijziging onzichtbaar voor FE |

### Migratie-strategie

**Phase 2 (alias-fase, geen FE-werk vereist):**
- Backend stuurt `portal_role` (default → "personal"), `effective_role` (canoniek), `effective_capabilities` (canoniek), plus aliassen `role` en `capabilities` (zelfde inhoud)
- FE leest oud-en-nieuw door elkaar zonder breekrisico

**Follow-up SPEC (na deze refactor landt):**
- `routes/admin/users/index.tsx` en `$userId/edit.tsx` migreren `user.role` → `user.portal_role` (legacy field) of `user.effective_role` (canoniek)
- Backend laat `role` en `capabilities` aliassen vallen
- TypeScript types updaten

### Open vragen
- Fallback-strategie alias-fase: server-side aliassen in `MeResponse` of FE-fallback in `useCurrentUser()` hook?
- `capabilities` veld: actief deprecation-warning (sentry) of stille drop in follow-up?

---

## Synthese — wat moet er in v0.3.0 van de SPEC

### Schema-wijziging die nu wel nodig is

Eén kolom: `portal_orgs.platform_unlocked_features text[] NOT NULL DEFAULT '{}'`. Eén Alembic-migration. Geen post-deploy SQL.

### Phases bijwerken naar 6 hoofdphases

1. **Phase 1 — Architecturale consolidatie** (was zo, blijft zo)
2. **Phase 2 — Endpoints overzetten** (splitsen in 2a t/m 2h, totaal 8 sub-PRs)
3. **Phase 3 — Enforcement gaps** (zoals nu, automatic-uit-Phase-2)
4. **Phase 4 — MCP-laag** (uitbreiden: dubbele gating tools/list+tools/call, klai_identity_assert JWT met role, internal endpoint `/internal/users/{id}/permissions`)
5. **Phase 5 — Platform-locked features** (NIEUW: kolom + endpoints + drie feature-gates + grandfathering-script)
6. **Phase 6 — Cleanup** (was 5)

### Nieuwe REQ's toe te voegen

| REQ | Onderwerp |
|---|---|
| REQ-20 | `portal_orgs.platform_unlocked_features` kolom + Alembic migration |
| REQ-21 | `_require_platform_unlocked(feature)` helper + dependency |
| REQ-22 | Partner-API endpoints checken `partner_api ∈ platform_unlocked_features` |
| REQ-23 | Widget endpoints checken `widgets ∈ platform_unlocked_features` |
| REQ-24 | Custom MCPs checken `custom_mcps ∈ platform_unlocked_features` (managed bypass) |
| REQ-25 | Platform-admin endpoints `GET/PATCH /api/admin/orgs/{slug}/platform-unlocks` |
| REQ-26 | Grandfathering-migration: tenants met actieve partner_api_keys / widgets / custom_mcps krijgen feature initieel unlocked |
| REQ-27 | `klai-knowledge-mcp` filtert `tools/list` per user (personal user ziet `save_org_knowledge` niet) |
| REQ-28 | `klai-knowledge-mcp` re-checkt rol in `tools/call` (defense-in-depth) |
| REQ-29 | LibreChat-internal-secret pad: `klai_identity_assert` JWT bevat `effective_role` claim |
| REQ-30 | Custom-MCP-toevoegen vereist URL-allowlist + description-sanitization + per-tool admin-approval (alleen relevant zodra die feature platform-unlocked is) |

### AC's toe te voegen

- Phase 5 AC: nieuwe tenant heeft `platform_unlocked_features=[]` default; partner-API call → 403; widget create → 403; custom-mcp config → 403. Platform-admin unlockt `partner_api` → key-gebaseerde call werkt weer.
- Phase 5 AC: grandfathering-script draait dry-run op staging, toont N tenants per feature, lijst klopt met handmatige verificatie.
- Phase 4 AC: personal-user via Claude Desktop ziet `save_org_knowledge` niet in tool-list; client probeert toch te callen → response is een tool-error string.
- Phase 4 AC: LibreChat-pad met personal-user → MCP weigert org-write zonder portal-api round-trip (claim uit JWT volstaat).

### Test-strategie aangepast

- Phase 1 levert `make_user(role=...)` factory in conftest.
- Pre-Phase-2: characterization snapshots voor de risico-set (~7 files / ~40 endpoints) gepind als 200/403/401-matrix.
- Phase 4 heeft eigen MCP-tests in `klai-knowledge-mcp/tests/` voor tool-list-filtering en tool-call-rejection per rol.

### Frontend impact

Minimaal in deze SPEC. Phase 2 alias-fase laat FE intact. Een follow-up SPEC migreert `user.role` lees-locaties (twee plekken) naar `user.portal_role` of `user.effective_role`.

### Beslissingen die de SPEC nu mag fixeren (geen open einden meer)

- **Datamodel platform-locked features**: extra kolom op `portal_orgs`, niet aparte tabel. Audit via bestaande `tenant_lifecycle_events`.
- **MCP rol-propagatie**: via `klai_identity_assert` JWT met role-claim. Internal endpoint als fallback voor cache-miss / signature-rotate.
- **Tool-gating**: dubbel (list + call), via per-tool capability-check. Pattern matcht GitHub MCP.
- **Custom MCPs**: gehele feature platform-locked + default-off. Risico-stack zit in REQ-30; zolang de feature niet unlocked is, irrelevant.
- **Phase 2 splitsing**: 8 sub-PRs zoals in Thread E.
- **Test-strategie**: characterization snapshots vereist VOOR elke gate-rewrite.

### Open vragen voor Mark vóór v0.3.0 final

1. **Custom-MCPs grandfathering**: zijn er vandaag tenants met custom MCPs in productie die initieel unlocked moeten worden? (`SELECT org_id FROM portal_orgs WHERE jsonb_path_exists(mcp_servers, '$[*] ? (@.managed != true && @.enabled == true)')`)
2. **Partner-API grandfathering**: zelfde vraag voor `partner_api_keys`. Meerdere tenants die dit gebruiken?
3. **`platform_unlocked_features` initial-state on tenant-create**: helemaal leeg, of voor "trusted" plans (complete) één-of-meer features default-aan?
4. **MCP `listChanged`-notificaties**: ondersteunen vandaag of in follow-up SPEC? Claude Desktop ondersteunt het, LibreChat-MCP-bridge is onzeker.
