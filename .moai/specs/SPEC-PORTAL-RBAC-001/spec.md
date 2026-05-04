---
id: SPEC-PORTAL-RBAC-001
version: "0.2.0"
status: ready-for-run
created: 2026-05-04
updated: 2026-05-04
author: Mark Vletter
priority: high
supersedes:
  - SPEC-PORTAL-PROFILES-001 Phase 2 (assignment model: portal_user_products / portal_group_products / addon_* system groups)
  - SPEC-PORTAL-PROFILES-001 Phase 3 (admin UI for per-user / per-group product assignment)
related:
  - SPEC-PORTAL-PROFILES-001 (5-rung profile ladder + PROFILE_CAPABILITIES — KEPT)
  - SPEC-PORTAL-UNIFY-KB-001 (PLAN_LIMITS + capabilities — KEPT)
  - SPEC-AUTH-008 (group memberships — KEPT, scope narrowed to KB-access + meeting-write via is_group_admin)
---

# SPEC-PORTAL-RBAC-001: Industry-standard product gating

## HISTORY

| Date | Version | Change |
|------|---------|--------|
| 2026-05-04 | 0.1.0 | Initial draft. |
| 2026-05-04 | 0.2.0 | Sparring resolved with Mark. All open questions answered. Ready for /moai run. |

### Sparring decisions (v0.2.0)

| # | Vraag | Keuze |
|---|---|---|
| 1 | Drempel voor Scribe/Docs | Vanaf `company` (Personal chat krijgt ze niet) |
| 2 | Per-tenant override van drempel | Nee. Drempel is vast voor alle tenants. |
| 3 | PR #291 reverten of laten | Laten staan. Wordt dode code bij merge van deze SPEC. |
| 4 | Naming products vs features | Blijft "products" overal. |
| 5 | Cleanup-diepte | Optie C: direct opruimen bij deploy. Geen latente tabellen. |
| 6 | KB `default_org_role` gedrag | Niet aanraken. Buiten scope. |

---

## Summary

Klai's product-gating heeft drie overlappende mechanismen: per-user product entitlements (`portal_user_products`), per-group product entitlements (`portal_group_products`) gebonden aan zeven systeem-groepen, en een 5-rung profile ladder. Dat is twee mechanismen te veel — Linear / Notion / Slack / GitHub / Auth0 hebben universeel geen per-user feature flags. Plan = workspace features. Rol = permissies binnen die features. Groepen = content-scoping.

Deze SPEC vervangt de products-laag door één profiel-gestuurde derivation. De RBAC-laag eronder (`PROFILE_CAPABILITIES`, `PROFILE_LIMITS`, `require_capability`, `_require_at_least`, `effective_kb_limits`) is al industriestandaard en blijft staan. KB-access (`PortalUserKBAccess`, `PortalGroupKBAccess`, `default_org_role`) is onafhankelijk en blijft staan. `is_group_admin` per-membership voor meeting-write-rechten is onafhankelijk en blijft staan.

```
USER FEATURES = PLAN_FEATURES[org.plan] ∪ { addon for addon in org.enabled_addons
                                            if PROFILE_RANK[user.role] >= PROFILE_RANK[FEATURE_MIN_PROFILE[addon]] }
```

Eén afleiding. Geen rijen meer in `portal_user_products` of `portal_group_products`. Geen `addon_*` of `role_*` system groups in de database. Eén `_require_at_least` helper voor ladder-gating. Eén `require_product` van vijf regels. `/admin/groups` is leeg tot de admin zelf een custom KB-toegang-groep aanmaakt.

---

## Motivation

1. **UX-defect.** Toggle voor Scribe/Docs op `/admin/settings` doet niets zichtbaars zonder vervolg-actie die nergens wordt aangewezen. Mark stuitte hier zelf op.
2. **Code-complexity.** 5 `_require_admin_or_*` varianten. 80-regelige `require_product` met inconsistente admin-bypass. `get_effective_products` met "self-healing tenant context" als symptoom van de RLS-ordering-puzzel die er niet had hoeven zijn. Twee tabellen die alleen schrijvers en geen lezers hebben buiten zichzelf.
3. **Industriemismatch.** Per-user feature flags bestaan niet als pattern in B2B SaaS. Als we ooit per-seat-billing willen wordt het een nieuwe SPEC met expliciete reden — niet een vergissing die we per ongeluk al hadden gebouwd.

---

## Scope

### In scope

