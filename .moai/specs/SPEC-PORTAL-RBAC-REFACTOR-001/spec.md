---
id: SPEC-PORTAL-RBAC-REFACTOR-001
version: "0.3.0"
status: ready-for-run
created: 2026-05-08
updated: 2026-05-08
author: Mark Vletter
priority: high
related:
  - SPEC-PORTAL-PROFILES-001 (5-rung ladder + PROFILE_CAPABILITIES — basis blijft)
  - SPEC-PORTAL-RBAC-001 (single-source derivation — basis blijft)
  - SPEC-PORTAL-UNIFY-KB-001 (PLAN_LIMITS — basis blijft)
  - SPEC-AUTH-008 (group memberships — basis blijft)
  - SPEC-INFRA-TENANT-DELETE-001 (`_require_platform_admin` — wordt hergebruikt)
  - SPEC-SEC-IDENTITY-ASSERT-001 (`klai_identity_assert` JWT — uitgebreid met role-claim)
supersedes:
  - SPEC-PORTAL-RBAC-CLEANUP-001 (draft 2026-05-08, never shipped — scope hier opgenomen)
---

# SPEC-PORTAL-RBAC-REFACTOR-001: Permission-laag industriestandaard maken

## HISTORY

| Date | Version | Change |
|------|---------|--------|
| 2026-05-08 | 0.1.0 | Initial draft. Refactor-first aanpak. Vervangt eerder geplande SPEC-PORTAL-RBAC-CLEANUP-001. |
| 2026-05-08 | 0.2.0-research | Status terug naar `research` na review-ronde. Zeven onderzoeksthreads parallel uitgevoerd, bevindingen in `research.md`. |
| 2026-05-08 | 0.3.0 | Bevindingen uit research verwerkt. Phase 5 toegevoegd (platform-locked features met nieuwe `portal_orgs.platform_unlocked_features` kolom). Phase 4 (MCP) uitgebreid met dubbele tool-gating (list+call) + `klai_identity_assert` role-claim + `listChanged`-notificaties. Phase 2 opgesplitst in 8 sub-PRs. Pre-phase characterization tests toegevoegd. Custom-MCP risico-stack uit scope (alleen catalog-selectie, geen tenant-eigen URLs). Geen grandfathering nodig (geen actieve productie-gebruikers). Status: ready-for-run. |

---

## Summary

PROFILES-001 + RBAC-001 hebben het derivation-model goed neergezet, maar de implementatie is uit twee fases gegroeid en draagt de littekens. Vier echte enforcement-gaps (waaronder critical: personal users kunnen org-kennis bevragen), drie stale comments, twee dode-code-residus, en — dieper — een architectuur waarin permissie-resolutie verspreid is over zes losse paden, gates inconsistent zijn (deels declaratief, deels imperatief), en plan-product mappings in twee bestanden naast elkaar leven.

Bovendien heeft de MCP-laag (`klai-knowledge-mcp`) vandaag geen rol-check op tools — een personal user via Claude Desktop of LibreChat kan ongegate org-data benaderen. En drie features (Partner-API, chat-widgets, catalog-MCP-selectie) horen platform-locked te zijn (alleen Klai-staff kan ze per-tenant aanzetten) maar staan vandaag op tenant-admin-niveau open.

Deze SPEC pakt eerst de architectuur aan en laat de gaps dan vanzelf dichtgaan als bijproduct. Concreet: één `UserPermissions` resolver, één bron voor plan-features, alle gates uniform declaratief via `Depends(...)`, één typed caller-object dat overal heen reist. Daarna trekken we alle endpoints, het `/api/me` veld, en de MCP-laag door naar dezelfde laag. Dan platform-locked-features. Tot slot opruimen wat nu nog Phase-2-gedrag suggereert.

Eén kleine schema-wijziging: nieuwe kolom `portal_orgs.platform_unlocked_features text[]`. Eén Alembic-migration. Geen post-deploy SQL. Geen actieve productie-gebruikers (alleen test-tenant Voys), dus geen grandfathering nodig — alle tenants starten met platform-features uit en Klai-staff zet ze per-tenant aan zodra die feature aan klanten beschikbaar wordt gesteld.

---

## Conceptueel kader

