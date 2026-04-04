---
id: SPEC-KB-019
document: acceptance
version: "1.0.0"
---

# SPEC-KB-019: Notion Connector -- Acceptatiecriteria

## Definition of Done

- [ ] `NotionAdapter` implementeert `BaseAdapter` en is geregistreerd in `main.py`
- [ ] `unstructured-ingest[notion]` staat in `pyproject.toml`
- [ ] Eerste sync haalt alle toegankelijke pagina's op
- [ ] Incrementele sync filtert op `last_edited_time`
- [ ] Foutafhandeling bij ongeldig token zonder crash
- [ ] Frontend formulier beschikbaar en functioneel
- [ ] i18n keys aanwezig in NL en EN
- [ ] Unit tests geschreven en geslaagd
- [ ] Code review afgerond

---

## Acceptatiescenario's

### AC-1: Eerste sync -- alle pagina's gesynchroniseerd

**Gegeven** een Notion-connector met een geldig access token en geen bestaande `cursor_state`
**En** de Notion-workspace bevat 5 pagina's

**Wanneer** de sync wordt gestart

**Dan** worden alle 5 pagina's opgehaald via `list_documents()`
**En** elke pagina wordt geconverteerd naar tekst via `fetch_document()`
**En** de `cursor_state` wordt opgeslagen met `last_synced_at` gelijk aan de meest recente `last_edited_time`
**En** de sync_run status is `COMPLETED`

**Dekt:** R1, R2, R3, R4, R7, R9

---

### AC-2: Incrementele sync -- alleen gewijzigde pagina's

**Gegeven** een Notion-connector met een geldig access token
**En** een bestaande `cursor_state` met `last_synced_at: "2026-04-01T10:00:00Z"`
**En** de Notion-workspace bevat 10 pagina's waarvan 2 bewerkt na de cursor timestamp

**Wanneer** de sync wordt gestart

**Dan** worden alleen de 2 gewijzigde pagina's opgehaald
**En** de 8 ongewijzigde pagina's worden overgeslagen
**En** de `cursor_state` wordt bijgewerkt met de nieuwe `last_synced_at`
**En** de sync_run status is `COMPLETED`

**Dekt:** R8, R9

---

### AC-3: Ongeldig token -- duidelijke foutmelding

**Gegeven** een Notion-connector met een ongeldig access token (`secret_invalid123`)

**Wanneer** de sync wordt gestart

**Dan** mislukt de verbinding met de Notion API
**En** de adapter gooit een duidelijke fout (geen onversleutelde stacktrace)
**En** de sync_run status is `FAILED` met een begrijpelijke foutmelding
**En** het ongeldige token wordt niet gelogd in de structlog output

**Dekt:** R10, R12

---

### AC-4: Lege workspace -- leeg resultaat zonder crash

**Gegeven** een Notion-connector met een geldig access token
**En** de Notion-workspace bevat 0 pagina's (leeg)

**Wanneer** de sync wordt gestart

**Dan** retourneert `list_documents()` een lege lijst
**En** er worden geen documenten verwerkt
**En** de `cursor_state` wordt opgeslagen (zonder `last_synced_at` of met initieel tijdstip)
**En** de sync_run status is `COMPLETED`
**En** er treedt geen exception op

**Dekt:** R2, R7, R11

---

### AC-5: Frontend formulier -- connector aanmaken en zichtbaar in lijst

**Gegeven** een gebruiker die is ingelogd en een knowledge base open heeft

**Wanneer** de gebruiker op "Connector toevoegen" klikt

**Dan** is de Notion-kaart zichtbaar in het connector-type grid als beschikbaar
**En** bij selectie verschijnt een 2-staps formulier

**Wanneer** de gebruiker stap 1 invult (naam, access_token, optioneel database_ids)
**En** de gebruiker stap 2 invult (assertion_modes, max_pages)
**En** op "Opslaan" klikt

**Dan** wordt de connector aangemaakt met het juiste config JSONB-formaat
**En** het access_token invoerveld toont de waarde gemaskeerd (password type)
**En** alle labels zijn vertaald via Paraglide i18n
**En** de connector verschijnt in de connectorlijst van de knowledge base

**Dekt:** R13, R14, R15, R16, R17, R18

---

### AC-6: Database-specifieke sync

**Gegeven** een Notion-connector met een geldig access token
**En** `database_ids` is geconfigureerd met 2 specifieke database-UUID's

**Wanneer** de sync wordt gestart

**Dan** worden alleen pagina's uit de 2 opgegeven databases gesynchroniseerd
**En** pagina's uit andere databases en losse pagina's worden overgeslagen

**Dekt:** R10, R11

---

### AC-7: Max pages limiet

**Gegeven** een Notion-connector met een geldig access token
**En** `max_pages` is ingesteld op 10
**En** de Notion-workspace bevat 50 pagina's

**Wanneer** de sync wordt gestart

**Dan** worden maximaal 10 pagina's opgehaald
**En** de sync_run status is `COMPLETED`

**Dekt:** R10

---

## Quality Gate Criteria

| Criterium | Drempel | Verificatiemethode |
|-----------|---------|-------------------|
| Unit test coverage (adapter) | >= 85% | pytest --cov |
| Alle EARS requirements gedekt | 18/18 | Traceability matrix in spec.md |
| Geen hardcoded model namen | 0 | Grep op `gpt-*`, `claude-*` |
| i18n keys compleet | NL + EN | Paraglide compilatie |
| Structlog velden aanwezig | connector_id, org_id, request_id | Log output inspectie |
| Token niet in logs | 0 matches | Grep op `secret_` in log output |
