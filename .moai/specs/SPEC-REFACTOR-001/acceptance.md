---
id: SPEC-REFACTOR-001
document: acceptance
version: "1.0.0"
---

# Acceptatiecriteria: SPEC-REFACTOR-001

## Overzicht

Alle acceptatiecriteria in Given-When-Then formaat. Elke fase moet aan de bijbehorende criteria voldoen voordat de volgende fase mag starten.

---

## Regressie-criteria (alle fasen)

### AC-REG-001: Bestaande backend testsuite slaagt

```gherkin
Given de volledige backend testsuite draait
When de refactor van de betreffende fase is voltooid
Then slagen alle bestaande tests zonder wijzigingen
And is het aantal geslaagde tests gelijk aan of hoger dan voor de refactor
```

### AC-REG-002: Bestaande frontend testsuite slaagt

```gherkin
Given de volledige frontend testsuite draait
When de refactor van fase 3 ($kbSlug.tsx) is voltooid
Then slagen alle bestaande tests zonder wijzigingen
```

### AC-REG-003: Geen gedragswijzigingen

```gherkin
Given een volledige doorloop van de applicatie (provisioning, admin, knowledge base)
When de refactor van alle drie fasen is voltooid
Then is het observeerbare gedrag identiek aan voor de refactor
And zijn er geen nieuwe foutmeldingen in de logs
```

### AC-REG-004: Geen nieuwe runtime-dependencies

```gherkin
Given de huidige dependency-lijsten (pyproject.toml, package.json)
When de refactor van alle drie fasen is voltooid
Then zijn er geen nieuwe runtime-dependencies toegevoegd
And is de lockfile ongewijzigd qua dependencies (alleen bestandsverplaatsingen)
```

---

## Fase 1: provisioning.py

### AC-PROV-001: provision_tenant importeerbaar vanaf oorspronkelijke locatie

```gherkin
Given de provisioning-module is opgesplitst in een package
When signup.py wordt geladen
Then is `from app.services.provisioning import provision_tenant` succesvol
And heeft de functie dezelfde signatuur (org_id parameter)
And retourneert de functie hetzelfde type als voor de refactor
```

### AC-PROV-002: Rollback werkt na provisioning-fout

```gherkin
Given een provisioning-operatie is gestart
When een fout optreedt tijdens een van de provisioning-stappen
Then voert _rollback alle compensatieacties uit
And worden alle aangemaakte resources opgeruimd
And is de rollback-logica volledig in orchestrator.py
```

### AC-PROV-003: Orchestrator bevat volledige state-machine

```gherkin
Given de provisioning-package is aangemaakt
When orchestrator.py wordt geinspecteerd
Then bevat het _provision, _rollback, en _ProvisionState
And bevat het _caddy_lock op module-niveau
And is er geen rollback-logica in generators.py of infrastructure.py
```

### AC-PROV-004: Generators zijn puur en testbaar

```gherkin
Given generators.py bevat _slugify_unique en _generate_librechat_env
When deze functies worden aangeroepen met bekende input
Then produceren ze deterministische output
And hebben ze geen side-effects (geen I/O behalve _generate_librechat_yaml)
```

### AC-PROV-005: Nieuwe testbestanden aanwezig

```gherkin
Given de provisioning-package is opgesplitst
When de testdirectory wordt geinspecteerd
Then bestaat tests/services/provisioning/test_generators.py
And bestaat tests/services/provisioning/test_infrastructure.py
And bestaat tests/services/provisioning/test_orchestrator.py
And bevat elk testbestand minimaal een test
```

---

## Fase 2: admin.py

### AC-ADMIN-001: Alle 17 endpoints bereikbaar met correcte auth

```gherkin
Given de admin-module is opgesplitst in een package
When elk van de 17 endpoints wordt aangeroepen
Then retourneert elk endpoint dezelfde HTTP-statuscode als voor de refactor
And vereist elk endpoint dezelfde authenticatie en autorisatie
And zijn de response-schemas identiek
```

### AC-ADMIN-002: URL-prefix ongewijzigd

```gherkin
Given de admin-router is opgesplitst in submodules
When de OpenAPI-specificatie wordt gegenereerd
Then beginnen alle admin-endpoints met /api/admin/
And zijn er geen gedupliceerde prefixes (/api/admin/admin/)
And is het totaal aantal endpoints gelijk aan 17
```

### AC-ADMIN-003: Shared helpers beschikbaar voor alle submodules

```gherkin
Given _get_caller_org en _require_admin staan in __init__.py
When users.py, products.py, settings.py of audit.py deze importeren
Then zijn de functies beschikbaar zonder circulaire imports
And werken ze identiek aan de originele implementatie
```

### AC-ADMIN-004: Nieuwe testbestanden aanwezig

