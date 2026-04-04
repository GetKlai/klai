---
id: SPEC-REFACTOR-001
document: plan
version: "1.0.0"
---

# Implementatieplan: SPEC-REFACTOR-001

## Overzicht

God-component refactor van drie bestanden in de Klai-monorepo. De refactor wordt uitgevoerd in drie fasen, geordend van laagste naar hoogste risico.

---

## Fasevolgorde en rationale

| Fase | Component | Risico | Rationale |
|------|-----------|--------|-----------|
| 1 | `provisioning.py` | Laag | Puur backend, goed testbaar, kleinste bestand (664 regels), enkele consumer (`signup.py`) |
| 2 | `admin.py` | Laag-Medium | Backend met meer endpoints (17), maar geen gedragswijziging — alleen bestandsverplaatsing |
| 3 | `$kbSlug.tsx` | Medium | Frontend, grootste bestand (1.863 regels), routeringswijziging, URL-migratie vereist |

**Waarom backend eerst:**
- Backend-refactors zijn makkelijker te verif eren met geautomatiseerde tests (pytest)
- Geen visuele regressie-risico's
- Kleinere blast radius per fase
- Frontend-refactor bouwt voort op vertrouwen opgedaan in backend-fasen

---

## Fase 1: provisioning.py (Prioriteit: Hoog)

### Doel

Splits `services/provisioning.py` (664 regels) op in een Python package `services/provisioning/` met vier bestanden.

### Stappen

1. **Baseline vastleggen**
   - Draai volledige backend testsuite en leg resultaten vast
   - Identificeer alle imports van `provisioning` in de codebase met `grep`

2. **Package-structuur aanmaken**
   - Maak `services/provisioning/__init__.py` aan
   - Exporteer `provision_tenant` vanuit `__init__.py`

3. **Generators extraheren**
   - Verplaats `_slugify_unique`, `_generate_librechat_env`, `_generate_librechat_yaml` naar `generators.py`
   - Behoud interne imports voor YAML-fixtures

4. **Infrastructuur extraheren**
   - Verplaats 7 Docker/MongoDB/Caddy/Redis utility-functies naar `infrastructure.py`

5. **Orchestrator isoleren**
   - Verplaats `_provision`, `_rollback`, `_ProvisionState` naar `orchestrator.py`
   - `_caddy_lock` blijft in `orchestrator.py` (module-level)
   - Orchestrator importeert uit `generators` en `infrastructure`

6. **Tests schrijven**
   - `tests/services/provisioning/test_generators.py`
   - `tests/services/provisioning/test_infrastructure.py`
   - `tests/services/provisioning/test_orchestrator.py`

7. **Verificatie**
   - Draai volledige backend testsuite — moet identiek slagen
   - Verifieer `from app.services.provisioning import provision_tenant` werkt

### Afhankelijkheden

| Consumer | Importpad | Actie vereist |
|----------|-----------|---------------|
| `signup.py` | `from app.services.provisioning import provision_tenant` | Geen — `__init__.py` exporteert `provision_tenant` |

### Risicomitigatie

- **Risico:** Circulaire imports tussen submodules
  **Mitigatie:** Eenrichtings-importgrafiek: orchestrator -> generators, orchestrator -> infrastructure. Nooit andersom.

- **Risico:** `_caddy_lock` scope verandert bij verplaatsing
  **Mitigatie:** Lock blijft module-level in orchestrator.py, identiek aan huidige scope.

---

## Fase 2: admin.py (Prioriteit: Medium)

### Doel

Splits `api/admin.py` (889 regels, 17 endpoints) op in een Python package `api/admin/` met vijf bestanden.

### Stappen

1. **Baseline vastleggen**
   - Draai volledige backend testsuite
   - Inventariseer alle 17 endpoints met hun HTTP-methoden en URLs

2. **Package-structuur aanmaken**
   - Maak `api/admin/__init__.py` aan met hoofd-`router`
   - Verplaats `_get_caller_org()` en `_require_admin()` naar `__init__.py`

3. **Endpoints per domein extraheren**
   - `users.py`: 8 user lifecycle endpoints
   - `products.py`: 6 product entitlement endpoints
   - `settings.py`: 3 billing & settings endpoints
   - `audit.py`: 1 audit log endpoint

4. **Router-samenstelling**
   - Elke submodule definieert een eigen `router = APIRouter()`
   - `__init__.py` includeert alle sub-routers
   - Geen prefix-wijzigingen — alle URLs blijven identiek

5. **Import-update**
   - Update de hoofd-app router-inclusie als het importpad wijzigt
   - Verifieer dat `router` nog steeds op dezelfde manier wordt geincludeerd

6. **Tests schrijven**
   - `tests/api/admin/test_users.py`
   - `tests/api/admin/test_products.py`
   - `tests/api/admin/test_settings.py`
   - `tests/api/admin/test_audit.py`

7. **Verificatie**
   - Draai volledige backend testsuite
   - Verifieer alle 17 endpoints via curl of httpx

### Afhankelijkheden