| Mechanisme | Functie | Wie togglet | Bron |
|---|---|---|---|
| **Plan** (`portal_orgs.plan`) | Workspace billing-tier. Bepaalt baseline-products en kb-capability-ceiling. | Klai sales / billing | `portal_orgs` |
| **Add-ons** (`portal_orgs.enabled_addons`) | Tenant-toggle voor optionele products waar Klai geen poortwachter wil zijn. Vandaag: scribe, docs. | Tenant-admin | `portal_orgs.enabled_addons` |
| **Platform-unlocked features** (`portal_orgs.platform_unlocked_features`) — NIEUW | Per-tenant unlock voor features waar Klai expliciet moet beslissen of een tenant het mag gebruiken. Vandaag: partner_api, widgets, custom_mcps. | Platform-admin (Klai-staff) | `portal_orgs.platform_unlocked_features` |
| **Profiel** (`portal_users.role`) | Per-user permissie-niveau, 5-rung ladder. Bepaalt capabilities, product-toegang, kb-quota, admin-tab-zichtbaarheid. | Tenant-admin (binnen plan-ceiling) | `portal_users.role` |
| **Groepen** (`portal_groups`) | Content-scoping. Primair: per-team KB-toegang. Neventoepassing: meeting-scoping. Geen rol-binding, geen product-binding. | Tenant-admin / group_manager | `portal_groups` |

Regel: profielen voor "wat mag deze user doen", groepen voor "welke content ziet deze user", producten zijn altijd plan-of-addon (nooit per-user), platform-features zijn altijd Klai-controlled (nooit tenant-zelf-aan).

`is_group_admin` per-membership boolean blijft bestaan voor delegated meeting-write. Geen ladder-rol.

---

## Motivation

1. **Personal-rol user kan momenteel alle org-kennis bevragen.** REQ-1 van PROFILES-001 zit in code maar wordt nooit getriggerd: callers van `get_accessible_kb_slugs` geven `user_role=` niet door. Symptoom van: optionele kwarg in plaats van typed object dat altijd meereist.
2. **Frontend belofte zonder backend-dekking.** Group_manager ziet `/admin/groups` maar kan geen groep hernoemen — die endpoints staan op `_require_admin`. Verschillende plekken, verschillende gates.
3. **Twee bronnen van waarheid voor plan-products** (`plans.py::PLAN_PRODUCTS` en `features.py::PLAN_FEATURES`). Drift-risico permanent.
4. **Gates verspreid over vier plekken**, deels imperatief (`_require_admin(caller)`) en deels declaratief (`Depends(require_capability(...))`). Een nieuwe endpoint zonder de imperatieve aanroep heeft geen gate; CI vangt het niet.
5. **`/api/me` doet drie aparte DB-queries** + retourneert twee inconsistente capability-velden.
6. **MCP-laag is een blinde vlek.** Drie tools (`save_org_knowledge`, `save_to_docs`, `search_knowledge`) zonder rol-check. Een personal user via Claude Desktop of LibreChat kan org-data zien of schrijven.
7. **Drie features missen platform-controle.** Partner-API, chat-widgets, en catalog-MCP-activatie staan vandaag op tenant-admin-niveau open. Zouden door Klai-staff per-tenant aangezet moeten worden.

Deze zeven punten zijn één probleem: er is geen single permissions-resolver die overal heen reist, en er is geen platform-laag die boven tenant-admin staat. Symptomen.

---

## Scope — zes phases

### Phase 1: Architecturale consolidatie

**1A: Centrale permissions resolver.** Nieuw bestand `app/core/permissions.py`:

```python
@dataclass(frozen=True)
class UserPermissions:
    user_id: str
    org_id: int
    org_slug: str
    role: ProfileRole
    plan: str
    enabled_addons: frozenset[str]
    platform_unlocked_features: frozenset[str]
    effective_role: ProfileRole
    effective_capabilities: frozenset[Capability]
    effective_products: frozenset[str]
    effective_kb_limits: KBLimits
    is_platform_admin: bool

async def resolve_user_permissions(zitadel_user_id: str, db: AsyncSession) -> UserPermissions | None:
    """Single SELECT op portal_users + portal_orgs, plus pure derivation. RLS-safe."""
```

**1B: Plan-product bron consolideren.** `core/plans.py` wordt opgeruimd; alle `PLAN_PRODUCTS` / `ADDON_PRODUCTS` callers verhuizen naar `core/features.py::PLAN_FEATURES` / `ADDON_FEATURES`. Na deze stap returnt `git grep "PLAN_PRODUCTS\|ADDON_PRODUCTS"` nul matches.

**1C: ProfileRole als StrEnum.** Postgres enum aan DB-kant blijft; aan Python-kant `class ProfileRole(StrEnum)` in `core/profiles.py`. PortalUser-model `role: Mapped[ProfileRole]`.

**1D: Uniforme declaratieve gates.** `core/permissions.py` exposeert `get_caller(...)`, `get_caller_at_least(role)`, `require_product(p)`, `require_capability(c)`, `require_platform_unlocked(feature)`, `require_platform_admin()`. Alles `Depends(...)`. `_require_admin(caller_user)` (imperatief) wordt verwijderd.