**Backend — derivation**

- Nieuw bestand `klai-portal/backend/app/core/features.py`:
  ```py
  PLAN_FEATURES: dict[str, frozenset[str]] = {
      "free":         frozenset(),
      "core":         frozenset({"chat", "knowledge"}),
      "professional": frozenset({"chat", "knowledge"}),
      "complete":     frozenset({"chat", "knowledge"}),
  }
  ADDON_FEATURES: frozenset[str] = frozenset({"scribe", "docs"})
  FEATURE_MIN_PROFILE: dict[str, str] = {
      "chat": "personal", "knowledge": "personal",
      "scribe": "company", "docs": "company",
  }

  def derive_user_products(role: str, plan: str, enabled_addons: list[str]) -> set[str]:
      plan_features = set(PLAN_FEATURES.get(plan, frozenset()))
      caller_rank = PROFILE_RANK.get(role, -1)
      addon_features = {
          addon for addon in enabled_addons
          if addon in ADDON_FEATURES
          and caller_rank >= PROFILE_RANK[FEATURE_MIN_PROFILE[addon]]
      }
      return plan_features | addon_features
  ```
  Pure functie. Single source of truth. Term blijft "products" — alleen interne helper-naam is `derive_user_products`.

- Herschrijf `klai-portal/backend/app/services/entitlements.py::get_effective_products` naar één query:
  ```py
  async def get_effective_products(zitadel_user_id: str, db: AsyncSession) -> list[str]:
      row = (await db.execute(
          select(PortalUser.role, PortalOrg.plan, PortalOrg.enabled_addons)
          .join(PortalOrg, PortalOrg.id == PortalUser.org_id)
          .where(PortalUser.zitadel_user_id == zitadel_user_id)
      )).one_or_none()
      if row is None:
          return []
      role, plan, enabled_addons = row
      return sorted(derive_user_products(role, plan, enabled_addons or []))
  ```
  Geen UNION. Geen self-healing tenant context (alleen permissive `portal_users` + `portal_orgs` worden gelezen — RLS-safe).

- Herschrijf `klai-portal/backend/app/api/dependencies.py::require_product` naar single-layer check:
  ```py
  def require_product(product: str):
      async def dep(user_id: str = Depends(get_current_user_id),
                    db: AsyncSession = Depends(get_db)) -> None:
          if product not in await get_effective_products(user_id, db):
              raise HTTPException(403, f"Product not available: {product}")
      return dep
  ```
  Geen admin-bypass — admin staat altijd boven `FEATURE_MIN_PROFILE[addon]` en hoeft dus structureel niet te bypassen. Geen ADDON_PRODUCTS-special-case.

- Verwijder uit `dependencies.py`:
  - `_require_admin_or_group_admin_role`
  - `_require_admin_or_group_admin`
  - `_require_admin_or_group_manager`
  - de `group_management` system_key-fallback (dode code: niet in `SYSTEM_GROUPS`)

  Vervang elke aanroep door `_require_at_least(role)` (uit `app/core/profiles.py`).

**Backend — writes verwijderen**

- `app/api/admin/users.py::invite_user`: verwijder de `PortalUserProduct(...)` writes voor plan products (regel 246-256). Plan products worden afgeleid.
- `app/api/admin/users.py::offboard_user`: verwijder `delete(PortalUserProduct).where(...)` (regel 514-520). Niets om te verwijderen.
- `app/api/admin/settings.py::change_plan`: verwijder de PortalUserProduct + PortalGroupProduct delete-loops bij downgrade (regel 100-130). Onnodig — afleiding doet het.
- `app/api/admin/settings.py::update_addons`: stript alle side-effects op groep-membership of group-products. Toggle is enige state-verandering. Audit-log + emit_event blijven.
- `app/services/system_groups.py`: verwijder `sync_role_from_system_group` aanroep in `groups.py::add_member`. Verwijder ook de helper zelf.

**Backend — endpoints op 410**

Deze endpoints corresponderen niet meer met state die het systeem leest. Direct 410 (Mark koos optie C: breken in development is OK):

