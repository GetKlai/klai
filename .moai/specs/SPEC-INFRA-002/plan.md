---
id: SPEC-INFRA-002
type: plan
version: "1.0.0"
---

# SPEC-INFRA-002: Implementatieplan — DB-driven Per-tenant MCP Server Management

---

## 1. Taakdecompositie per module

### Module 1: MCP Catalog (`deploy/librechat/mcp_catalog.yaml`)

| Taak | Beschrijving | Prioriteit |
|------|-------------|------------|
| T1 | Maak `deploy/librechat/mcp_catalog.yaml` met `twenty-crm` entry | Primair doel |

### Module 2: Secrets + Alembic Migratie

| Taak | Beschrijving | Prioriteit |
|------|-------------|------------|
| T2 | Implementeer `encrypt_mcp_secret()` en `decrypt_mcp_secret()` in `secrets.py` | Primair doel |
| T3 | Schrijf unit tests voor encrypt/decrypt round-trip | Primair doel |
| T4 | Maak Alembic migratie die getklai stdio seed data vervangt door streamable-http formaat | Primair doel |

### Module 3: Provisioning Updates

| Taak | Beschrijving | Prioriteit |
|------|-------------|------------|
| T5 | Update `_generate_librechat_yaml()` met catalog-lookup logica | Secundair doel |
| T6 | Update `_generate_librechat_env()` met MCP env var injectie (decrypt + schrijf) | Secundair doel |
| T7 | Implementeer `_flush_redis_and_restart_librechat()` | Secundair doel |
| T8 | Integreer T5-T7 in `_start_librechat_container()` flow | Secundair doel |

### Module 4: Portal API

| Taak | Beschrijving | Prioriteit |
|------|-------------|------------|
| T9 | Implementeer `GET /api/orgs/{org_id}/mcp-servers` | Tertiair doel |
| T10 | Implementeer `PUT /api/orgs/{org_id}/mcp-servers/{server_id}` | Tertiair doel |
| T11 | Implementeer `POST /api/orgs/{org_id}/mcp-servers/{server_id}/test` | Tertiair doel |

### Module 5: Portal UI + i18n

| Taak | Beschrijving | Prioriteit |
|------|-------------|------------|
| T12 | Maak route `/admin/integrations` met integratiepagina | Tertiair doel |
| T13 | Voeg sidebar link "Integraties" toe aan admin navigatie | Tertiair doel |
| T14 | Implementeer MCP server kaarten: toggle, configuratieformulier, test-knop | Tertiair doel |
| T15 | Voeg i18n vertalingen toe (NL + EN) via Paraglide | Tertiair doel |

### Productie-migratie

| Taak | Beschrijving | Prioriteit |
|------|-------------|------------|
| T16 | Draai Alembic migratie op getklai productie-database | Laatste doel |
| T17 | Verifieer end-to-end flow: Portal UI -> DB -> Provisioning -> LibreChat | Laatste doel |

---

## 2. Technische specificaties

### 2.1 MCP Catalog YAML structuur

```yaml
# deploy/librechat/mcp_catalog.yaml
version: "1.0"
servers:
  twenty-crm:
    description: "Twenty CRM -- contacten, bedrijven, deals, taken"
    required_env_vars:
      - TWENTY_API_KEY
      - TWENTY_BASE_URL
    config_template:
      type: streamable-http
      url: "${TWENTY_BASE_URL}/mcp"
      headers:
        Authorization: "Bearer ${TWENTY_API_KEY}"
    system_prompt_hint: |
      ## Twenty CRM
      Use http_request_mcp_twenty-crm tool. Base URL already configured.
      Notes require bodyV2 (NOT body):
        { "title": "...", "bodyV2": { "markdown": "...", "blocknote": null } }
      POST /objects/noteTargets to link notes to records.
```

Uitbreidbaar: nieuwe MCP servers worden toegevoegd als extra entries onder `servers`.