**1E: Typed caller-injection.** Endpoint-signature wordt `perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN))`. Twee regels boilerplate verdwijnen per endpoint.

**1F: Test-fixtures.** Conftest-helper `make_user(role=...)` factory; `make_org(plan=..., enabled_addons=..., platform_unlocked_features=...)` factory.

### Phase 2: Endpoints overzetten — opgesplitst in 8 sub-PRs

Volgens domein-clusters (zie research.md Thread E voor file-inventaris):

| Sub-PR | Domein | Files | Endpoints | Risico |
|---|---|---|---|---|
| **2a** | Admin: users & core tenant | `admin/users.py`, `admin/__init__.py`, `admin/settings.py`, `admin/products.py`, `admin/retry_provisioning.py` | ~18-22 | Laag |
| **2b** | Admin: audit, provisioning | `admin/audit.py`, `admin/deprovision_org.py`, `admin/join_requests.py` | ~8-10 | Laag |
| **2c** | Admin: API keys & widgets | `admin_api_keys.py`, `admin_widgets.py` | 10 | Laag |
| **2d** | Billing | `billing.py` | 5 | Laag |
| **2e** | KB CRUD | `knowledge_bases.py`, `app_knowledge_sources.py`, `app_templates.py`, `knowledge.py` + CRUD-helft `app_knowledge_bases.py` | ~15-18 | Medium |
| **2f** | Taxonomy & advanced KB ops | `taxonomy.py` + advanced helft `app_knowledge_bases.py` | ~16-20 | Medium |
| **2g** | Connectors & MCPs | `connectors.py`, `mcp_servers.py`, `app_gaps.py` | 13 | Medium |
| **2h** | Groups & me-tokens | `groups.py`, `me_mcp_tokens.py` | ~15 | Medium |

Volgorde: 2a → 2b sequentieel; 2c+2d parallel; 2e → 2f sequentieel; 2g+2h parallel.

Inhoudelijk per sub-PR: `_require_admin` callers vervangen door `Depends(get_caller_at_least(ADMIN))`, `_get_caller_org` boilerplate vervangen door typed dependency, `get_effective_products`/`get_effective_capabilities` losse calls vervangen door `perms` object.

### Phase 3: Enforcement-gaps die dichtgaan

**3A: REQ-6 — personal hard gate op org-KB read.** Met `get_accessible_kb_slugs(perms, db)` wordt rol via object meegegeven. Twee callers (`app_knowledge_bases.py::list_kbs_with_access`, `knowledge.py:114`) ontvangen `perms` via `Depends`; rol kan niet vergeten worden.

**3B: REQ-7 — personal mag niet schrijven naar org-KB.** In `_get_writable_kb_or_raise`: `if kb.owner_type == "org" and perms.effective_role == ProfileRole.PERSONAL: raise 403 org_kb_write_requires_company`. Idem in `connectors.py::create_connector`.

**3C: REQ-8/9 — group_manager mag groepen beheren.** Volgt automatisch uit Phase 2h (gates uniform).

**3D: REQ-12/13 — plan-ceiling op rol-toekenning.** `ALLOWED_PROFILES_PER_PLAN` in `core/permissions.py`. `update_user_role`, `promote_admin`, `invite_user` valideren tegen `ALLOWED_PROFILES_PER_PLAN[org.plan]`.

### Phase 4: MCP-laag

**4A: `klai_identity_assert` JWT uitbreiden met `effective_role` claim.** Bestaande shared library krijgt extra veld in de signed assertion. Caller-services (LibreChat-bridge, portal-api) signen `{user_id, org_id, effective_role, exp}`. MCP-server verifieert + leest role direct. Geen DB-credentials in MCP-container, geen extra HTTP round-trip op het happy path.

**4B: Internal endpoint als fallback.** `GET /internal/users/{zitadel_user_id}/permissions` op portal-api retourneert serialized `UserPermissions`. Gebruikt door MCP wanneer JWT-claim ontbreekt of stale is (post-rotation).

**4C: Dubbele tool-gating in `klai-knowledge-mcp`.** Pattern matcht GitHub MCP:
- **`tools/list` filtering**: server registreert tools met een `min_role` annotation. Bij ListTools-request leest de server `effective_role` uit de assertion en filtert. Een personal user ziet `save_org_knowledge` en `save_to_docs` niet.
- **`tools/call` re-check**: bij elke tool-invocatie opnieuw rol-check. Defense-in-depth tegen stale tool-list, rebellious clients, of cache-issues.