- `POST /api/admin/users/{id}/products` → 410 Gone
- `DELETE /api/admin/users/{id}/products/{product}` → 410 Gone
- `GET /api/admin/users/{id}/products` → 410 Gone
- `GET /api/admin/users/{id}/effective-products` → blijft, lees uit `get_effective_products` (was al zo)
- `POST /api/admin/groups/{id}/products` → 410 Gone
- `DELETE /api/admin/groups/{id}/products/{product}` → 410 Gone
- `GET /api/admin/groups/{id}/products` → 410 Gone
- `GET /api/admin/products` → blijft, returnt `derive_user_products(caller.role, org.plan, org.enabled_addons)` (zelfde shape, andere afleiding — frontend `/admin/groups` blijft werken tot frontend-PR ook landt)
- `GET /api/admin/products/summary` → 410 Gone

Body voor 410's: `{"detail": "Endpoint removed by SPEC-PORTAL-RBAC-001. Products now derive from /admin/settings (plan + add-ons) and /admin/users/<id>/edit (profile)."}`

**Backend — system groups + DB cleanup migration**

`app/core/system_groups.py` wordt:
```py
SYSTEM_GROUPS: list[dict] = []  # geen system groups meer
```
en het hele bestand kan blijven bestaan voor `create_default_groups(org_id)` die straks een no-op is (handhaaft het API-contract met `provisioning/state_machine.py` zonder gedrag).

Alembic migration `xxx_drop_rbac_v1_data.py` (Mark koos optie C, direct opruimen):
```sql
TRUNCATE TABLE portal_user_products CASCADE;
TRUNCATE TABLE portal_group_products CASCADE;
DELETE FROM portal_group_memberships
 WHERE group_id IN (SELECT id FROM portal_groups WHERE system_key IS NOT NULL);
DELETE FROM portal_groups WHERE system_key IS NOT NULL;
```
Tabellen `portal_user_products` en `portal_group_products` BLIJVEN bestaan in het schema voor toekomstige seat-billing-SPEC. Alleen leeg getruncate. De `system_key` kolom blijft op `portal_groups` zodat we toekomstige system groups (bv. een `default` voor "iedereen") later kunnen introduceren zonder schema-wijziging.

**Backend — verwijderingen**

Verwijder volledig:
- `app/api/admin/products.py` — `assign_product`, `revoke_product`, `get_user_products`, `product_summary` worden 410 stub. Helper code voor de tabel-queries weg.
- `app/api/groups.py::assign_group_product`, `revoke_group_product`, `list_group_products` — 410.
- `app/api/groups.py::list_groups` en `get_group`: verwijder de `products` veld in de response. (Frontend leest dit niet meer na frontend-deel.)
- `app/services/system_groups.py::sync_role_from_system_group` — weg.

**Frontend**

- `klai-portal/frontend/src/routes/admin/groups/index.tsx`: na de DB-cleanup zijn er geen system groups meer; de bestaande lijst toont automatisch alleen custom groepen. Veiligheidsnet: filter `g.system_key == null` blijft zichtbaar in de code zodat een toekomstige system-group (mocht die ooit terugkomen) niet per ongeluk in de lijst belandt. Empty-state copy: "Nog geen groepen — maak een groep aan om kennisbank-toegang per team te regelen."
- `klai-portal/frontend/src/routes/admin/groups/$groupId/index.tsx`: verwijder de Products-sectie volledig (~120 regels). Members-sectie blijft.
- `klai-portal/frontend/src/routes/admin/groups/new.tsx` (of waar group-create staat): geen Product-veld op de aanmaak-form.
- `klai-portal/frontend/src/routes/admin/users/$userId/edit.tsx`: groepen-dropdown filtert al impliciet op alle groepen (na DB-cleanup zijn dat alleen custom groepen). Geen wijziging nodig.
- `klai-portal/frontend/src/routes/admin/settings.tsx::addonsMutation::onSuccess`:
  ```ts
  queryClient.setQueryData(['admin-enabled-addons'], { enabled_addons: next })
  ```
  fixt de Save-button-blijft-actief bug.
- `klai-portal/frontend/src/routes/app/route.tsx::PRODUCT_ROUTES`: ongewijzigd.

**Tests**

Toevoegen:
- `tests/test_features_derive.py` — parametrized matrix `(plan, role, enabled_addons) -> expected_set`. ~30 cases.
- `tests/test_addon_threshold.py` — Personal chat + scribe enabled → geen scribe. Company chat + scribe enabled → wel scribe. Admin + scribe enabled → wel scribe. Toggle uit → niemand.
- `tests/test_require_product_v2.py` — single-layer, geen admin-bypass-special-case.

Verwijderen:
- `tests/test_addon_gating.py` (de tweelaags-test wordt overbodig)
- Per-user/group product-assignment tests in `test_products.py`

