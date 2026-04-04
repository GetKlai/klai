---
id: SPEC-REFACTOR-001
version: "1.0.0"
status: completed
created: 2026-04-02
updated: 2026-04-02
author: MoAI
priority: medium
---

# SPEC-REFACTOR-001: God-component refactor — $kbSlug.tsx, provisioning.py, admin.py

## HISTORY

| Versie | Datum      | Auteur | Wijziging                        |
|--------|------------|--------|----------------------------------|
| 1.0.0  | 2026-04-02 | MoAI   | Initieel SPEC-document aangemaakt |

---

## 1. Environment (Omgeving)

### 1.1 Huidige situatie

Drie bestanden in de Klai-monorepo overschrijden de beheersbare omvang en bevatten meerdere verantwoordelijkheden in een enkel bestand:

| Bestand | Locatie | Regels | Probleem |
|---------|---------|--------|----------|
| `$kbSlug.tsx` | `klai-portal/frontend/src/routes/app/knowledge/$kbSlug.tsx` | 1.863 | 6 tab-componenten in een bestand |
| `provisioning.py` | `klai-portal/backend/app/services/provisioning.py` | 664 | Generators, infrastructuur en orchestratie gemengd |
| `admin.py` | `klai-portal/backend/app/api/admin.py` | 889 | 4 domeinen (users, products, settings, audit) in een router |

### 1.2 Technische omgeving

- **Frontend:** React 19, Vite, TypeScript 5.9, TanStack Router, TanStack Query, Mantine 8, Tailwind 4
- **Backend:** Python 3.12, FastAPI, SQLAlchemy (async), Alembic, PostgreSQL, uv
- **Testinfrastructuur:** pytest (backend), Vitest/Playwright (frontend)

### 1.3 Afhankelijkheden

- `provisioning.py` wordt geimporteerd door `signup.py` via `provision_tenant(org_id)`
- `admin.py` wordt gerouteerd via de FastAPI `router` inclusie in de hoofdapplicatie
- `$kbSlug.tsx` wordt gerouteerd via TanStack Router `createFileRoute('/app/knowledge/$kbSlug')`

---

## 2. Assumptions (Aannames)

| ID | Aanname | Vertrouwen | Risico indien onjuist |
|----|---------|------------|----------------------|
| A1 | Alle bestaande tests slagen voor aanvang van de refactor | Hoog | Geen baseline om regressies te detecteren |
| A2 | Tab-lokale state in `$kbSlug.tsx` is volledig geisoleerd per tab | Hoog | Extract vereist extra state-lifting |
| A3 | `_caddy_lock` in provisioning.py is module-scoped en mag in orchestrator.py blijven | Hoog | Lock-contention bij verkeerde plaatsing |
| A4 | Alle 17 admin-endpoints gebruiken dezelfde `_get_caller_org()` en `_require_admin()` helpers | Hoog | Helpers moeten gedupliceerd of apart geplaatst worden |
| A5 | TanStack Router ondersteunt child routes met `<Outlet/>` in een `$kbSlug/` directory-structuur | Hoog | Alternatieve routeringsoplossing nodig |
| A6 | Geen externe consumers importeren interne functies (functies met `_` prefix) uit provisioning.py | Medium | Breaking imports bij verplaatsing |

---

## 3. Requirements (Eisen)

### 3.1 Ubiquitaire eisen (altijd geldig)

**REQ-U-001:** Het systeem **zal** na refactoring identiek gedrag vertonen voor alle mutaties, queries en side-effects — NULGEDRAGSWIJZIGING.

**REQ-U-002:** Het systeem **zal** na refactoring dezelfde API-contracten behouden — NULAPIWIJZIGING. Alle backend-URLs, request-/response-schema's en HTTP-methoden blijven ongewijzigd.

**REQ-U-003:** Het systeem **zal** na refactoring dezelfde frontend-functionaliteit bieden — alle 6 tabs (overview, items, connectors, members, taxonomy, settings) blijven bereikbaar en functioneel.

**REQ-U-004:** Het systeem **zal** voor elke nieuw geextraheerde module een corresponderend testbestand hebben.

### 3.2 Event-driven eisen (WHEN ... THEN ...)

**REQ-E-001:** **WHEN** een gebruiker navigeert naar `/app/knowledge/{slug}` zonder subpad **THEN** wordt de gebruiker geredirect naar `/app/knowledge/{slug}/overview`.

**REQ-E-002:** **WHEN** een gebruiker navigeert naar `/app/knowledge/{slug}?tab=connectors` (oud URL-formaat) **THEN** wordt de gebruiker geredirect naar `/app/knowledge/{slug}/connectors` (nieuw pad-formaat).

**REQ-E-003:** **WHEN** `provision_tenant(org_id)` wordt aangeroepen vanuit `signup.py` **THEN** is het importpad ongewijzigd of wordt het vanuit dezelfde module-locatie geexporteerd via `__init__.py`.

**REQ-E-004:** **WHEN** een provisioning-operatie faalt **THEN** voert de rollback-logica in `orchestrator.py` alle compensatieacties uit (identiek aan huidig gedrag).

**REQ-E-005:** **WHEN** een admin-endpoint wordt aangeroepen **THEN** past het dezelfde authenticatie- en autorisatiecontroles toe als voor de refactor.

### 3.3 State-driven eisen (IF ... THEN ...)

**REQ-S-001:** **IF** een gebruiker wisselt tussen tabs in de knowledge base **THEN** worden gedeelde queries (`kb`, `stats`, `members`, `pendingCount`) NIET opnieuw opgehaald — TanStack Query cache wordt hergebruikt.

