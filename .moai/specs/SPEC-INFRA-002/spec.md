---
id: SPEC-INFRA-002
version: "1.0.0"
status: draft
created: 2026-04-02
updated: 2026-04-02
author: MoAI
priority: high
---

# SPEC-INFRA-002: DB-driven Per-tenant MCP Server Management

## HISTORY

| Versie | Datum | Auteur | Wijziging |
|--------|-------|--------|-----------|
| 1.0.0 | 2026-04-02 | MoAI | Initieel SPEC document — DB-driven provisioning, MCP Catalog, Portal API + UI |

---

## 1. Context

Dit SPEC vervolgt op **SPEC-INFRA-001** (Per-tenant MCP Configuratie voor LibreChat). SPEC-INFRA-001 heeft de architectuur ontworpen en de Twenty CRM MCP handmatig live gezet voor de `getklai` tenant. De DB-driven provisioning bleef echter geparkeerd: er is geen MCP Catalog, geen secret-encryptie in de `mcp_servers` kolom, geen Portal API, en geen Portal UI.

SPEC-INFRA-002 implementeert de volledige automatiseringsketen: van MCP Catalog YAML als whitelist, via encrypted secrets in de database, door provisioning-integratie met Redis flush + container restart, tot Portal API endpoints en een admin UI voor integratiebeheer.

### Architectuurbeslissing

**Token gaat in tenant `.env` via provisioning** (gedecrypteerd uit encrypted DB). LibreChat expandeert `${TWENTY_API_KEY}` in MCP headers bij container startup. Het system prompt bevat uitsluitend gedragsinstructies, nooit het token. `_generate_librechat_yaml()` gebruikt `mcp_catalog.yaml` als whitelist en template-bron.

### Betreffende bestanden (bestaand)

| Bestand | Doel |
|---------|------|
| `deploy/librechat/librechat.yaml` | Base MCP + model configuratie (gedeeld template) |
| `klai-portal/backend/app/models/portal.py` | PortalOrg model met `mcp_servers` JSON kolom |
| `klai-portal/backend/app/services/provisioning.py` | Tenant-aanmaak, `_generate_librechat_yaml()`, `_generate_librechat_env()` |
| `klai-portal/backend/app/services/secrets.py` | AES-256-GCM encryptie (PortalSecretsService) |
| `klai-portal/backend/alembic/versions/d2e3f4a5b6c7_*.py` | Bestaande migratie met verouderde stdio seed data |

### Betreffende bestanden (nieuw)

| Bestand | Doel |
|---------|------|
| `deploy/librechat/mcp_catalog.yaml` | Whitelist van ondersteunde MCP servers met config templates |
| `klai-portal/backend/app/services/secrets.py` | Uitbreiding: `encrypt_mcp_secret()` / `decrypt_mcp_secret()` |
| `klai-portal/backend/app/api/mcp_servers.py` | Portal API: GET/PUT/POST test endpoints |
| `klai-portal/frontend/src/routes/admin/integrations/` | Portal UI: integratiepagina + sidebar link |

---

## 2. Assumptions

**[A-001]** LibreChat ondersteunt environment variable expansion (`${VAR}`) in `librechat.yaml` voor alle `mcpServers`-configuratie, inclusief headers. **Confidence: High** -- bewezen in productie voor `klai-knowledge` en `twenty-crm` op de `getklai` tenant.

**[A-002]** De bestaande `PortalSecretsService` (AES-256-GCM) is geschikt als basis voor MCP secret encryptie. De huidige implementatie encrypt al OIDC secrets en LiteLLM keys. **Confidence: High** -- draait in productie.

**[A-003]** LibreChat cached de geparsede `librechat.yaml` in Redis onder key `CacheKeys.APP_CONFIG` met onbeperkte TTL. Een Redis `FLUSHALL` is nodig voordat een container restart config-wijzigingen oppikt. **Confidence: High** -- gedocumenteerd in pitfalls/platform.md en bevestigd in research.

