---
id: SPEC-INFRA-001
version: "2.0.0"
status: completed
created: 2026-04-02
updated: 2026-04-02
author: MoAI
priority: high
---

# SPEC-INFRA-001: Per-tenant MCP Configuratie voor LibreChat

## HISTORY

| Versie | Datum | Auteur | Wijziging |
|--------|-------|--------|-----------|
| 2.0.0 | 2026-04-02 | MoAI | DB-driven MCP configuratie (was: statische yaml bestanden) |
| 1.0.0 | 2026-04-02 | MoAI | Initieel SPEC document |

---

## 1. Context

Klai is een privacy-first, EU-only AI platform. Elke tenant krijgt een geisoleerde LibreChat container. Momenteel delen alle tenants dezelfde `deploy/librechat/librechat.yaml`, waardoor MCP-servers niet per tenant geconfigureerd kunnen worden. Het directe doel is het toevoegen van een Twenty CRM MCP-server exclusief voor de `getklai` tenant.

### Ontdekte bugs

1. **KLAI_ORG_ID mismatch**: `librechat.yaml:40` gebruikte `${KLAI_ORG_ID}` maar `provisioning.py:264` stelt `KLAI_ZITADEL_ORG_ID` in. De MCP-header `X-Org-ID` was leeg. (Bugfix al toegepast in base yaml.)
2. **KNOWLEDGE_INGEST_SECRET ontbreekt**: Wordt niet meegegeven aan LibreChat containers. De MCP-header `X-Internal-Secret` is leeg.

### Architectuurbeslissing

Per-tenant MCP configuratie wordt opgeslagen als JSON in de database (`PortalOrg.mcp_servers`), niet als statische yaml-bestanden. Voordelen:
- Schaalbaar: geen handmatig yaml-beheer per tenant
- Base config blijft in git, tenant-specifieke MCP servers in de DB
- Provisioning genereert per-tenant yaml automatisch door base + DB te mergen

Beperking: LibreChat leest yaml alleen bij container startup. MCP config wijzigingen vereisen een container restart.

### Betreffende bestanden

| Bestand | Doel |
|---------|------|
| `deploy/librechat/librechat.yaml` | Base MCP + model configuratie (gedeeld template) |
| `deploy/docker-compose.yml` | Pre-provisioned tenant containers |
| `klai-portal/backend/app/models/portal.py` | PortalOrg model |
| `klai-portal/backend/app/services/provisioning.py` | Tenant-aanmaak + yaml generatie |

---

## 2. Assumptions

**[A-001]** LibreChat ondersteunt environment variable expansion (`${VAR}`) in `librechat.yaml` voor alle `mcpServers`-configuratie. **Confidence: High** — bevestigd in research.

**[A-002]** De LibreChat Docker image (`ghcr.io/danny-avila/librechat:latest`) bevat `npx` voor het uitvoeren van stdio MCP-servers. **Confidence: Medium** — image is Node.js-based, moet geverifieerd worden.

**[A-003]** De Twenty CRM MCP-server (`twenty-mcp-server` npm package) is stabiel genoeg voor productie-gebruik. **Confidence: Medium** — 29 tools, actief onderhouden (maart 2026).

**[A-004]** PyYAML is beschikbaar in de portal-backend omgeving voor yaml generatie. **Confidence: High** — standaard Python dependency.

---

## 3. Requirements (EARS Format)

### 3.1 Ubiquitous Requirements

**[REQ-U-001]** Het systeem SHALL alle MCP-server communicatie exclusief uitvoeren binnen EU-infrastructuur.

**[REQ-U-002]** Het systeem SHALL geen tenant-specifieke credentials (API keys, secrets) opslaan in git-getrackte bestanden of in de database.

### 3.2 Event-Driven Requirements

**[REQ-E-001]** WHEN `provisioning.py` een LibreChat container aanmaakt of herstart, THEN SHALL het systeem een per-tenant `librechat.yaml` genereren door de base configuratie te mergen met de `mcp_servers` JSON uit `PortalOrg`.