| Consumer | Importpad | Actie vereist |
|----------|-----------|---------------|
| Hoofd-app router | `from app.api.admin import router` | Mogelijk pad-update in `__init__.py` |

### Risicomitigatie

- **Risico:** Router prefix-duplicatie (dubbele `/admin/admin/`)
  **Mitigatie:** Sub-routers krijgen GEEN prefix. Alleen de hoofd-router in `__init__.py` heeft `prefix="/admin"`.

- **Risico:** Shared helpers (`_get_caller_org`, `_require_admin`) niet bereikbaar
  **Mitigatie:** Helpers worden geexporteerd vanuit `__init__.py` en geimporteerd door submodules.

---

## Fase 3: $kbSlug.tsx (Prioriteit: Medium)

### Doel

Splits `$kbSlug.tsx` (1.863 regels, 6 tabs) op in een TanStack Router directory-structuur met child routes.

### Stappen

1. **Baseline vastleggen**
   - Verifieer dat alle 6 tabs functioneel zijn in de browser
   - Draai frontend testsuite indien aanwezig

2. **Directory-structuur aanmaken**
   - Maak `$kbSlug/` directory aan
   - Verplaats huidige `$kbSlug.tsx` tijdelijk als backup

3. **Parent route aanmaken (`route.tsx`)**
   - `createFileRoute('/app/knowledge/$kbSlug')` met layout
   - Gedeelde queries: `kb`, `stats`, `members`, `pendingCount`
   - Tab-navigatie component
   - `<Outlet/>` voor child routes
   - `validateSearch` voor `?tab=X` backwards-compatibiliteit

4. **Index route (`index.tsx`)**
   - Redirect van `/app/knowledge/{slug}` naar `/app/knowledge/{slug}/overview`

5. **Tab-componenten extraheren**
   - `overview.tsx`, `items.tsx`, `connectors.tsx`, `members.tsx`, `taxonomy.tsx`, `settings.tsx`
   - Elk als child route met `createFileRoute`
   - Tab-lokale state blijft in het betreffende bestand
   - Gedeelde data via TanStack Router context of props van parent

6. **URL-migratie**
   - `validateSearch` in parent detecteert `?tab=X` parameter
   - Redirect naar corresponderende child route path
   - Ondersteunde mappings: `?tab=overview` -> `/overview`, `?tab=items` -> `/items`, enz.

7. **Verificatie**
   - Alle 6 tabs bereikbaar via nieuwe URLs
   - Oude `?tab=X` URLs redirecten correct
   - Gedeelde data wordt niet opnieuw opgehaald bij tab-wissel
   - Geen visuele regressies

### Technische aanpak

- **TanStack Router file-based routing:** De `$kbSlug/` directory wordt automatisch herkend als geneste route
- **Gedeelde queries:** Via `useRouteContext` of `useLoaderData` van de parent route
- **Tab-navigatie:** Mantine `Tabs` component gekoppeld aan TanStack Router `<Link/>`

### Risicomitigatie

- **Risico:** TanStack Router herkent directory-structuur niet correct
  **Mitigatie:** Verifieer met `routeTree.gen.ts` dat alle child routes gegenereerd worden.

- **Risico:** Gedeelde queries worden opnieuw opgehaald per tab-wissel
  **Mitigatie:** Queries in parent route met TanStack Query `staleTime` configuratie.

- **Risico:** Oude bookmarks en gedeelde links breken
  **Mitigatie:** `validateSearch` redirect in parent route vangt alle `?tab=X` patronen op.

---

## Afhankelijkhedenkaart

```
signup.py
  └── import provision_tenant
        └── services/provisioning/__init__.py
              ├── orchestrator.py
              │     ├── generators.py
              │     └── infrastructure.py
              └── (re-export provision_tenant)

main app router
  └── include admin router
        └── api/admin/__init__.py
              ├── users.py
              ├── products.py
              ├── settings.py
              └── audit.py

TanStack Router
  └── /app/knowledge/$kbSlug (route.tsx)
        ├── /overview (overview.tsx)
        ├── /items (items.tsx)
        ├── /connectors (connectors.tsx)
        ├── /members (members.tsx)
        ├── /taxonomy (taxonomy.tsx)
        └── /settings (settings.tsx)
```

---

## Kwaliteitspoorten per fase

| Poort | Fase 1 | Fase 2 | Fase 3 |
|-------|--------|--------|--------|
| Bestaande tests slagen | Vereist | Vereist | Vereist |
| Nieuwe testbestanden aanwezig | 3 bestanden | 4 bestanden | Per component |
| Import-compatibiliteit geverifieerd | `provision_tenant` | `router` | N.v.t. |
| API-endpoints bereikbaar | N.v.t. | Alle 17 | N.v.t. |
| UI-tabs bereikbaar | N.v.t. | N.v.t. | Alle 6 |
| URL-redirect werkt | N.v.t. | N.v.t. | `?tab=X` -> pad |
| Geen nieuwe dependencies | Geverifieerd | Geverifieerd | Geverifieerd |
