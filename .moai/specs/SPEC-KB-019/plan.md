---
id: SPEC-KB-019
document: plan
version: "1.0.0"
---

# SPEC-KB-019: Notion Connector -- Implementatieplan

## Technologie

- **Backend adapter**: Python 3.12, `unstructured-ingest[notion]`, httpx (async), FastAPI
- **Frontend formulier**: React 19, TypeScript 5.9, TanStack Router, Mantine 8, Paraglide i18n
- **Referentie-implementaties**: `GitHubAdapter` (adapter-patroon), `WebCrawlerAdapter` (cursor/cache-patroon)

---

## Taakafbraak

### T1: Dependency toevoegen (Prioriteit Hoog)

**Bestanden:**
- `klai-connector/pyproject.toml`

**Aanpak:**
- Voeg `unstructured-ingest[notion]` toe aan de dependencies
- Pin de versie na lokale validatie
- Controleer compatibiliteit met bestaande `unstructured` 0.16.0+ dependency

**Dekt:** R6

---

### T2: NotionAdapter implementeren (Prioriteit Hoog)

**Bestanden:**
- `klai-connector/app/adapters/notion.py` (nieuw)
- `klai-connector/app/main.py` (registratie toevoegen)

**Aanpak:**
- Maak `NotionAdapter(BaseAdapter)` naar voorbeeld van `GitHubAdapter`
- Implementeer `list_documents()`: roep Notion Search API aan via unstructured-ingest, retourneer `DocumentRef`-lijst met page ID als `ref` en `source_ref`
- Implementeer `fetch_document()`: haal page blocks op, converteer naar tekst via unstructured
- Implementeer `get_cursor_state()`: retourneer `{"last_synced_at": max(last_edited_time)}`
- Registreer in `main.py` als `registry.register("notion", NotionAdapter(settings))`
- Gebruik httpx.AsyncClient voor API-calls met rate limiting (3 req/s)

**Dekt:** R1, R2, R3, R4, R5

---

### T3: Incrementele sync logica (Prioriteit Hoog)

**Bestanden:**
- `klai-connector/app/adapters/notion.py`

**Aanpak:**
- Bij eerste sync (geen cursor_context): haal alle pagina's op
- Bij vervolgsync: filter op `last_edited_time > cursor_state.last_synced_at`
- Sla bijgewerkte `last_synced_at` op na succesvolle sync
- Respecteer `max_pages` limiet uit config

**Dekt:** R7, R8, R9

---

### T4: Config schema validatie (Prioriteit Hoog)

**Bestanden:**
- `klai-connector/app/adapters/notion.py`

**Aanpak:**
- Valideer `access_token` als verplicht veld bij adapter-initialisatie
- Behandel optionele `database_ids` (lijst van UUID-strings)
- Behandel optioneel `max_pages` met standaardwaarde 500
- Geef duidelijke foutmelding bij ontbrekend of ongeldig token

**Dekt:** R10, R11, R12

---

### T5: Frontend -- connector beschikbaar maken (Prioriteit Medium)

**Bestanden:**
- `klai-portal/frontend/src/routes/app/knowledge/$kbSlug_.add-connector.tsx`

**Aanpak:**
- Zet `notion` connector op `available: true` in de connector type grid
- Voeg het Notion-icoon toe aan de connector-kaart
- Volg het bestaande patroon van GitHub en WebCrawler connectors

**Dekt:** R13

---

### T6: Frontend -- 2-staps formulier (Prioriteit Medium)

**Bestanden:**
- `klai-portal/frontend/src/routes/app/knowledge/$kbSlug_.add-connector.tsx`

**Aanpak:**
- Stap 1: naam + access_token (`type="password"`) + optioneel database_ids textarea
- Stap 2: assertion_modes selectie + max_pages input
- Config assemblage naar het verwachte JSONB-formaat (`access_token`, `database_ids`, `max_pages`)
- Volg het bestaande 2-staps patroon van de GitHub-connector

**Dekt:** R14, R15

---

### T7: i18n message keys (Prioriteit Medium)

