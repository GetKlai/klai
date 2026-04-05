---
id: SPEC-KB-019
version: "1.0.0"
status: implemented
created: 2026-04-04
updated: 2026-04-05
author: MoAI
priority: medium
tags: [connector, notion, knowledge-base, ingestion]
---

# SPEC-KB-019: Notion Connector

## History

| Versie | Datum      | Auteur | Wijziging           |
|--------|------------|--------|---------------------|
| 1.0.0  | 2026-04-04 | MoAI   | Initieel ontwerp    |

---

## Overzicht

Notion-connector voor het Klai knowledge base-systeem. Gebruikers koppelen hun Notion-workspace aan een Klai knowledge base, waarna pagina's gesynchroniseerd en doorzoekbaar worden via de RAG-pipeline.

---

## Environment

- **klai-connector**: Python 3.12, FastAPI, `unstructured-ingest[notion]`
- **klai-portal/frontend**: React 19, TypeScript 5.9, TanStack Router, Mantine 8, Paraglide i18n
- **klai-portal/backend**: `ConnectorType` bevat reeds `"notion"`; `CONTENT_TYPE_DEFAULTS` bevat reeds `"notion": "kb_article"` -- geen wijzigingen nodig
- **Notion API**: REST API met rate limit van 3 requests/seconde per token
- **Bestaande adapters**: `GitHubAdapter` en `WebCrawlerAdapter` als referentie-implementaties

## Assumptions

- A1: Gebruikers maken zelf een Notion Internal Integration aan via notion.so/my-integrations en plakken het `secret_XXX`-token in Klai. OAuth is buiten scope voor deze MVP.
- A2: Het access token wordt opgeslagen in de `connector.config` JSONB-kolom (onversleuteld, consistent met huidige architectuur). Versleuteling via `encrypted_config` is een toekomstige SPEC.
- A3: De `unstructured-ingest[notion]` library is stabiel genoeg voor productiegebruik, maar de versie moet gepind worden na validatie.
- A4: De portal-backend API hoeft niet aangepast te worden -- `notion` staat al in `ConnectorType` en `CONTENT_TYPE_DEFAULTS`.
- A5: De Notion API levert `last_edited_time` per pagina, bruikbaar als cursor voor incrementele sync.

---

## Requirements

### Module 1: Notion Adapter (klai-connector)

**R1** -- Adapter interface
Het systeem biedt **altijd** een `NotionAdapter` klasse aan die de `BaseAdapter` interface implementeert met de methoden `list_documents()`, `fetch_document()`, `get_cursor_state()` en optioneel `post_sync()`.

**R2** -- Document discovery
**WHEN** `list_documents()` wordt aangeroepen **THEN** roept de adapter de Notion Search API aan via `unstructured-ingest` en retourneert een lijst van `DocumentRef`-objecten met Notion page ID's als `ref` en `source_ref`.

**R3** -- Document ophalen
**WHEN** `fetch_document()` wordt aangeroepen met een `DocumentRef` **THEN** haalt de adapter de Notion-paginablokken op en converteert deze naar platte tekst via `unstructured-ingest`.

**R4** -- Cursor state
**WHEN** `get_cursor_state()` wordt aangeroepen **THEN** retourneert de adapter `{"last_synced_at": "<ISO8601 timestamp>"}` op basis van de meest recente `last_edited_time` van gesynchroniseerde pagina's.

**R5** -- Adapter registratie
Het systeem registreert de adapter **altijd** in `klai-connector/app/main.py` als `registry.register("notion", NotionAdapter(settings))`.

**R6** -- Dependency
Het systeem bevat **altijd** `unstructured-ingest[notion]` als dependency in `klai-connector/pyproject.toml`.

### Module 2: Incrementele Sync

**R7** -- Eerste sync
**WHEN** een Notion-connector voor het eerst synchroniseert (geen bestaande `cursor_state`) **THEN** worden alle toegankelijke pagina's gesynchroniseerd.