Aanpassen:
- `tests/test_admin_addons.py` — drop de PR #291 assignment-side tests (worden 410-tests)
- `tests/test_entitlements_self_heal.py` — herschrijf naar single-query-pattern. "self-healing" deel verdwijnt.
- `tests/test_entitlements_rls.py` — single-query, RLS-safe.
- `tests/test_admin_users.py::test_invite_user_*` — verwijder PortalUserProduct-add asserts.
- `tests/test_products.py` — drop tests voor de 410-endpoints; behoud alleen tests voor `PLAN_PRODUCTS`/`get_plan_products`-helpers (overlapt met nieuwe `derive_user_products` test).

### Out of scope

- Per-seat billing voor add-ons (toekomstige SPEC, kan `portal_user_products` revivaliseren)
- Custom-group-bound features
- KB-access mechaniek (`PortalUserKBAccess`, `PortalGroupKBAccess`, `default_org_role`) — werkt prima
- Aanpassingen aan `PROFILE_CAPABILITIES`, `PROFILE_LIMITS`, `require_capability`, `effective_kb_limits`, `check_connector_allowed` — zijn al industriestandaard
- 5-rung profile ladder zelf
- `is_group_admin` per-membership boolean — apart concept, blijft
- Naming-rename van "products" naar "features" (Mark koos optie A)
- Per-tenant override van `FEATURE_MIN_PROFILE` (Mark koos optie A)

---

## Requirements (EARS)

**REQ-1**: WHEN een admin een add-on aanvinkt op `/admin/settings` THEN het systeem SHALL de add-on direct beschikbaar maken voor alle gebruikers in die org met `PROFILE_RANK[role] >= PROFILE_RANK[FEATURE_MIN_PROFILE[addon]]`, zonder verdere toewijzing.

**REQ-2**: WHEN een admin een add-on uitvinkt op `/admin/settings` THEN het systeem SHALL de add-on onmiddellijk verwijderen uit `get_effective_products` voor elke gebruiker in die org.

**REQ-3**: WHEN `get_effective_products(zitadel_user_id)` wordt aangeroepen THEN het systeem SHALL het resultaat puur afleiden uit `(portal_users.role, portal_orgs.plan, portal_orgs.enabled_addons)` via één SELECT-query, zonder lees op `portal_user_products` of `portal_group_products`.

**REQ-4**: WHEN `require_product(product)` wordt gebruikt als FastAPI dependency THEN het systeem SHALL toegang geven dan en alleen dan als `product in get_effective_products(user_id)`. Geen admin-bypass.

**REQ-5**: WHEN een admin `/admin/groups` opent THEN het systeem SHALL alleen groepen met `system_key IS NULL` tonen. Op een verse tenant is de lijst leeg.

**REQ-6**: WHEN een admin `/admin/users/<id>/edit` opent THEN de groepen-dropdown SHALL alleen custom groepen tonen.

**REQ-7**: WHEN een admin een gebruiker's profiel wijzigt via de profile-picker THEN het systeem SHALL `portal_users.role` direct schrijven via `PATCH /api/admin/users/<id>/role`. `sync_role_from_system_group` SHALL niet meer bestaan.

**REQ-8**: WHEN een admin een custom groep aanmaakt of bewerkt THEN het systeem SHALL geen Product-veld tonen op het formulier.

**REQ-9**: WHEN een aanroep komt op een verwijderd assignment-endpoint (POST/DELETE/GET op `/api/admin/users/{id}/products`, `/api/admin/groups/{id}/products`, `/api/admin/products/summary`) THEN het systeem SHALL HTTP 410 Gone returneren met een body die naar de nieuwe flow verwijst.

**REQ-10**: WHEN de migratie deployt THEN het systeem SHALL `portal_user_products` en `portal_group_products` truncaten, en SHALL alle rijen verwijderen uit `portal_groups` en `portal_group_memberships` waar `system_key IS NOT NULL`.

**REQ-11**: WHEN een admin de add-on toggles opslaat THEN de React Query cache voor `['admin-enabled-addons']` SHALL geüpdatet worden zodat de Save-knop direct disabled raakt.

---

## Acceptance Criteria

**AC-1**: Admin vinkt Scribe aan op `/admin/settings`, refresh `/app`, ziet Transcribe in de sidebar — zonder `/admin/groups` te bezoeken, zonder per-user/per-group actie.