**Bestanden:**
- Paraglide NL message-bestand
- Paraglide EN message-bestand

**Aanpak:**
- Voeg minimaal de volgende keys toe:
  - `admin_connectors_notion_access_token`
  - `admin_connectors_notion_access_token_placeholder`
  - `admin_connectors_notion_database_ids`
  - `admin_connectors_notion_database_ids_placeholder`
  - `admin_connectors_notion_max_pages`
  - `admin_connectors_notion_token_help`
- Volg het bestaande `admin_connectors_github_*` naamgevingspatroon

**Dekt:** R16, R17, R18

---

### T8: Tests schrijven (Prioriteit Hoog)

**Bestanden:**
- `klai-connector/tests/adapters/test_notion.py` (nieuw)
- `klai-portal/frontend/src/routes/app/knowledge/__tests__/` (nieuw of uitbreiden)

**Aanpak:**
- Unit tests voor `NotionAdapter`: list_documents, fetch_document, get_cursor_state
- Mock de Notion API / unstructured-ingest responses
- Test incrementele sync met en zonder cursor_state
- Test config validatie (ontbrekend token, ongeldige database_ids)
- Test foutafhandeling (ongeldig token, rate limiting, lege workspace)
- Frontend: test dat het Notion-formulier correct rendert en config assembleert

**Dekt:** Alle requirements (verificatie)

---

## Bestandseigendomskaart

| Bestand | Actie | Taak |
|---------|-------|------|
| `klai-connector/pyproject.toml` | Wijzigen | T1 |
| `klai-connector/app/adapters/notion.py` | Nieuw | T2, T3, T4 |
| `klai-connector/app/main.py` | Wijzigen | T2 |
| `klai-portal/frontend/src/routes/app/knowledge/$kbSlug_.add-connector.tsx` | Wijzigen | T5, T6 |
| Paraglide NL messages | Wijzigen | T7 |
| Paraglide EN messages | Wijzigen | T7 |
| `klai-connector/tests/adapters/test_notion.py` | Nieuw | T8 |

---

## Afhankelijkheidsvolgorde

```
T1 (dependency) 
  -> T2 (adapter) 
    -> T3 (incrementele sync) 
    -> T4 (config validatie)
  -> T8 (tests, parallel met T3/T4)

T7 (i18n keys, onafhankelijk)
  -> T5 (connector beschikbaar)
    -> T6 (formulier)
```

---

## Risico-analyse

### R-1: unstructured-ingest stabiliteit (Risico: Medium)

**Probleem:** `unstructured-ingest` is pre-1.0 en de Notion-connector heeft een geschiedenis van breaking changes.

**Mitigatie:**
- Pin de exacte versie in pyproject.toml na validatie
- Schrijf integratietests die de unstructured API-surface afdekken
- Houd een fallback-optie open: directe Notion API-calls als unstructured faalt

### R-2: Notion API rate limiting (Risico: Medium)

**Probleem:** Notion beperkt tot 3 requests/seconde per integratietoken. Grote workspaces met honderden pagina's kunnen lang duren.

**Mitigatie:**
- Implementeer exponential backoff bij 429-responses
- Respecteer `max_pages` limiet (standaard 500)
- Log sync-voortgang zodat gebruikers zien wat er gebeurt

### R-3: Token beveiliging (Risico: Hoog, geaccepteerd)

**Probleem:** Het Notion access token wordt onversleuteld opgeslagen in JSONB. Dit is een beveiligingsrisico.

**Mitigatie:**
- Consistent met huidige architectuur (GitHub adapter doet hetzelfde)
- Toekomstige SPEC voor `encrypted_config` kolom
- Token wordt gemaskeerd in de frontend (`type="password"`)
- Token wordt niet gelogd in structlog output

### R-4: Notion page block rendering (Risico: Laag)

**Probleem:** Sommige Notion block types (database views, embeds, formules) worden mogelijk niet correct geconverteerd naar tekst.

**Mitigatie:**
- Scope beperkt tot tekstuele inhoud
- unstructured-ingest handelt de meeste standaard block types af
- Niet-ondersteunde blocks worden overgeslagen met een warning log