**[A-004]** De `mcp_servers` JSON kolom in `PortalOrg` is reeds aanwezig (migratie `d2e3f4a5b6c7`) maar bevat verouderde seed data in stdio-formaat voor de `getklai` tenant. De migratie moet gecorrigeerd worden. **Confidence: High** -- geverifieerd in codebase.

**[A-005]** De Portal frontend gebruikt React 19 + Vite + TanStack Router + Mantine 8 + Paraglide i18n. Nieuwe routes volgen het bestaande patroon in `klai-portal/frontend/src/routes/`. **Confidence: High** -- vastgelegd in project tech stack.

**[A-006]** Redis `FLUSHALL` wist alle keys, niet alleen LibreChat config. Actieve LibreChat-sessies verliezen cached state (chatgeschiedenis, user preferences). **Confidence: Medium** -- afgeleid uit Redis single-database setup; exacte impact op sessies vereist validatie.

---

## 3. Requirements (EARS Format)

### 3.1 Ubiquitous Requirements

**[REQ-U-001]** Het systeem SHALL alle MCP-server configuratie uitsluitend beheren via de MCP Catalog whitelist (`mcp_catalog.yaml`). Entries die niet in de catalog staan, worden geweigerd.

**[REQ-U-002]** Het systeem SHALL MCP secrets (API keys, tokens) altijd encrypted opslaan in de database en nooit plaintext loggen, printen of opslaan in git-getrackte bestanden.

**[REQ-U-003]** Het systeem SHALL MCP secrets nooit in system prompts plaatsen. Tokens worden uitsluitend via environment variabelen en YAML header-configuratie doorgegeven.

### 3.2 Event-Driven Requirements

**[REQ-E-001]** WHEN een tenant-admin een MCP server activeert via de Portal API, THEN SHALL het systeem de configuratie valideren tegen de MCP Catalog, de secrets encrypten, en opslaan in `PortalOrg.mcp_servers`.

**[REQ-E-002]** WHEN `provisioning.py` een LibreChat container aanmaakt of herstart, THEN SHALL het systeem:
  1. De MCP Catalog laden als whitelist en template-bron
  2. Per actieve catalog-entry de `config_template` mergen in de tenant `librechat.yaml`
  3. De encrypted secrets decrypten en schrijven naar de tenant `.env`

**[REQ-E-003]** WHEN een MCP-configuratiewijziging wordt doorgevoerd via de Portal API, THEN SHALL het systeem de betreffende Redis cache flushen en de LibreChat container herstarten om de wijziging te activeren.

**[REQ-E-004]** WHEN een tenant-admin een `POST /api/orgs/{org_id}/mcp-servers/{server_id}/test` request stuurt, THEN SHALL het systeem de connectiviteit naar de MCP server valideren en het resultaat rapporteren.

**[REQ-E-005]** WHEN de Portal UI de integratiepagina laadt, THEN SHALL het systeem de beschikbare MCP servers uit de catalog tonen, met per server de activatiestatus en configuratievelden voor de huidige tenant.

### 3.3 Unwanted Requirements

**[REQ-N-001]** Het systeem SHALL NIET gedecrypteerde MCP secrets loggen via structlog, print, of enige andere logging-methode. Dit geldt voor alle log levels inclusief DEBUG.

**[REQ-N-002]** Het systeem SHALL NIET een MCP server activeren die niet voorkomt in `mcp_catalog.yaml`, zelfs niet via directe database manipulatie.

**[REQ-N-003]** Het systeem SHALL NIET de DB-driven provisioning activeren voordat de Alembic migratie de verouderde stdio seed data heeft gecorrigeerd naar het juiste streamable-http formaat.

### 3.4 Optional Requirements

**[REQ-O-001]** Waar mogelijk, SHALL het systeem een audit log bijhouden van MCP-configuratiewijzigingen (wie, wanneer, welke server, welke actie).