**R8** -- Vervolgsync
**WHEN** een Notion-connector synchroniseert met een bestaande `cursor_state` **THEN** worden alleen pagina's gesynchroniseerd die bewerkt zijn na de `last_synced_at` timestamp (via het `last_edited_time` filter van de Notion API).

**R9** -- Cursor opslag
**WHEN** een sync succesvol is afgerond **THEN** wordt de `cursor_state` opgeslagen met de bijgewerkte `last_synced_at` waarde.

### Module 3: Config en Auth

**R10** -- Config schema
Het systeem ondersteunt **altijd** het volgende connector config schema:
- `access_token` (verplicht): Notion Internal Integration token (`secret_XXX`)
- `database_ids` (optioneel): lijst van specifieke database-UUID's om te synchroniseren
- `max_pages` (optioneel, standaard 500): maximum aantal pagina's per sync

**R11** -- Standaard scope
**IF** `database_ids` niet is opgegeven **THEN** synchroniseert de adapter alle toegankelijke pagina's en databases.

**R12** -- Token opslag
Het access token wordt **altijd** opgeslagen in het `connector.config` JSONB-veld (consistent met de huidige architectuur; versleuteling valt buiten scope van deze SPEC).

### Module 4: Frontend UI

**R13** -- Connector beschikbaarheid
Het systeem toont de Notion-kaart in het connector-type grid **altijd** als `available: true`.

**R14** -- Formulier
**WHEN** een gebruiker de Notion-connector selecteert **THEN** toont het systeem een 2-staps formulier:
- Stap 1: naam + `access_token` invoerveld + optioneel `database_ids`
- Stap 2: instellingen (`assertion_modes`, `max_pages`)

**R15** -- Token masking
Het `access_token` invoerveld gebruikt **altijd** `type="password"` om het token te maskeren.

**R16** -- i18n
Alle UI-strings gebruiken **altijd** Paraglide i18n met het `admin_connectors_notion_*` prefix.

### Module 5: i18n

**R17** -- Taalbestanden
Het systeem bevat **altijd** NL- en EN-Paraglide messagebestanden voor alle Notion-specifieke labels.

**R18** -- Minimum keys
Het systeem definieert **altijd** minimaal de volgende i18n-keys:
- `admin_connectors_notion_access_token`
- `admin_connectors_notion_access_token_placeholder`
- `admin_connectors_notion_database_ids`
- `admin_connectors_notion_database_ids_placeholder`
- `admin_connectors_notion_max_pages`
- `admin_connectors_notion_token_help`

---

## Specifications

### Niet-functionele eisen

- **Rate limiting**: De adapter respecteert de Notion API rate limit van 3 req/s per token. Bij `429 Too Many Requests` wordt exponential backoff toegepast.
- **Foutafhandeling**: Ongeldige tokens resulteren in een duidelijke foutmelding naar de gebruiker, zonder crash of onversleutelde stacktrace.
- **Paginering**: De adapter ondersteunt automatische paginering van Notion API-resultaten.
- **Logging**: Alle sync-operaties loggen via structlog met `connector_id`, `org_id`, en `request_id` velden.

### Buiten scope

- OAuth-authenticatie (toekomstige SPEC)
- Versleutelde opslag van het access token (toekomstige SPEC)
- Real-time webhooks van Notion (toekomstige SPEC)
- Notion-specifieke block types (database views, embeds) -- alleen tekstuele inhoud

---

## Traceability

| Requirement | Plan Taak | Acceptance Scenario |
|-------------|-----------|---------------------|
| R1-R6       | T1, T2    | AC-1, AC-2          |
| R7-R9       | T3        | AC-1, AC-2          |
| R10-R12     | T4        | AC-3, AC-4          |
| R13-R16     | T5, T6    | AC-5                |
| R17-R18     | T7        | AC-5                |