**4D: `search_knowledge` propageert rol naar retrieval-api.** Body krijgt `effective_role: str` veld. `klai-retrieval-api` filtert slug-lijst zelfde regel als portal: personal → geen "org" slug, geen `default_org_role`-KBs.

**4E: `save_org_knowledge` en `save_to_docs` rol-gegate.**
- `save_org_knowledge`: `if perms.effective_role == PERSONAL: return _ERR_NOT_ALLOWED`
- `save_to_docs`: `if "docs" not in perms.effective_products: return _ERR_NOT_ALLOWED`

**4F: `notifications/tools/list_changed` implementeren.** Klai-knowledge-mcp emit een `listChanged`-notification wanneer een user's rol wijzigt mid-session. LibreChat-MCP-bridge (Klai-controlled) honoreert het en herlaadt de tool-list zonder reconnect. Third-party clients (Claude Desktop, ChatGPT-desktop): per MCP-spec moeten zij zelf `tools/list` opnieuw aanroepen — sommige doen dat automatisch op de notificatie, andere vereisen user-actie. Geen Klai-werk om die te ondersteunen.

**4G: Documentatie-regel.** Nieuwe rule of CLAUDE.md-sectie in `klai-knowledge-mcp/`: "Elke nieuwe tool MOET `min_role` annotation hebben en de gate door zowel `tools/list` filter als `tools/call` re-check honoreren."

### Phase 5: Platform-locked features

**5A: Schema-wijziging.** Eén Alembic-migration:

```sql
ALTER TABLE portal_orgs
ADD COLUMN platform_unlocked_features text[] NOT NULL DEFAULT '{}';
```

Alle bestaande tenants starten met `[]`. Geen grandfathering — geen actieve gebruikers met partner_api_keys; custom-MCP-feature kan nog niet door tenants gebruikt worden (alleen Klai-curated catalog), dus niets om te grandfatheren.

**5B: Helper en dependency.** In `core/permissions.py`:
```python
def require_platform_unlocked(feature: str):
    """Returns Depends-callable. 403 als feature niet in perms.platform_unlocked_features."""
```

**5C: Drie features achter de gate.**
- **Partner-API** (`partner.py`, `partner_dependencies.py`): bestaande `get_partner_key` dependency krijgt extra check. Body van helper begint met `assert_platform_unlocked(org, "partner_api")`. Bij een 403: `error_code=feature_not_unlocked, feature=partner_api`.
- **Chat-widgets** (`admin_widgets.py`): alle 6 widget-endpoints krijgen `Depends(require_platform_unlocked("widgets"))` naast `Depends(get_caller_at_least(ADMIN))`.
- **Custom MCPs** (`mcp_servers.py`): tenant-admin kan vandaag uit Klai-catalog selecteren, niet eigen URLs invoeren. De selectie-actie zelf (`PUT /api/mcp-servers/{id}` met `enabled=true`) wordt platform-gegate via `require_platform_unlocked("custom_mcps")`. Managed MCPs (waarbij `managed=true` op de catalog-entry) staan altijd beschikbaar — die zijn Klai-curated en altijd-aan voor alle tenants. Het is dus alleen tenant-keuze-uit-niet-managed-catalog die platform-locked is. Custom URLs: blijft uit scope tot een aparte SPEC die feature ontwerpt.

**5D: Platform-admin endpoints.**
```
GET  /api/admin/orgs/{slug}/platform-unlocks      # platform-admin
PATCH /api/admin/orgs/{slug}/platform-unlocks     # platform-admin
```
Beide gegate via `Depends(require_platform_admin())`. Audit via bestaande `tenant_lifecycle_events` (`actor_type='platform_admin'`).

**5E: Geen frontend-werk in deze SPEC.** Het Platform-admin UI komt in een follow-up SPEC; voor nu opereren Klai-staff via de API direct (zelfde patroon als voor `retry_provisioning` en `deprovision_org`).

### Phase 6: Cleanup

**6A: Stale comments** in `me.py:144` (self-heals) en `settings.py::update_addons` docstring (dormant entitlements) bijwerken naar RBAC-001 v0.2.0 gedrag.

**6B: `klai-portal/backend/scripts/create_default_groups.py` verwijderen.** Niet runtime, wel misleidend.

**6C: `me.py:98` `portal_role` default** "member" → "personal".

**6D: Documentatie.** Nieuwe rule `.claude/rules/klai/projects/portal-permissions.md` met:
- Conceptueel kader (Plan / Add-ons / Platform-features / Profiel / Groepen)
- Endpoint-template (`Depends(get_caller_at_least(...))`)
- Hoe een nieuw product toevoegen (alleen via `core/features.py`, FEATURE_MIN_PROFILE)
- Hoe een nieuwe gate toevoegen (in `core/permissions.py`)
- Hoe een nieuwe platform-locked feature toevoegen (kolom-update + helper + endpoint-decoratie)