**[REQ-E-002]** WHEN een gebruiker in de `getklai` tenant een chat start, THEN SHALL het systeem de Twenty CRM MCP-tools beschikbaar stellen naast de bestaande klai-knowledge MCP-tools.

**[REQ-E-003]** WHEN een gebruiker in een andere tenant een chat start, THEN SHALL het systeem uitsluitend de geconfigureerde MCP-servers voor die tenant gebruiken (standaard: alleen klai-knowledge).

**[REQ-E-004]** WHEN de `klai-knowledge` MCP-server een request ontvangt, THEN SHALL de `X-Internal-Secret` header het correcte `KNOWLEDGE_INGEST_SECRET` bevatten (niet leeg).

**[REQ-E-005]** WHEN `provisioning.py` een nieuwe tenant aanmaakt, THEN SHALL het gegenereerde `.env` bestand de variabele `KNOWLEDGE_INGEST_SECRET` bevatten.

### 3.3 Unwanted Requirements

**[REQ-N-001]** Het systeem SHALL NIET de Twenty CRM MCP-server beschikbaar stellen aan andere tenants dan `getklai`.

**[REQ-N-002]** Het systeem SHALL NIET `TWENTY_API_KEY` of `TWENTY_BASE_URL` environment variables blootstellen aan niet-`getklai` containers.

---

## 4. Specifications

### 4.1 Database schema

Nieuw veld op `PortalOrg`:

```python
mcp_servers: Mapped[dict | None] = mapped_column(JSON, nullable=True)
```

JSON structuur (per MCP server):

```json
{
  "twenty-crm": {
    "type": "stdio",
    "command": "npx",
    "args": ["-y", "twenty-mcp-server", "start"],
    "timeout": 60000,
    "initTimeout": 30000,
    "env": {
      "TWENTY_API_KEY": "${TWENTY_API_KEY}",
      "TWENTY_BASE_URL": "${TWENTY_BASE_URL}"
    }
  }
}
```

Merk op: `env` waarden gebruiken `${VAR}` syntax — de daadwerkelijke secrets staan in de `.env` op de server, niet in de DB.

### 4.2 Yaml generatie

Nieuwe functie `_generate_librechat_yaml(extra_mcp_servers: dict | None)` in `provisioning.py`:

1. Laad de base yaml uit `deploy/librechat/librechat.yaml`
2. Als `extra_mcp_servers` niet None is:
   - Merge in `mcpServers` sectie
   - Voeg server namen toe aan `modelSpecs.list[].mcpServers` array
3. Schrijf resultaat naar `librechat/{slug}/librechat.yaml`
4. Mount per-tenant yaml in container volume

### 4.3 Bugfix KNOWLEDGE_INGEST_SECRET

De variabele `KNOWLEDGE_INGEST_SECRET` wordt toegevoegd aan:

1. `deploy/docker-compose.yml` — environment-sectie van `librechat-klai` en `librechat-getklai`.
2. `provisioning.py` — `.env` template voor dynamische tenants.

### 4.4 Docker-compose volume mount

Voor `librechat-getklai`: volume mount wijzigt naar per-tenant yaml:

```yaml
- ./librechat/getklai/librechat.yaml:/app/librechat.yaml:ro
```

---

## 5. Traceability

| Requirement | Plan taak | Acceptance criterium |
|-------------|-----------|---------------------|
| REQ-E-001 | T1, T2, T3 | AC1, AC2 |
| REQ-E-002 | T1, T2, T4 | AC1 |
| REQ-E-003 | T2 | AC2 |
| REQ-E-004 | T3 | AC4 |
| REQ-E-005 | T3 | AC5 |
| REQ-N-001 | T1, T2 | AC2 |
| REQ-N-002 | T4 | AC2 |
| REQ-U-001 | Alle | Alle |
| REQ-U-002 | T4 | Alle |
