# SPEC-AUTH-004: Group-Based Product Entitlements -- Implementation Plan

**SPEC ID:** SPEC-AUTH-004
**Status:** Draft
**Priority:** High
**Dependencies:** SPEC-AUTH-001 (groepen + lidmaatschap), SPEC-AUTH-002 (product entitlements, plan-plafond)
**Dependents:** Geen

---

## Implementation Strategy

### Approach

Het groepsgebaseerde entitlement-systeem wordt in vier fasen gebouwd: (1) database model en migratie, (2) entitlement-resolutie refactor die directe en groepsgebaseerde toewijzingen samenvoegt, (3) frontend groep-productbeheer, (4) frontend gebruiker-edit aanpassing en standaardgroepen bij org-aanmaak. De bestaande per-user toewijzingen blijven functioneel (dual-mode) om backward compatibility te garanderen.

### Architecture Design Direction

- **Dual-mode entitlements:** Beide bronnen (direct + groep) blijven actief. De `get_user_effective_products()` functie is de single source of truth.
- **Denormalisatie van `org_id`:** In `portal_group_products` om efficiiente queries per organisatie mogelijk te maken zonder extra joins.
- **Plan-plafond op groepsniveau:** Dezelfde `PLAN_PRODUCTS` constante wordt hergebruikt. Groepsproduct-toewijzing wordt geweigerd als het product niet in het plan zit.
- **Geen cascading invalidation:** JWT tokens verlopen natuurlijk (~15 min). Na groepswijziging krijgt de gebruiker bij de volgende token-refresh de nieuwe claims.

---

## Milestones

### Phase 1: Backend -- Model + Migratie + Endpoints

**Priority:** Primary Goal

**Deliverables:**
- SQLAlchemy model `PortalGroupProduct` in `app/models/groups.py`
- Alembic migratie voor `portal_group_products` tabel (geen backfill)
- Drie nieuwe endpoints in `app/api/groups.py`:
  - `GET /api/admin/groups/{group_id}/products`
  - `POST /api/admin/groups/{group_id}/products`
  - `DELETE /api/admin/groups/{group_id}/products/{product}`

**Tasks:**
1. Voeg `PortalGroupProduct` model toe aan `app/models/groups.py` -- volg het patroon van `PortalUserProduct` in `app/models/products.py`
2. Genereer Alembic migratie met schema uit S1 (UUID pk, group_id FK CASCADE, org_id FK, product VARCHAR(32), enabled_at, enabled_by, unique constraint, indexes)
3. Implementeer `GET /api/admin/groups/{group_id}/products` -- query `portal_group_products` WHERE group_id, return lijst
4. Implementeer `POST /api/admin/groups/{group_id}/products` -- valideer plan-plafond via `PLAN_PRODUCTS[org.plan]`, controleer unieke constraint, maak record aan
5. Implementeer `DELETE /api/admin/groups/{group_id}/products/{product}` -- verwijder record, return 204
6. Voeg autorisatie toe: alleen org admin (niet group admin) mag producten toewijzen/intrekken
7. Unit tests voor alle drie endpoints inclusief plan-plafond enforcement

**Reference implementations:**
- `klai-portal/backend/app/models/products.py` -- PortalUserProduct model patroon
- `klai-portal/backend/app/api/groups.py` -- bestaande groep endpoint structuur
- `klai-portal/backend/app/api/admin.py:449-601` -- bestaande product endpoint patroon

---

### Phase 2: Backend -- Entitlement Resolution Refactor

**Priority:** Primary Goal

**Deliverables:**
- Nieuwe `get_user_effective_products(user_id, db)` functie
- Update `/internal/users/{id}/products` endpoint
- Update `require_product()` dependency
- Uitbreiding plan-downgrade logica voor groepsproducten

**Tasks:**
1. Maak `get_user_effective_products(user_id, db)` in `app/core/products.py` (of vergelijkbare locatie):
   - Query directe toewijzingen uit `portal_user_products`
   - Query groepstoewijzingen via JOIN `portal_group_memberships` -> `portal_group_products`
   - Return `set[str]` als unie van beide
2. Update `/internal/users/{id}/products` in `app/api/internal.py:66-82` om `get_user_effective_products()` aan te roepen
3. Update `require_product()` in `app/api/dependencies.py` om `get_user_effective_products()` te gebruiken
4. Breid plan-downgrade logica in `app/api/admin.py:604-640` uit om ook `portal_group_products` records te verwijderen die het nieuwe plafond overschrijden
5. Performance test: verifieer dat de group-join query < 50ms toevoegt aan JWT enrichment
6. Unit tests voor `get_user_effective_products()` met combinaties van directe en groepstoewijzingen
7. Integratie tests voor JWT enrichment met groepsgebaseerde producten