### Out of scope

- Drop van `portal_user_products` / `portal_group_products` schema (RBAC-001 hield die voor toekomstige seat-billing).
- Wijziging aan PROFILE_LADDER, FEATURE_MIN_PROFILE, PLAN_FEATURES selecties (alleen consolidatie van bron, niet inhoud).
- KB-access mechaniek (`PortalUserKBAccess`, `default_org_role`).
- `is_group_admin` per-membership review.
- Definitief verwijderen van `capabilities` veld in MeResponse — alias-fase eerst, follow-up SPEC sluit het af.
- Frontend grijs-rendering audit.
- **Custom-MCP-URL-feature.** Tenant-admin kan vandaag alleen uit Klai-catalog selecteren. Eigen URLs invoeren is een aparte feature met een aparte risico-stack (tool-poisoning, SSRF, cross-server hijack — zie research.md Thread B). Komt in een eigen SPEC zodra die feature gevraagd wordt.
- **Platform-admin UI.** Klai-staff opereert via API tot een aparte SPEC die UI ontwerpt.
- **Grandfathering scripts.** Niet nodig — geen actieve productie-gebruikers behalve test-tenant Voys.

---

## Pre-phase: characterization tests

Vóór elke endpoint-rewrite (start van Phase 2a) moeten snapshot-tests gepind zijn voor de risico-set: endpoints die vandaag `_require_admin` gebruiken zonder bestaande rol-test. Volgens research.md Thread F:

- `admin_widgets.py` (6 widget-endpoints)
- `billing.py` (5 endpoints)
- `admin_api_keys.py` (5 endpoints)
- `mcp_servers.py` (4 endpoints)
- `groups.py` (gedeeltelijk — toggle_group_admin, update_group, delete_group)
- `app_gaps.py` (4 endpoints)
- `taxonomy.py` (admin endpoints)

Per endpoint: 200 voor admin, 403 voor company/personal, 401 voor unauthenticated. Template:

```python
@pytest.mark.asyncio
async def test_<endpoint>_role_matrix_snapshot(client, auth_admin, auth_company, auth_unauth):
    resp_admin = await client.get(URL, headers={"Authorization": f"Bearer {auth_admin.token}"})
    assert resp_admin.status_code == 200

    resp_company = await client.get(URL, headers={"Authorization": f"Bearer {auth_company.token}"})
    assert resp_company.status_code == 403

    resp_unauth = await client.get(URL)
    assert resp_unauth.status_code == 401
```

Deze snapshot-tests blijven ná de refactor staan — ze worden de regression-test voor de uniforme gate-laag.

---

## Requirements (EARS)

### Architectuur (Phase 1)

**REQ-1**: Het systeem SHALL één `resolve_user_permissions(zitadel_user_id, db)` functie hebben in `app/core/permissions.py` die in één SELECT-query een `UserPermissions` dataclass returnt.

**REQ-2**: `app/core/plans.py::PLAN_PRODUCTS` en `ADDON_PRODUCTS` SHALL niet meer in runtime-code voorkomen na deze SPEC. `git grep "PLAN_PRODUCTS\|ADDON_PRODUCTS" klai-portal/backend/app` SHALL nul matches returneren.

**REQ-3**: `portal_users.role` SHALL aan Python-kant typed zijn als `ProfileRole(StrEnum)`. Pyright-check SHALL fout geven bij vergelijking met een string die niet in de enum staat.

**REQ-4**: Alle admin/role-check endpoints in `klai-portal/backend/app/api/` SHALL hun gate via FastAPI `Depends(...)` in de signature declareren. `git grep "_require_admin(" klai-portal/backend/app/api` SHALL nul matches returneren.

**REQ-5**: `_get_caller_org` 3-tuple boilerplate SHALL vervangen zijn door `Depends(get_caller(...))` of `Depends(get_caller_at_least(...))` op alle endpoints.

### Personal hard gate en write-blokkering (Phase 3)

**REQ-6**: WHEN een user met `effective_role == PERSONAL` `/api/app/docs/with-access` of `/api/knowledge/*` aanroept, THEN het systeem SHALL "org" slug en `default_org_role`-KBs uit de slug-lijst filteren.

**REQ-7**: WHEN een user met `effective_role == PERSONAL` POST doet op `/api/app/knowledge-bases/{org-kb-slug}/sources/url`, `/sources/text`, of `/connectors`, THEN het systeem SHALL HTTP 403 returneren met `error_code=org_kb_write_requires_company`.