```gherkin
Given de admin-package is opgesplitst
When de testdirectory wordt geinspecteerd
Then bestaat tests/api/admin/test_users.py
And bestaat tests/api/admin/test_products.py
And bestaat tests/api/admin/test_settings.py
And bestaat tests/api/admin/test_audit.py
And bevat elk testbestand minimaal een test per endpoint
```

### AC-ADMIN-005: Router-inclusie in hoofdapplicatie werkt

```gherkin
Given de admin-router wordt geimporteerd in de hoofdapplicatie
When de applicatie opstart
Then wordt de admin-router zonder fouten geincludeerd
And zijn alle 17 endpoints geregistreerd in de FastAPI-app
```

---

## Fase 3: $kbSlug.tsx

### AC-FE-001: Alle 6 tabs bereikbaar via nieuwe URLs

```gherkin
Given de knowledge base pagina is opgesplitst in child routes
When een gebruiker navigeert naar /app/knowledge/{slug}/overview
Then wordt de overview-tab weergegeven
And wanneer naar /items wordt genavigeerd, wordt de items-tab weergegeven
And wanneer naar /connectors wordt genavigeerd, wordt de connectors-tab weergegeven
And wanneer naar /members wordt genavigeerd, wordt de members-tab weergegeven
And wanneer naar /taxonomy wordt genavigeerd, wordt de taxonomy-tab weergegeven
And wanneer naar /settings wordt genavigeerd, wordt de settings-tab weergegeven
```

### AC-FE-002: Root-URL redirect naar overview

```gherkin
Given een gebruiker navigeert naar /app/knowledge/{slug} (zonder subpad)
When de route wordt geladen
Then wordt de gebruiker geredirect naar /app/knowledge/{slug}/overview
And is de URL in de browser-balk bijgewerkt
```

### AC-FE-003: Oude ?tab=X URLs redirecten correct

```gherkin
Given een gebruiker navigeert naar /app/knowledge/{slug}?tab=connectors
When de route wordt geladen
Then wordt de gebruiker geredirect naar /app/knowledge/{slug}/connectors
And werkt dit voor alle 6 tab-waarden: overview, items, connectors, members, taxonomy, settings
And worden onbekende tab-waarden geredirect naar /overview
```

### AC-FE-004: Gedeelde data niet opnieuw opgehaald bij tab-wissel

```gherkin
Given een gebruiker is op de overview-tab van een knowledge base
And de kb, stats, members en pendingCount queries zijn geladen
When de gebruiker wisselt naar de connectors-tab
Then worden de gedeelde queries NIET opnieuw uitgevoerd
And blijft de TanStack Query cache intact
And wordt alleen tab-specifieke data opgehaald indien nodig
```

### AC-FE-005: Tab-componenten als aparte bestanden

```gherkin
Given de $kbSlug directory-structuur is aangemaakt
When de directory wordt geinspecteerd
Then bestaat $kbSlug/route.tsx (parent layout)
And bestaat $kbSlug/index.tsx (redirect)
And bestaat $kbSlug/overview.tsx
And bestaat $kbSlug/items.tsx
And bestaat $kbSlug/connectors.tsx
And bestaat $kbSlug/members.tsx
And bestaat $kbSlug/taxonomy.tsx
And bestaat $kbSlug/settings.tsx
```

### AC-FE-006: Geen visuele regressies

```gherkin
Given alle 6 tabs zijn geextraheerd naar aparte bestanden
When elke tab wordt weergegeven in de browser
Then is de visuele weergave identiek aan voor de refactor
And werken alle interactieve elementen (knoppen, formulieren, modals)
And zijn er geen console-errors in de browser
```

---

## Testcriteria

### AC-TEST-001: Elk geextraheerd module heeft een testbestand

| Module | Verwacht testbestand |
|--------|---------------------|
| `provisioning/generators.py` | `test_generators.py` |
| `provisioning/infrastructure.py` | `test_infrastructure.py` |
| `provisioning/orchestrator.py` | `test_orchestrator.py` |
| `admin/users.py` | `test_users.py` |
| `admin/products.py` | `test_products.py` |
| `admin/settings.py` | `test_settings.py` |
| `admin/audit.py` | `test_audit.py` |
| Frontend tab-componenten | Bestaande tests + visuele verificatie |

---

## Definition of Done

De SPEC-REFACTOR-001 is voltooid wanneer:

- [ ] Alle AC-REG criteria zijn voldaan (regressie-vrij)
- [ ] Alle AC-PROV criteria zijn voldaan (provisioning refactor)
- [ ] Alle AC-ADMIN criteria zijn voldaan (admin refactor)
- [ ] Alle AC-FE criteria zijn voldaan (frontend refactor)
- [ ] Alle AC-TEST criteria zijn voldaan (testdekking)
- [ ] Code review is uitgevoerd
- [ ] Geen nieuwe linter-waarschuwingen geintroduceerd (ruff, pyright, ESLint)