**Reference implementations:**
- `klai-portal/backend/app/api/dependencies.py` -- `require_product()` patroon
- `klai-portal/backend/app/api/internal.py:66-82` -- JWT enrichment endpoint
- `klai-portal/backend/app/api/admin.py:604-640` -- plan downgrade logica

---

### Phase 3: Frontend -- Groep Product Toggles

**Priority:** Secondary Goal

**Deliverables:**
- Products sectie op groep-detailpagina met Switch toggles per plan-product
- API hooks voor groep-product endpoints

**Tasks:**
1. Maak TanStack Query hooks voor groep-product endpoints (list, assign, revoke)
2. Voeg "Products" sectie toe aan `routes/admin/groups/$groupId/index.tsx` -- parallel aan de bestaande leden-sectie
3. Toon Switch toggle per product dat beschikbaar is in het org-plan
4. Toggle enabled state: POST bij aan, DELETE bij uit
5. Toon disabled toggle voor producten buiten het plan-plafond
6. Optimistic updates via TanStack Query mutation + invalidation

**Reference implementations:**
- `klai-portal/frontend/src/routes/admin/users/$userId/edit.tsx` -- bestaande product toggle UI
- `klai-portal/frontend/src/routes/admin/groups/$groupId/index.tsx` -- groep-detailpagina structuur

---

### Phase 4: Frontend -- User Edit + Standaardgroepen

**Priority:** Secondary Goal

**Deliverables:**
- Gebruiker-bewerkingspagina: verwijder per-user product toggles, vervang door alleen-lezen "Effectieve producten" sectie
- Standaardgroepen aanmaken bij org-creatie

**Tasks:**
1. Maak backend-functie voor standaardgroepen bij org-aanmaak: "Chat users", "Scribe users", "Knowledge users" (afhankelijk van plan)
2. Integreer standaardgroepen-aanmaak in het org-creatie endpoint
3. Verwijder de product-toggles sectie uit `routes/admin/users/$userId/edit.tsx`
4. Voeg "Effectieve producten" alleen-lezen sectie toe die per product toont:
   - Productnaam
   - Bron: "Direct" of groepsnaam (met link naar groep)
5. Maak backend endpoint of extend bestaand endpoint om effectieve producten met bron-informatie te retourneren

---

## Risks & Mitigations

### Risk 1: JWT Enrichment Latency

**Probleem:** De `get_user_effective_products()` query vereist nu een JOIN over `portal_group_memberships` en `portal_group_products`, wat latentie kan toevoegen aan de Zitadel pre-token Action.

**Mitigatie:**
- Gebruik een enkele SQL query met UNION in plaats van meerdere round-trips
- Index op `portal_group_memberships(zitadel_user_id)` bestaat al (SPEC-AUTH-001)
- Index op `portal_group_products(group_id)` wordt toegevoegd in de migratie
- Performance budget: < 50ms extra; monitor via Grafana na deployment
- Fallback: als latentie onacceptabel is, overweeg materialized view of caching

### Risk 2: Race Condition bij Plan Downgrade

**Probleem:** Plan downgrade moet nu zowel `portal_user_products` als `portal_group_products` opschonen. Als dit niet atomair gebeurt, kan een gebruiker tijdelijk toegang behouden.

**Mitigatie:** Beide DELETE operaties in dezelfde database-transactie uitvoeren.

### Risk 3: Admin UI Verwarring

**Probleem:** Verwijderen van per-user product toggles kan admins verwarren die gewend zijn aan de oude workflow.

**Mitigatie:** Toon duidelijke "Effectieve producten" sectie met bron-informatie. Overweeg een banner/tooltip die uitlegt dat producten nu via groepen worden beheerd.

---

## Out of Scope

- Group admin die producten toewijst (alleen org admin)
- Verwijdering van `portal_user_products` tabel (dual-mode blijft behouden)
- Product-definities als database-tabel (blijft hard-coded in `plans.py`)
- Migratiescript voor bestaande per-user toewijzingen naar groepen (follow-up taak)
- Caching van effectieve producten (alleen indien latentie onacceptabel is)

## Follow-up Items

- Migratiescript: bestaande `portal_user_products` records omzetten naar groepslidmaatschappen voor organisaties die voor deze feature bestaan
- Overweeg verwijdering van per-user producttoewijzing als alle organisaties zijn gemigreerd
- Audit logging voor groepsproduct-wijzigingen