### 2.2 Secret helpers API

```python
# klai-portal/backend/app/services/secrets.py

def encrypt_mcp_secret(plaintext: str) -> str:
    """Encrypt een MCP secret en retourneer base64-encoded ciphertext.

    Gebruikt de bestaande PortalSecretsService (AES-256-GCM).
    Base64 encoding maakt het resultaat JSON-serializable.
    """

def decrypt_mcp_secret(ciphertext: str) -> str:
    """Decrypt een base64-encoded MCP secret ciphertext.

    Retourneert de originele plaintext string.
    Raises ValueError bij ongeldige ciphertext.
    """
```

### 2.3 Alembic migratie: seed data correctie

De migratie vervangt de verouderde stdio seed data in `PortalOrg.mcp_servers` voor de `getklai` tenant. Migratie-strategie:

1. **Upgrade**: zoek de getklai org via slug, vervang `mcp_servers` JSON door streamable-http formaat met encrypted `TWENTY_API_KEY`
2. **Downgrade**: zet `mcp_servers` terug naar `None` (de oorspronkelijke waarde voor het corrigeren)

Aandachtspunt: het `TWENTY_API_KEY` token moet bij migratie-tijd beschikbaar zijn (via environment variable of uit bestaande `.env` op de server). De migratie encrypt het token met `encrypt_mcp_secret()`.

### 2.4 Provisioning: catalog-lookup flow

```
_start_librechat_container(slug, env_file_host_path, mcp_servers)
  |
  +-- _generate_librechat_yaml(base_path, mcp_servers)
  |     |
  |     +-- Laad mcp_catalog.yaml
  |     +-- Voor elke enabled entry in mcp_servers:
  |     |     +-- Valideer server_id in catalog (skip + warn als niet gevonden)
  |     |     +-- Merge config_template uit catalog in mcpServers sectie
  |     |     +-- Voeg server naam toe aan modelSpecs.list[].mcpServers
  |     +-- Return YAML string
  |
  +-- _generate_librechat_env(slug, org, ...)
  |     |
  |     +-- Bestaande env vars (Zitadel, LiteLLM, etc.)
  |     +-- NIEUW: Loop over mcp_servers entries
  |     |     +-- decrypt_mcp_secret() voor elke secret var
  |     |     +-- Schrijf {VAR}={value} naar .env
  |     +-- Return env content string
  |
  +-- _flush_redis_and_restart_librechat(slug)
        |
        +-- docker exec redis redis-cli FLUSHALL
        +-- docker restart librechat-{slug}
        +-- Health check (max 30s)
        +-- Log resultaat (zonder secrets)
```

### 2.5 Portal API contract

**GET `/api/orgs/{org_id}/mcp-servers`**

Response `200 OK`:

```json
{
  "servers": [
    {
      "id": "twenty-crm",
      "description": "Twenty CRM -- contacten, bedrijven, deals, taken",
      "enabled": true,
      "required_env_vars": ["TWENTY_API_KEY", "TWENTY_BASE_URL"],
      "configured_env_vars": ["TWENTY_API_KEY", "TWENTY_BASE_URL"]
    }
  ]
}
```

`configured_env_vars` toont welke variabelen al geconfigureerd zijn (niet de waarden). De API combineert catalog data (description, required_env_vars) met tenant-specifieke data (enabled, configured_env_vars).

**PUT `/api/orgs/{org_id}/mcp-servers/{server_id}`**

Request:

```json
{
  "enabled": true,
  "env": {
    "TWENTY_API_KEY": "sk-xxx-plaintext",
    "TWENTY_BASE_URL": "https://crm.getklai.com"
  }
}
```

Response `200 OK`:

```json
{
  "id": "twenty-crm",
  "enabled": true,
  "configured_env_vars": ["TWENTY_API_KEY", "TWENTY_BASE_URL"],
  "restart_required": true
}
```

