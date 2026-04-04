---
id: SPEC-INFRA-001
version: "3.2.0"
status: partial
created: 2026-04-02
updated: 2026-04-02
author: MoAI
priority: high
---

# SPEC-INFRA-001: Per-tenant MCP Configuratie voor LibreChat

## HISTORY

| Versie | Datum | Auteur | Wijziging |
|--------|-------|--------|-----------|
| 3.2.0 | 2026-04-02 | MoAI | Sync: acceptatiecriteria beoordeeld, Definition of Done bijgewerkt, learnings vastgelegd |
| 3.1.0 | 2026-04-02 | MoAI | Status update: jezweb vervangen door Twenty built-in MCP; DB-driven implementatie geparkeerd pending MCP management onderzoek |
| 3.0.0 | 2026-04-02 | MoAI | MCP Catalog + encrypted secrets in DB; interne service URL voor klai-knowledge |
| 2.0.0 | 2026-04-02 | MoAI | DB-driven MCP configuratie (was: statische yaml bestanden) |
| 1.0.0 | 2026-04-02 | MoAI | Initieel SPEC document |

---

## 1. Context

Klai is een privacy-first, EU-only AI platform. Elke tenant krijgt een geisoleerde LibreChat container. Momenteel delen alle tenants dezelfde `deploy/librechat/librechat.yaml`, waardoor MCP-servers niet per tenant geconfigureerd kunnen worden. Het directe doel is het toevoegen van een Twenty CRM MCP-server exclusief voor de `getklai` tenant.

### Ontdekte bugs

1. **KLAI_ORG_ID mismatch**: `librechat.yaml:40` gebruikte `${KLAI_ORG_ID}` maar `provisioning.py:264` stelt `KLAI_ZITADEL_ORG_ID` in. De MCP-header `X-Org-ID` was leeg. (Bugfix al toegepast in base yaml.)
2. **KNOWLEDGE_INGEST_SECRET ontbreekt**: Wordt niet meegegeven aan LibreChat containers. De MCP-header `X-Internal-Secret` is leeg.

### Architectuurbeslissing v2: MCP Catalog + encrypted secrets

Per-tenant MCP configuratie bestaat uit twee lagen:

**Laag 1 — MCP Catalog (codebase)**
Een lijst van door Klai ondersteunde MCP servers, opgeslagen in `deploy/librechat/mcp_catalog.yaml`. Elke entry bevat:
- `id`: unieke identifier (bijv. `twenty-crm`)
- `config_template`: LibreChat yaml config met `${VAR}` placeholders
- `required_env_vars`: welke secrets de tenant moet aanleveren
- `description`: wat de server doet

Alleen catalog-entries kunnen door tenants worden ingeschakeld. Dit voorkomt willekeurige externe MCP servers.

**Laag 2 — Per-tenant activatie (DB)**
`PortalOrg.mcp_servers` slaat op welke catalog-entries actief zijn én de tenant-specifieke configuratie:

```json
{
  "twenty-crm": {
    "enabled": true,
    "env": {
      "TWENTY_API_KEY": "<fernet-encrypted>",
      "TWENTY_BASE_URL": "https://crm.getklai.com"
    }
  }
}
```

Secrets worden **Fernet-encrypted opgeslagen** (met de portal-api `SECRET_KEY`). Bij yaml-generatie decrypt provisioning.py de waarden en schrijft ze naar de tenant `.env` — nooit plaintext in DB of git.

**Laag 3 — Interne service URL voor klai-knowledge**
`klai-knowledge` MCP gebruikt een intern Docker-network URL per tenant: `http://{slug}-knowledge-mcp:8080/mcp`. Geen publiek domein variabele nodig. Dit elimineert internet-roundtrips en vermindert afhankelijkheid van public DNS.

**Voordelen ten opzichte van v1:**
- Tenants kunnen via portal UI self-service MCP servers aanzetten
- Secrets encrypted at rest, nooit plaintext in DB of git
- Catalog beperkt attack surface (geen willekeurige externe MCP)
- Interne URLs voor klai-knowledge: lager latency, meer resilient

**Beperking:** LibreChat leest yaml alleen bij container startup. MCP config wijzigingen vereisen een container restart.

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

**[A-002]** De LibreChat Docker image (`ghcr.io/danny-avila/librechat:latest`) bevat `npx` voor het uitvoeren van stdio MCP-servers. **Confidence: High** — bevestigd in productie. Kanttekening: `npx -y <package> start` faalt in de LibreChat container omdat turbo de LibreChat monorepo-workspace oppikt. Workaround: `npm install --prefix /tmp/<pkg> <package> && node /tmp/<pkg>/node_modules/<package>/dist/index.js`.