### Group manager (Phase 2h)

**REQ-8**: WHEN een user met `effective_role >= GROUP_MANAGER` `PATCH /api/admin/groups/{id}`, `DELETE /api/admin/groups/{id}`, of `PATCH /api/admin/groups/{id}/members/{uid}` aanroept binnen zijn eigen org, THEN het systeem SHALL de operatie accepteren.

**REQ-9**: WHEN een user met `effective_role < GROUP_MANAGER` één van die endpoints aanroept, THEN het systeem SHALL HTTP 403 returneren.

### /api/me harmoniseren (Phase 2)

**REQ-10**: `GET /api/me` SHALL `effective_capabilities` returneren uit `resolve_user_permissions(...).effective_capabilities`. Het bestaande `capabilities` veld SHALL exact dezelfde inhoud hebben (alias).

**REQ-11**: `MeResponse.portal_role` default SHALL `"personal"` zijn. Veld blijft één release als alias voor `effective_role`.

### Plan-ceiling (Phase 3D)

**REQ-12**: WHEN een admin `PATCH /api/admin/users/{id}/role`, `POST /api/admin/users/invite`, of `POST /api/admin/users/{id}/promote-admin` aanroept met een rol die niet in `ALLOWED_PROFILES_PER_PLAN[org.plan]` zit, THEN het systeem SHALL HTTP 403 returneren met `error_code=role_not_allowed_for_plan`.

**REQ-13**: `kb_manager` SHALL alleen toekenbaar zijn op orgs met `plan == "complete"`.

### MCP-laag (Phase 4)

**REQ-14**: De `klai_identity_assert` JWT SHALL een `effective_role` claim bevatten. Caller-services SHALL die claim signen in elke MCP-call.

**REQ-15**: `klai-knowledge-mcp::list_tools` SHALL de tool-set filteren op caller's `effective_role`. Een tool met `min_role >= COMPANY` SHALL niet zichtbaar zijn voor een `PERSONAL` user.

**REQ-16**: `klai-knowledge-mcp::call_tool` SHALL voor elke tool-invocatie de rol opnieuw verifiëren. Een rol-mismatch SHALL een tool-error returneren met betekenis `role_not_allowed_for_tool` zonder de tool uit te voeren.

**REQ-17**: WHEN een MCP-client `search_knowledge` aanroept, THEN `klai-knowledge-mcp` SHALL `effective_role` meesturen in de `/retrieve` body, en `klai-retrieval-api` SHALL die rol respecteren bij slug-filtering.

**REQ-18**: WHEN een user's rol wijzigt mid-session, THEN `klai-knowledge-mcp` SHALL een `notifications/tools/list_changed` emit zodat LibreChat-MCP-bridge de tool-list kan herladen zonder reconnect. Third-party clients (Claude Desktop, ChatGPT-desktop) handelen dit per MCP-spec af.

**REQ-19**: `GET /internal/users/{zitadel_user_id}/permissions` SHALL bestaan op portal-api als fallback voor MCP-server bij ontbrekende of stale JWT-claim. Auth via bestaand `_require_internal_token`.

### Platform-locked features (Phase 5)

**REQ-20**: Schema SHALL kolom `portal_orgs.platform_unlocked_features text[] NOT NULL DEFAULT '{}'` hebben. Alembic-migration is included in Phase 5.

**REQ-21**: `app/core/permissions.py` SHALL helper `require_platform_unlocked(feature: str)` exposeren als FastAPI-dependency die 403 returnt met `error_code=feature_not_unlocked, feature=<name>` wanneer het feature niet in `org.platform_unlocked_features` zit.

**REQ-22**: Alle endpoints onder `/partner/v1/*` SHALL `partner_api ∈ org.platform_unlocked_features` checken. Een tenant zonder die unlock SHALL 403 krijgen, ongeacht of er een geldige `partner_api_key` bestaat.

**REQ-23**: Alle widget endpoints (`POST/GET/PATCH/DELETE /api/admin/widgets[/{id}]`) SHALL `widgets ∈ org.platform_unlocked_features` checken bovenop de admin-gate.

**REQ-24**: `PUT /api/mcp-servers/{server_id}` met `enabled=true` op een non-managed catalog-entry SHALL `custom_mcps ∈ org.platform_unlocked_features` checken. Managed catalog-entries (`managed=true`) zijn altijd beschikbaar en bypassen deze check.

**REQ-25**: Twee platform-admin endpoints SHALL bestaan: `GET /api/admin/orgs/{slug}/platform-unlocks` en `PATCH /api/admin/orgs/{slug}/platform-unlocks`. Beide gegate via `require_platform_admin()`. Audit via `tenant_lifecycle_events` met `actor_type='platform_admin'`.