**AC-2**: Admin demoot een gebruiker naar Personal chat, refresh `/app` voor die gebruiker, Transcribe verdwijnt uit sidebar (FEATURE_MIN_PROFILE["scribe"] = "company").

**AC-3**: Admin vinkt Scribe uit op `/admin/settings`, refresh `/app` voor elke gebruiker, Transcribe verdwijnt uit alle sidebars.

**AC-4**: Op een verse tenant toont `/admin/groups` een empty state. Geen "Personal chat" / "Scribe users" / etc. in de lijst.

**AC-5**: Group create-form heeft alleen naam en (optioneel) beschrijving + leden. Geen Product-veld.

**AC-6**: Een gebruiker met legacy `portal_user_products` rij voor "scribe" maar role = "personal" en tenant heeft scribe enabled, heeft GEEN scribe-toegang. (Na de truncate is dit moot, maar de assertie geldt structureel.)

**AC-7**: `git grep "_require_admin_or_"` in `klai-portal/backend/app/api/dependencies.py` returnt nul matches.

**AC-8**: `get_effective_products` is ≤ 15 regels Python en doet één DB-query.

**AC-9**: Aanroep van een 410-endpoint returnt status 410 + de afgesproken JSON body.

**AC-10**: Na deploy is `SELECT count(*) FROM portal_user_products` = 0 en `SELECT count(*) FROM portal_group_products` = 0 en `SELECT count(*) FROM portal_groups WHERE system_key IS NOT NULL` = 0.

**AC-11**: Save-knop op `/admin/settings` (Add-ons sectie) raakt disabled na opslaan zolang er niets opnieuw wijzigt.

**AC-12**: Volledige backend testsuite groen (≥1810 tests passing) na test-aanpassingen.

---

## Migration & deploy

Eén PR, één deploy, één Alembic migration. Geen dual-read-flag, geen verificatie-window — Mark gaf groen voor "breken in productie is OK in dit ontwikkelstadium".

Volgorde binnen de PR:
1. Code: `app/core/features.py` toevoegen + nieuwe `derive_user_products`.
2. Code: `get_effective_products` herschrijven.
3. Code: `require_product` herschrijven, helpers verwijderen, call-sites bijwerken.
4. Code: 410-stubs op de assignment-endpoints. Writes verwijderen uit invite/offboard/change_plan/update_addons.
5. Code: `system_groups.py` SYSTEM_GROUPS leegmaken, `sync_role_from_system_group` weg.
6. Migration: TRUNCATE + DELETE.
7. Frontend: groups-page filter, products-section weg, settings cache-invalidate.
8. Tests: nieuwe + aanpassen + verwijderen.

Rollback = `git revert` van de PR + `alembic downgrade -1` (de DELETE/TRUNCATE migration is oneway — een rollback maakt de tabellen leeg laten staan; data is weg). Mark accepteert dit risico expliciet.

---

## Estimated effort

- **Backend**: ~600 LOC verwijderd, ~80 LOC toegevoegd. Net negatief ~520 LOC.
- **Frontend**: ~150 LOC verwijderd, ~10 LOC toegevoegd. Net negatief ~140 LOC.
- **Tests**: ~300 LOC verwijderd of aangepast, ~150 LOC toegevoegd. Net negatief ~150 LOC.
- **Calendar time**: 1 werkdag voor implementatie + tests + lokale verificatie. CI deploy is dan een uur.

---

## Definition of done

- Alle REQ-1 t/m REQ-11 geïmplementeerd, gedekt door tests.
- Alle AC-1 t/m AC-12 verifieerbaar in CI of na deploy.
- `git grep "_require_admin_or_"` in `dependencies.py` returnt nul.
- `git grep "PortalUserProduct\|PortalGroupProduct"` returnt alleen het model-bestand en eventuele Alembic-history. Geen runtime callers.
- Op de Voys-tenant (Mark's account) na deploy:
  - Toggle Scribe aan op `/admin/settings` → Transcribe in sidebar.
  - Profile demote naar Personal chat → Transcribe weg.
  - Toggle Scribe uit → Transcribe weg.
  - `/admin/groups` is leeg (Mark heeft geen custom groepen aangemaakt).
- Volledige backend test suite groen.
- PR description bevat: link naar deze SPEC, screenshot van `/admin/groups` (leeg), screenshot van `/admin/settings` (toggle werkt direct), VictoriaLogs bevestiging dat er geen 500's of onverwachte 403's zijn na deploy.