**REQ-S-002:** **IF** de provisioning-orchestrator in een `_ProvisionState` is **THEN** blijft de volledige state-machine in `orchestrator.py` — niet verspreid over meerdere bestanden.

**REQ-S-003:** **IF** de admin-router wordt opgesplitst in submodules **THEN** blijft de URL-prefix `/api/admin/...` ongewijzigd voor alle 17 endpoints.

### 3.4 Unwanted-eisen (het systeem zal NIET ...)

**REQ-N-001:** Het systeem **zal niet** rollback-logica splitsen over meerdere bestanden — rollback blijft volledig in `orchestrator.py`.

**REQ-N-002:** Het systeem **zal niet** de publieke API van `provision_tenant` wijzigen — dezelfde functiesignatuur en return-type.

**REQ-N-003:** Het systeem **zal niet** nieuwe runtime-dependencies introduceren voor deze refactor.

### 3.5 Optionele eisen (waar mogelijk)

**REQ-O-001:** **Waar mogelijk**, voeg type-hints toe aan functies die momenteel untyped zijn tijdens de extractie.

**REQ-O-002:** **Waar mogelijk**, voeg docstrings toe aan geextraheerde modules om het doel van elke module te documenteren.

---

## 4. Specifications (Specificaties)

### 4.1 Frontend: $kbSlug.tsx -> $kbSlug/ directory

**Doelstructuur:**

```
src/routes/app/knowledge/$kbSlug/
  route.tsx          # Layout met <Outlet/>, gedeelde queries, tab-navigatie
  index.tsx          # Redirect naar /overview
  overview.tsx       # Tab: overview
  items.tsx          # Tab: items
  connectors.tsx     # Tab: connectors
  members.tsx        # Tab: members
  taxonomy.tsx       # Tab: taxonomy
  settings.tsx       # Tab: settings
```

**Routering:**

- Parent route: `createFileRoute('/app/knowledge/$kbSlug')` in `route.tsx`
- Child routes: `createFileRoute('/app/knowledge/$kbSlug/overview')` enz.
- URL-migratie: `validateSearch` in `route.tsx` detecteert `?tab=X` en redirect naar corresponderende child route
- Gedeelde state: `kb`, `stats`, `members`, `pendingCount` queries in parent `route.tsx`, doorgegeven via TanStack Router context of props

### 4.2 Backend: provisioning.py -> services/provisioning/ package

**Doelstructuur:**

```
services/provisioning/
  __init__.py          # Exporteert provision_tenant (publiek entry point)
  generators.py        # _slugify_unique, _generate_librechat_env, _generate_librechat_yaml
  infrastructure.py    # Docker, MongoDB, Caddy, Redis utility-functies (7 functies)
  orchestrator.py      # _provision, _rollback, _ProvisionState, _caddy_lock
```

**Importcontract:**

```python
# Bestaande import blijft werken:
from app.services.provisioning import provision_tenant
```

### 4.3 Backend: admin.py -> api/admin/ package

**Doelstructuur:**

```
api/admin/
  __init__.py    # Exporteert router, _get_caller_org, _require_admin
  users.py       # 8 user lifecycle endpoints
  products.py    # 6 product entitlement endpoints
  settings.py    # 3 billing & settings endpoints
  audit.py       # 1 audit log endpoint
```

**Router-samenstelling:**

```python
# __init__.py
from fastapi import APIRouter
router = APIRouter(prefix="/admin", tags=["admin"])

from .users import router as users_router
from .products import router as products_router
from .settings import router as settings_router
from .audit import router as audit_router

router.include_router(users_router)
router.include_router(products_router)
router.include_router(settings_router)
router.include_router(audit_router)
```

---

## 5. Constraints (Randvoorwaarden)

| ID | Randvoorwaarde | Type |
|----|---------------|------|
| C1 | Nul gedragswijzigingen — alle mutaties, queries en side-effects identiek | Hard |
| C2 | Nul API-contractwijzigingen — URLs, schemas, HTTP-methoden ongewijzigd | Hard |
| C3 | Bestaande testsuite moet 100% blijven slagen na elke fase | Hard |
| C4 | Geen nieuwe runtime-dependencies | Hard |
| C5 | Elke geextraheerde module krijgt een eigen testbestand | Hard |
| C6 | Backwards-compatibiliteit voor `?tab=X` URLs via redirect | Hard |
| C7 | `provision_tenant` importeerbaar vanaf oorspronkelijke locatie | Hard |

---

## 6. Traceability (Traceerbaarheid)

| Requirement | Plan.md Fase | Acceptance.md Scenario |
|-------------|-------------|----------------------|
| REQ-U-001 | Fase 1, 2, 3 | AC-REG-001, AC-REG-002, AC-REG-003 |
| REQ-U-002 | Fase 1, 2 | AC-API-001, AC-API-002 |
| REQ-U-003 | Fase 3 | AC-FE-001 |
| REQ-U-004 | Fase 1, 2, 3 | AC-TEST-001 |
| REQ-E-001 | Fase 3 | AC-FE-002 |
| REQ-E-002 | Fase 3 | AC-FE-003 |
| REQ-E-003 | Fase 1 | AC-PROV-001 |
| REQ-E-004 | Fase 1 | AC-PROV-002 |
| REQ-E-005 | Fase 2 | AC-ADMIN-001 |
| REQ-S-001 | Fase 3 | AC-FE-004 |
| REQ-S-002 | Fase 1 | AC-PROV-003 |
| REQ-S-003 | Fase 2 | AC-ADMIN-002 |
| REQ-N-001 | Fase 1 | AC-PROV-003 |
| REQ-N-002 | Fase 1 | AC-PROV-001 |
| REQ-N-003 | Fase 1, 2, 3 | AC-REG-004 |