**REQ-26**: Nieuwe tenants SHALL `platform_unlocked_features=[]` hebben. Geen automatische unlocks per plan-tier.

### Cleanup (Phase 6)

**REQ-27**: `klai-portal/backend/scripts/create_default_groups.py` SHALL verwijderd zijn.

**REQ-28**: Stale comments in `me.py:144` en `settings.py::update_addons` SHALL bijgewerkt zijn.

**REQ-29**: Een rule `.claude/rules/klai/projects/portal-permissions.md` SHALL bestaan met conceptueel kader, endpoint-template, en uitbreiding-regels.

---

## Acceptance Criteria

**AC-1**: `resolve_user_permissions("user_id", db)` returnt een `UserPermissions` met alle 12 velden gevuld in één DB-query (verifieerbaar via SQLAlchemy event-listener counter in test).

**AC-2**: `git grep -E "PLAN_PRODUCTS|ADDON_PRODUCTS" klai-portal/backend/app` returnt nul matches.

**AC-3**: `git grep "_require_admin(" klai-portal/backend/app/api` returnt nul matches.

**AC-4**: `git grep "_, org, caller_user = await _get_caller_org" klai-portal/backend/app/api` returnt nul matches.

**AC-5**: Pytest `test_personal_hard_gate_e2e.py`: personal-rol user op tenant met org-KB die `default_org_role=viewer` heeft. `GET /api/app/docs/with-access` returnt geen org-KB. Upgrade naar company → org-KB verschijnt.

**AC-6**: Pytest `test_personal_org_kb_write_blocked.py`: personal user `POST /sources/url` op org-KB met `default_org_role=contributor` → 403 `org_kb_write_requires_company`. Personal user op eigen `personal-{uid}` KB → 200.

**AC-7**: Pytest `test_group_manager_can_manage_groups.py`: group_manager `PATCH/DELETE /admin/groups/{id}`, `PATCH /admin/groups/{id}/members/{uid}` → 200/204. Personal/company op zelfde endpoints → 403.

**AC-8**: `GET /api/me` returnt `effective_capabilities` met `get_effective_capabilities`-waarde. `capabilities` veld heeft exact dezelfde inhoud (alias). Voor `personal` op `free`: `[]`. Voor `personal` op `complete`: `["kb.connectors"]`. Voor `admin` op `core`: alle complete-tier capabilities.

**AC-9**: `PATCH /api/admin/users/{id}/role` met `kb_manager` op `core`-plan → 403 `role_not_allowed_for_plan`. Op `complete`-plan → 200.

**AC-10**: MCP-test (uitbreiding van `klai-knowledge-mcp/tests/`): personal user via MCP-token doet `tools/list` → response bevat geen `save_org_knowledge` of `save_to_docs`. Company user op tenant zonder docs in `enabled_addons`: `tools/list` bevat `save_org_knowledge` maar niet `save_to_docs`.

**AC-11**: MCP-test: personal user die ondanks niet-zichtbaar `save_org_knowledge` toch `tools/call` poogt → response is tool-error met `role_not_allowed_for_tool`.

**AC-12**: MCP-test: personal user via MCP-token doet `search_knowledge` op tenant met org-KB → response bevat geen `scope=org` chunks.

**AC-13**: Platform-test: een tenant zonder `partner_api` in `platform_unlocked_features` doet call op `GET /partner/v1/knowledge-bases` met geldige `partner_api_key` → 403 `feature_not_unlocked, feature=partner_api`. Platform-admin doet `PATCH /api/admin/orgs/voys/platform-unlocks` met `{add: ["partner_api"]}` → 200. Nu werkt de partner-API call wel.

**AC-14**: Platform-test: tenant-admin probeert `POST /api/admin/widgets` op tenant zonder `widgets` in `platform_unlocked_features` → 403. Klai-staff unlockt `widgets` → admin call werkt.

**AC-15**: Platform-test: tenant-admin probeert `PUT /api/mcp-servers/{non_managed_id}` met `enabled=true` op tenant zonder `custom_mcps` unlock → 403. Managed catalog-entry wordt altijd geaccepteerd, ongeacht unlock-status.

**AC-16**: `git grep "self-heals" klai-portal/backend/app/api/me.py` returnt nul. `git grep "dormant" klai-portal/backend/app/api/admin/settings.py` returnt nul.

**AC-17**: `git grep "PortalUserProduct\|PortalGroupProduct" klai-portal/backend` returnt alleen `app/models/products.py`, `app/models/groups.py`, en alembic-history.