**[A-003]** ~~De Twenty CRM MCP-server (`twenty-mcp-server` npm package) is stabiel genoeg voor productie-gebruik.~~ **VERVALLEN** — jezweb/twenty-mcp community package is abandoned: 20/29 tools broken. Twee bevestigde bugs: `create_note` gebruikt `body` i.p.v. `bodyV2`; `create_comment` gebruikt `CommentCreateInput` dat niet meer bestaat in de API. **Vervangen door Twenty built-in MCP server** (zie §4.1 update).

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

### 4.1 MCP Catalog

> **Status update (v3.1.0):** de `jezweb/twenty-mcp-server` stdio-aanpak is vervangen door de built-in MCP server die Twenty zelf levert. De Twenty MCP server is beschikbaar op `https://crm.getklai.com/mcp` (streamable-http transport, JSON-RPC). De `IS_AI_ENABLED` feature flag is handmatig ingesteld in de PostgreSQL `core.featureFlag` tabel en de Redis workspace-cache is gecleared. De huidige `librechat.yaml` voor getklai tenant is al bijgewerkt op de server.

**Huidige config (getklai, `/opt/klai/librechat/getklai/librechat.yaml` op core-01):**

```yaml
twenty-crm:
  type: streamable-http
  url: https://crm.getklai.com/mcp
  headers:
    Authorization: 'Bearer ${TWENTY_API_KEY}'
```

**Gewenste config_template in `mcp_catalog.yaml` (DB-driven, nog niet geïmplementeerd):**

```yaml
servers:
  twenty-crm:
    description: "Twenty CRM — contacten, bedrijven, deals, taken"
    required_env_vars:
      - TWENTY_API_KEY
      - TWENTY_BASE_URL
    config_template:
      type: streamable-http
      url: "${TWENTY_BASE_URL}/mcp"
      headers:
        Authorization: "Bearer ${TWENTY_API_KEY}"
```

**Beschikbare tools via Twenty MCP:** `http_request` (Twenty REST API), `send_email`, `search_help_center`.

**System prompt (huidig, actief in librechat.yaml):**

```yaml
systemPrompt: >-
  You are a helpful AI assistant. Always respond in the same language the user
  writes in — if they write Dutch, reply in Dutch; if English, reply in English.
  ## Twenty CRM
  Use http_request_mcp_twenty-crm tool. Base URL already configured.
  Notes require bodyV2 (NOT body):
    { "title": "...", "bodyV2": { "markdown": "...", "blocknote": null } }
  POST /objects/noteTargets to link notes to records.
```

### 4.2 Database schema

`PortalOrg.mcp_servers` (reeds aanwezig):

```python
mcp_servers: Mapped[dict | None] = mapped_column(JSON, nullable=True)
```

JSON structuur (per tenant, per actieve MCP server):

```json
{
  "twenty-crm": {
    "enabled": true,
    "env": {
      "TWENTY_API_KEY": "<fernet-encrypted-value>",
      "TWENTY_BASE_URL": "https://crm.example.com"
    }
  }
}
```

Regels:
- Alleen catalog-IDs zijn toegestaan als keys
- `TWENTY_BASE_URL` en andere niet-geheime waarden worden plaintext opgeslagen
- API keys en secrets worden altijd Fernet-encrypted opgeslagen
- `provisioning.py` decrypt bij yaml-generatie en schrijft naar `.env`

### 4.3 Yaml generatie

`_generate_librechat_yaml(slug, mcp_servers_db)` in `provisioning.py`:

1. Laad de base yaml uit `deploy/librechat/librechat.yaml`
2. Laad de MCP catalog uit `deploy/librechat/mcp_catalog.yaml`
3. Voor elke `enabled: true` entry in `mcp_servers_db`:
   - Valideer dat het catalog-ID bestaat (anders skip + log warning)
   - Merge `config_template` in `mcpServers` sectie
   - Voeg server naam toe aan `modelSpecs.list[].mcpServers`
4. Schrijf resultaat naar `librechat/{slug}/librechat.yaml`

Env vars voor secrets: `provisioning.py` decrypt de Fernet-waarden en voegt de echte secrets toe aan de tenant `.env`.

### 4.4 klai-knowledge interne URL

`klai-knowledge` MCP gebruikt geen publiek domein. URL-patroon per tenant:

```
http://{slug}-knowledge-mcp:8080/mcp
```

Dit vereist dat de LibreChat container op hetzelfde Docker-netwerk zit als de knowledge-mcp container van die tenant.

### 4.5 Bugfix KNOWLEDGE_INGEST_SECRET

De variabele `KNOWLEDGE_INGEST_SECRET` wordt toegevoegd aan:

1. `deploy/docker-compose.yml` — environment-sectie van `librechat-klai` en `librechat-getklai`. ✅ Gedaan
2. `provisioning.py` — `.env` template voor dynamische tenants. ✅ Gedaan

### 4.6 Docker-compose volume mount

Voor `librechat-getklai`: volume mount per-tenant yaml:

```yaml
- ./librechat/getklai/librechat.yaml:/app/librechat.yaml:ro
```
✅ Gedaan

---

## 5. Toekomstige Architectuur (niet geïmplementeerd)

### 5.1 Per-tenant MCP containers (target)

De huidige stdio-in-LibreChat aanpak is een werkbare tussenstap. De target-architectuur:

- Elke actieve MCP server per tenant draait als **eigen Docker container** (SSE transport)
- Provisioning beheert de lifecycle: container aanmaken bij activatie, stoppen bij deactivatie
- LibreChat verbindt via intern netwerk URL: `http://{slug}-{server-id}-mcp:{port}/sse`
- **Geen fork van bestaande MCP packages** — officiële npm packages, eigen container

Voordeel: volledige lifecycle-isolatie, geen resource-conflict met LibreChat, restartbaar zonder LibreChat te raken.

### 5.2 MCP Catalog (target)

`deploy/librechat/mcp_catalog.yaml` beschrijft ondersteunde servers:

```yaml
servers:
  twenty-crm:
    description: "Twenty CRM — contacten, bedrijven, deals, taken"
    docker_image: "node:20-alpine"
    start_command: "npx -y twenty-mcp-server"
    transport: sse
    port: 3000
    required_env_vars:
      - TWENTY_API_KEY      # encrypted in DB
      - TWENTY_BASE_URL     # plaintext in DB
```

### 5.3 Secret management (target)

- Tenant voert API key in via portal UI
- Portal-api slaat op als Fernet-encrypted JSON in `PortalOrg.mcp_servers`
- Provisioning decrypt bij container-aanmaak en schrijft naar tenant `.env`
- Nooit plaintext in DB, git of logs

### 5.4 Huidige staat (geïmplementeerd)

| Component | Status | Aanpak |
|-----------|--------|--------|
| `PortalOrg.mcp_servers` DB kolom | ✅ | JSON, migration `d2e3f4a5b6c7` |
| `_generate_librechat_yaml()` in provisioning.py | ✅ | Geïmplementeerd in `provisioning/generators.py`, aangeroepen in `infrastructure.py` |
| twenty-crm voor getklai | ✅ | Twenty built-in MCP, streamable-http naar `crm.getklai.com/mcp` |
| `IS_AI_ENABLED` feature flag | ✅ | Handmatig in `core.featureFlag` tabel + Redis cache gecleared |
| Auth header in MCP verbinding | ✅ | `Authorization: Bearer ${TWENTY_API_KEY}` in librechat.yaml headers |
| Auth header in http_request calls | ✅ workaround | Token hardcoded in system prompt (Twenty http_request injecteert geen auth) |
| API key in container env | ✅ | `TWENTY_API_KEY` in `/opt/klai/.env` |
| Secrets encrypted in DB | ✅ | AES-256-GCM via `portal_secrets.encrypt()` in SPEC-INFRA-002 (`mcp_servers.py`) |
| Per-tenant MCP containers | ❌ | Toekomstige architectuur |
| MCP Catalog yaml | ✅ | `deploy/librechat/mcp_catalog.yaml` aangemaakt in SPEC-INFRA-002 |
| Portal UI MCP management | ✅ | Integrations admin page in SPEC-INFRA-002 |
| Portal API MCP endpoints | ✅ | GET/PUT/POST `/api/mcp-servers` in SPEC-INFRA-002 |
| KNOWLEDGE_INGEST_SECRET bugfix | ✅ | Toegevoegd aan docker-compose + provisioning .env template |
| klai-knowledge interne URL | ⚠️ | getklai gebruikt nog public URL |

**Bekende beperking (hardcoded token):** De Twenty `http_request` tool injecteert de Bearer token niet automatisch. De AI moet hem zelf meegeven in elke call. Omdat `${TWENTY_API_KEY}` niet geëxpandeerd wordt in de LibreChat `systemPrompt`, is de token tijdelijk hardcoded in de system prompt op de server (`/opt/klai/librechat/getklai/librechat.yaml`). Dit is een workaround — bij rotatie van de API key moet de system prompt handmatig bijgewerkt worden.

**DB-driven implementatie geparkeerd:** De volledige DB-driven aanpak (`_generate_librechat_yaml()`, MCP Catalog, Fernet-encrypted secrets) is geparkeerd. Zie GetKlai/klai#87 voor vervolgonderzoek en oplossingsrichtingen.

---

## 6. Traceability

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