Verwerking:
1. Valideer `server_id` tegen catalog (404 als niet gevonden)
2. Valideer dat alle `required_env_vars` aanwezig zijn in `env` (422 als ontbrekend)
3. Encrypt secret env vars met `encrypt_mcp_secret()`
4. Sla op in `PortalOrg.mcp_servers` JSON
5. Trigger `_flush_redis_and_restart_librechat()` asynchroon

**POST `/api/orgs/{org_id}/mcp-servers/{server_id}/test`**

Request: leeg body

Response `200 OK`:

```json
{
  "status": "ok",
  "response_time_ms": 145,
  "tools_available": ["http_request", "send_email", "search_help_center"]
}
```

Response `502 Bad Gateway`:

```json
{
  "status": "error",
  "error": "Connection refused to https://crm.getklai.com/mcp"
}
```

De test stuurt een JSON-RPC `initialize` request naar de MCP server URL met de geconfigureerde headers.

### 2.6 Portal UI componenten

| Component | Beschrijving |
|-----------|-------------|
| `IntegrationsPage` | Hoofdpagina, laadt MCP servers via GET endpoint |
| `McpServerCard` | Kaart per MCP server: toggle, env var formulier, test-knop |
| `McpTestButton` | "Test verbinding" knop, toont resultaat inline |
| Sidebar item | "Integraties" link onder admin sectie, icoon: puzzle-piece |

Formulier-gedrag:
- Secret velden (vars die `KEY`, `SECRET`, `TOKEN` bevatten): password input, niet pre-filled
- Non-secret velden (zoals `TWENTY_BASE_URL`): text input, pre-filled uit configuratie
- Opslaan disabled totdat alle required_env_vars ingevuld zijn
- Na opslaan: loading state, daarna success/error toast

---

## 3. Risicoanalyse

| ID | Risico | Ernst | Mitigatie |
|----|--------|-------|-----------|
| R-001 | Redis `FLUSHALL` wist alle keys, niet alleen LibreChat config. Actieve sessies verliezen cached state. | MEDIUM | REQ-O-002 stelt selectieve flush voor. Start met FLUSHALL, documenteer impact, implementeer selectieve flush als follow-up. Voer flush uit tijdens lage traffic momenten. |
| R-002 | Container restart veroorzaakt korte downtime voor de tenant. | LAAG | Restart duurt typisch 5-10 seconden. Documenteer in UI dat wijzigingen een korte onderbreking veroorzaken. |
| R-003 | Alembic migratie vereist dat `TWENTY_API_KEY` beschikbaar is bij migratie-tijd voor encryptie. | MEDIUM | Lees het token uit environment variable. Fallback: sla de encryptie over en stel `mcp_servers` in op `None`; handmatige configuratie via Portal UI na migratie. |
| R-004 | DB-driven provisioning activeren voordat de seed data gecorrigeerd is, breekt de getklai tenant. | HOOG | REQ-N-003 blokkeert dit expliciet. Alembic migratie (T4) moet draaien voordat provisioning updates (T5-T8) geactiveerd worden. |
| R-005 | Catalog ID mismatch: DB bevat een server_id dat niet in de catalog staat. | LAAG | REQ-S-002 schrijft voor dat onbekende entries genegeerd worden met een warning log. Geen crash, geen data loss. |
| R-006 | Gedecrypteerde secrets lekken in logs. | KRITIEK | REQ-N-001 verbiedt dit expliciet. Gebruik structlog met secret-filtering. Review alle log statements in provisioning.py en secrets.py. Geen `repr()` of `str()` van secret-bevattende objecten in logs. |
| R-007 | Portal API IDOR: gebruiker van tenant A wijzigt MCP config van tenant B. | HOOG | Gebruik bestaande tenant-scoping middleware. Valideer dat `org_id` in URL overeenkomt met de geauthenticeerde gebruiker's organisatie. Volg multi-tenant security patronen uit pitfalls/security.md. |

---

## 4. Implementatievolgorde