**[REQ-O-002]** Waar mogelijk, SHALL het systeem selectief Redis keys flushen (alleen `CacheKeys.APP_CONFIG` voor de betreffende tenant) in plaats van `FLUSHALL`.

### 3.5 State-Driven Requirements

**[REQ-S-001]** IF een MCP server entry in `PortalOrg.mcp_servers` de status `enabled: false` heeft, THEN SHALL het systeem deze server NIET opnemen in de gegenereerde `librechat.yaml` voor die tenant.

**[REQ-S-002]** IF de MCP Catalog geen entry bevat voor een server-ID dat in de database staat, THEN SHALL het systeem deze entry negeren, een warning loggen, en doorgaan met de overige servers.

---

## 4. Specifications

### 4.1 Module 1: MCP Catalog (`deploy/librechat/mcp_catalog.yaml`)

Nieuw bestand dat fungeert als whitelist en template-bron voor alle ondersteunde MCP servers.

```yaml
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

Regels:
- Alleen entries in dit bestand kunnen door tenants worden geactiveerd
- `config_template` bevat `${VAR}` placeholders die LibreChat bij startup expandeert
- `required_env_vars` definieert welke variabelen de tenant moet aanleveren
- `system_prompt_hint` is optioneel en bevat gedragsinstructies (nooit secrets)

### 4.2 Module 2: Secrets en Alembic Migratie

#### 4.2.1 Secret helpers in `secrets.py`

Twee nieuwe functies in `klai-portal/backend/app/services/secrets.py`:

- `encrypt_mcp_secret(plaintext: str) -> str` -- encrypts met bestaande `PortalSecretsService`, retourneert base64-encoded ciphertext
- `decrypt_mcp_secret(ciphertext: str) -> str` -- decrypts base64-encoded ciphertext, retourneert plaintext

Deze functies wrappen de bestaande `PortalSecretsService.encrypt()` / `decrypt()` met base64 encoding zodat de resultaten veilig in JSON opgeslagen kunnen worden (bytes zijn niet JSON-serializable).

#### 4.2.2 Alembic migratie: corrigeer seed data

Nieuwe migratie die de verouderde stdio seed data in `PortalOrg.mcp_servers` voor de `getklai` tenant vervangt door het correcte streamable-http formaat:

```json
{
  "twenty-crm": {
    "enabled": true,
    "env": {
      "TWENTY_API_KEY": "<encrypted-value>",
      "TWENTY_BASE_URL": "https://crm.getklai.com"
    }
  }
}
```

### 4.3 Module 3: Provisioning Updates

#### 4.3.1 `_generate_librechat_yaml()` update

Huidige implementatie merged `mcp_servers` dict direct in YAML. Nieuwe versie:

1. Laad `mcp_catalog.yaml` als whitelist
2. Loop over `mcp_servers` entries uit DB waar `enabled: true`
3. Voor elke entry: valideer dat `server_id` in catalog bestaat
4. Gebruik `config_template` uit catalog (niet uit DB) als YAML-template
5. Voeg server naam toe aan `modelSpecs.list[].mcpServers`
6. Voeg optioneel `system_prompt_hint` toe aan model spec

#### 4.3.2 `_generate_librechat_env()` update

Voeg MCP-specifieke environment variabelen toe aan de tenant `.env`:

1. Loop over `mcp_servers` entries uit DB waar `enabled: true`
2. Decrypt elke secret env var met `decrypt_mcp_secret()`
3. Schrijf als `{VAR_NAME}={decrypted_value}` naar `.env`

#### 4.3.3 `_flush_redis_and_restart_librechat()`

Nieuwe functie die:

1. Redis `FLUSHALL` uitvoert via Docker exec (`docker exec redis redis-cli FLUSHALL`)
2. De LibreChat container herstart via Docker SDK
3. Wacht op health check (max 30 seconden)
4. Logt het resultaat (zonder secrets)

### 4.4 Module 4: Portal API (`api/mcp_servers.py`)

Drie endpoints onder `/api/orgs/{org_id}/mcp-servers`:

| Method | Path | Doel |
|--------|------|------|
| `GET` | `/api/orgs/{org_id}/mcp-servers` | Lijst van MCP servers met activatiestatus |
| `PUT` | `/api/orgs/{org_id}/mcp-servers/{server_id}` | Activeer/deactiveer + configureer MCP server |
| `POST` | `/api/orgs/{org_id}/mcp-servers/{server_id}/test` | Test connectiviteit naar MCP server |

**GET response** combineert catalog data met tenant-specifieke status:

```json
[
  {
    "id": "twenty-crm",
    "description": "Twenty CRM -- contacten, bedrijven, deals, taken",
    "enabled": true,
    "required_env_vars": ["TWENTY_API_KEY", "TWENTY_BASE_URL"],
    "configured_env_vars": ["TWENTY_API_KEY", "TWENTY_BASE_URL"]
  }
]
```

**PUT request body:**

```json
{
  "enabled": true,
  "env": {
    "TWENTY_API_KEY": "sk-xxx-plaintext-input",
    "TWENTY_BASE_URL": "https://crm.getklai.com"
  }
}
```

De API encrypt secrets automatisch voor opslag. Configured env vars worden in de GET response als lijst van keys getoond, nooit als waarden.

**POST test** stuurt een minimale JSON-RPC request naar de MCP server URL met de opgegeven credentials en rapporteert succes/falen.

### 4.5 Module 5: Portal UI (`/admin/integrations`)

Nieuwe route in de Portal frontend:

- **Route:** `/admin/integrations`
- **Sidebar:** Nieuw menu-item "Integraties" onder de admin sectie
- **i18n:** Nederlandse en Engelse vertalingen via Paraglide

De pagina toont:
- Lijst van beschikbare MCP servers (uit catalog via API)
- Per server: activatiestatus toggle, configuratieformulier voor env vars
- Secret velden gebruiken password input type (gemaskeerd)
- "Test verbinding" knop per server
- Opslaan triggert PUT endpoint, waarna provisioning de container herstart

---

## 5. Buiten scope

- Per-tenant MCP containers (SSE transport) -- toekomstige architectuur
- Upstream Twenty PR voor `http_request` auth injection
- Automatisering van `IS_AI_ENABLED` feature flag in Twenty
- Migratie van klai-knowledge naar interne Docker-network URL
- Audit logging van MCP-configuratiewijzigingen (optioneel, REQ-O-001)

---

## 6. Traceability

| Requirement | Module | Plan taken | Acceptance criteria |
|-------------|--------|------------|---------------------|
| REQ-U-001 | M1, M3 | T1, T5-T8 | AC-M1-01, AC-M3-01 |
| REQ-U-002 | M2, M3, M4 | T2-T4, T9-T11 | AC-M2-01, AC-M2-02, AC-M4-02 |
| REQ-U-003 | M1, M3 | T1, T5-T8 | AC-M1-02, AC-M3-02 |
| REQ-E-001 | M2, M4 | T2-T4, T9-T11 | AC-M4-01 |
| REQ-E-002 | M1, M2, M3 | T1-T8 | AC-M3-01, AC-M3-02 |
| REQ-E-003 | M3 | T7-T8 | AC-M3-03 |
| REQ-E-004 | M4 | T11 | AC-M4-03 |
| REQ-E-005 | M4, M5 | T12-T15 | AC-M5-01 |
| REQ-N-001 | M2, M3 | T2-T8 | AC-M2-02 |
| REQ-N-002 | M1, M3 | T1, T5-T8 | AC-M3-01 |
| REQ-N-003 | M2 | T3-T4 | AC-M2-03 |
| REQ-O-001 | -- | -- | Buiten scope |
| REQ-O-002 | M3 | T7 | AC-M3-04 |
| REQ-S-001 | M3 | T5-T6 | AC-M3-05 |
| REQ-S-002 | M3 | T5-T6 | AC-M3-06 |