**AC-18**: Volledige backend testsuite groen (klai-portal + klai-knowledge-mcp + klai-retrieval-api).

**AC-19**: Op de Voys-tenant na deploy:
- Test-user met profiel `personal` in `/app/chat` ziet alleen eigen KB; geen org-KB.
- Test-user met profiel `personal` via Claude Desktop met MCP-token doet `search_knowledge` → krijgt alleen personal chunks terug; ziet `save_org_knowledge` niet in tool-list.
- Test-user met profiel `group_manager` kan in `/admin/groups` rename / delete / toggle is_group_admin.
- Promote naar `kb_manager` op core-plan → UI toont 403 toast.
- `GET /partner/v1/knowledge-bases` met geldige key → 403 zolang `partner_api` niet in unlocks staat.

**AC-20**: De rule `.claude/rules/klai/projects/portal-permissions.md` documenteert het volledige conceptueel kader (Plan / Add-ons / Platform-features / Profiel / Groepen) plus endpoint-template plus uitbreiding-regels voor nieuwe products / gates / platform-features.

---

## Migration & deploy

Eén Alembic-migration (Phase 5: kolom toevoegen). Geen post-deploy SQL.

| Phase | Wat | PR-grootte | Afhankelijkheden |
|---|---|---|---|
| Pre | Characterization-tests voor risico-set | klein (~250 LOC tests) | geen |
| 1 | Core: resolver + plans-consolidatie + StrEnum + uniforme gates + typed deps | groot (~600 LOC) | Pre |
| 2a | Admin: users & core tenant | medium | Phase 1 |
| 2b | Admin: audit, provisioning | klein | Phase 1 |
| 2c | Admin: API keys & widgets | medium | Phase 1 |
| 2d | Billing | klein | Phase 1 |
| 2e | KB CRUD | medium | Phase 1 |
| 2f | Taxonomy & advanced KB ops | medium | Phase 2e |
| 2g | Connectors & MCPs | medium | Phase 1 |
| 2h | Groups & me-tokens | medium | Phase 1 |
| 3 | Enforcement gaps + plan-ceiling | klein (~200 LOC, vooral tests) | Phase 2 |
| 4 | MCP-laag + retrieval-api filter + listChanged | medium (~400 LOC, cross-repo) | Phase 1 |
| 5 | Platform-locked features (kolom + helper + drie features + admin endpoints) | medium (~300 LOC + Alembic) | Phase 1 |
| 6 | Cleanup + documentatie | klein (~50 LOC + docs) | Phase 1 |

Volgorde: Pre → 1 → (2a → 2b sequentieel; 2c+2d parallel; 2e → 2f sequentieel; 2g+2h parallel) → 3 → 4 (cross-repo) parallel met 5 → 6.

Doorlooptijd: ongeveer 5-6 weken bij 1 PR/week + paralleliseerbare paren.

Rollback per phase: `git revert` van de phase-PR. Phase 5 heeft Alembic-down-migration die de kolom DROP't (data verlies = `[]` op alle tenants, acceptabel want geen actieve gebruikers).

Deploy-discipline: na elke phase `gh run watch --exit-status`. Verificatie van phase-specifieke AC's op de Voys-tenant via Playwright MCP en (voor Phase 4) via Claude Desktop met test-MCP-token.

---

## Notes for reviewer

Dit is een refactor die de architectuur op industriestandaard zet en de huidige bugs als bijproduct dichtgooit, plus drie features platform-locked maakt. De volgorde — eerst characterization tests, dan architectuur, dan endpoints, dan enforcement, dan MCP, dan platform-features — is bewust: omgekeerd zou betekenen "eerst REQ-6 fixen door een optionele kwarg door te geven, daarna pas de central resolver bouwen". Dat is dubbel werk.

Phase 1 is het zwaarst. Begin daar pas als er geen andere portal-PRs in flight zijn waarmee het kan conflicten.

Geen actieve productie-gebruikers behalve test-tenant Voys. Dat geeft veel speelruimte: breaking changes acceptabel, geen langzame rollout nodig, geen grandfathering-scripts. Maar de Voys-test-flow MOET groen blijven — die is de regression-canary.

`is_group_admin` per-membership boolean blijft expliciet bestaan. Geen ladder-rol.

Custom-MCP-URLs (tenant geeft eigen MCP-server URL op) is uit scope. Zodra die feature aan de orde is: aparte SPEC met de risico-stack uit `research.md` Thread B (tool-poisoning, SSRF, cross-server hijack, exfiltratie via output, pre-use exfil via OAuth). Klai's huidige model — alleen catalog-selectie — vermijdt het hele attack-surface tot we expliciet besluiten het toe te laten.