De taken hebben de volgende afhankelijkheden en worden in deze volgorde uitgevoerd:

### Fase 1: Fundament (parallel uitvoerbaar)

| Stap | Taak | Afhankelijkheid |
|------|------|-----------------|
| 1 | T1: Maak `mcp_catalog.yaml` | Geen |
| 2 | T2: Implementeer `encrypt_mcp_secret()` / `decrypt_mcp_secret()` | Geen |
| 3 | T3: Unit tests voor encrypt/decrypt | T2 |
| 4 | T4: Alembic migratie seed data correctie | T2 |

### Fase 2: Provisioning integratie (sequentieel)

| Stap | Taak | Afhankelijkheid |
|------|------|-----------------|
| 5 | T5: Update `_generate_librechat_yaml()` met catalog-lookup | T1 |
| 6 | T6: Update `_generate_librechat_env()` met MCP env vars | T2 |
| 7 | T7: Implementeer `_flush_redis_and_restart_librechat()` | Geen |
| 8 | T8: Integreer T5-T7 in `_start_librechat_container()` | T5, T6, T7 |

### Fase 3: Portal API (sequentieel)

| Stap | Taak | Afhankelijkheid |
|------|------|-----------------|
| 9 | T9: GET endpoint | T1, T2 |
| 10 | T10: PUT endpoint | T9, T2 |
| 11 | T11: POST test endpoint | T10 |

### Fase 4: Portal UI + i18n (parallel uitvoerbaar)

| Stap | Taak | Afhankelijkheid |
|------|------|-----------------|
| 12 | T12: Route `/admin/integrations` | T9 (API moet beschikbaar zijn) |
| 13 | T13: Sidebar link | T12 |
| 14 | T14: MCP server kaarten | T12 |
| 15 | T15: i18n vertalingen (NL + EN) | T12 |

### Fase 5: Productie (sequentieel)

| Stap | Taak | Afhankelijkheid |
|------|------|-----------------|
| 16 | T16: Productie-migratie getklai | T4, T8 |
| 17 | T17: End-to-end verificatie | T16, T14 |

---

## 5. Kritieke bestanden

### Bestaande bestanden (worden gewijzigd)

| Bestand | Wijziging |
|---------|-----------|
| `klai-portal/backend/app/services/secrets.py` | Toevoegen `encrypt_mcp_secret()`, `decrypt_mcp_secret()` |
| `klai-portal/backend/app/services/provisioning.py` | Update `_generate_librechat_yaml()`, `_generate_librechat_env()`, nieuwe `_flush_redis_and_restart_librechat()` |
| `deploy/librechat/librechat.yaml` | Geen wijziging -- base template blijft ongewijzigd |

### Nieuwe bestanden

| Bestand | Doel |
|---------|------|
| `deploy/librechat/mcp_catalog.yaml` | MCP server whitelist en config templates |
| `klai-portal/backend/alembic/versions/{hash}_fix_mcp_servers_seed_data.py` | Alembic migratie |
| `klai-portal/backend/app/api/mcp_servers.py` | Portal API endpoints |
| `klai-portal/frontend/src/routes/admin/integrations/index.tsx` | Integratiepagina |
| `klai-portal/frontend/src/routes/admin/integrations/components/McpServerCard.tsx` | Server kaart component |
| `klai-portal/frontend/src/routes/admin/integrations/components/McpTestButton.tsx` | Test verbinding component |
| Paraglide message bestanden | i18n vertalingen NL + EN |

### Bestanden die NIET gewijzigd worden

| Bestand | Reden |
|---------|-------|
| `klai-portal/backend/app/models/portal.py` | `mcp_servers` kolom bestaat al |
| `deploy/docker-compose.yml` | Geen wijzigingen nodig |
| `klai-portal/backend/alembic/versions/d2e3f4a5b6c7_*.py` | Oude migratie blijft ongewijzigd; nieuwe migratie corrigeert de data |
