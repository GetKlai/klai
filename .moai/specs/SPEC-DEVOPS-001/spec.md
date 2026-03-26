---
id: SPEC-DEVOPS-001
version: 1.0.0
status: draft
created: 2026-03-26
updated: 2026-03-26
author: MoAI
priority: high
issue_number: 0
---

# SPEC-DEVOPS-001: Observability Stack -- Structured Logging & Centralized Log Collection

## HISTORY

| Versie | Datum      | Auteur | Wijziging                |
|--------|------------|--------|--------------------------|
| 1.0.0  | 2026-03-26 | MoAI   | Initieel SPEC-document   |

---

## Samenvatting

Dit SPEC beschrijft de implementatie van een volledig observability-platform voor het Klai-ecosysteem. Het omvat structured logging (JSON via structlog) voor alle 8 Python-services, gecentraliseerde log collection via Grafana Alloy, opslag in VictoriaLogs, dashboards in Grafana, en cross-server log shipping van public-01 naar core-01.

---

## Module 1: Structured Logging -- Python Services

### Omgeving

- 8 Python-services draaien als Docker-containers op core-01
- Alle services schrijven momenteel plain text naar stdout
- Geen van de services gebruikt structlog of JSON-logging
- Sommige services gebruiken `logging.basicConfig()`, andere hebben ad-hoc logging

### Aannames

- Alle Python-services ondersteunen Python 3.11+ (structlog-compatibel)
- Elke service heeft een `pyproject.toml` voor dependency management
- Services worden herstart na configuratiewijzigingen (standaard Docker-workflow)

### Requirements

**REQ-LOG-001** (Event-Driven):
WHEN een Python-service een logmelding schrijft, THEN SHALL de service een JSON-object naar stdout emitten met de velden: `timestamp` (ISO 8601), `level`, `service` (Docker service-naam), `logger` (module-naam), `event` (bericht), en eventuele gebonden contextvelden.

**REQ-LOG-002** (State-Driven):
IF de environment variable `LOG_FORMAT=console` is gezet, THEN SHALL de service human-readable tekst emitten in plaats van JSON (voor lokale ontwikkeling).

**REQ-LOG-003** (Unwanted):
WHERE een Python-service `logging.basicConfig()` gebruikt, SHALL dit vervangen worden door een structlog setup-functie. Directe `logging.basicConfig()`-aanroepen zijn verboden na implementatie.

**REQ-LOG-004** (Ubiquitous):
Elke Python-service SHALL altijd `logger = logging.getLogger(__name__)` gebruiken als standaard logger-naamgeving. Variaties zoals `log = logging.getLogger(...)` zijn niet toegestaan.

**REQ-LOG-005** (Event-Driven):
WHEN een HTTP-request binnenkomt in portal-api, THEN SHALL een FastAPI middleware via `structlog.contextvars.bind_contextvars()` de velden `org_id`, `user_id` en `request_id` binden, zodat alle logs binnen die request-context deze velden automatisch bevatten zonder dat modules ze handmatig hoeven mee te geven.

### Specificaties

- Library: `structlog` met stdlib-integratie (`structlog.stdlib`)
- Renderer: `structlog.dev.ConsoleRenderer` voor console-modus, `structlog.processors.JSONRenderer` voor productie
- Processor chain: `add_log_level`, `TimeStamper(fmt="iso")`, `StackInfoRenderer`, `format_exc_info`, renderer
- Service-naam wordt gebonden via `structlog.contextvars.bind_contextvars(service=SERVICE_NAME)`
- `SERVICE_NAME` wordt gelezen uit de `SERVICE_NAME` environment variable (fallback: hardcoded per service)

### Traceability

| Requirement | Plan referentie       | Acceptance test          |
|-------------|-----------------------|--------------------------|
| REQ-LOG-001 | Phase 2, per service  | AC-MOD1-001              |
| REQ-LOG-002 | Phase 2, setup func   | AC-MOD1-002              |
| REQ-LOG-003 | Phase 2, per service  | AC-MOD1-003 (impliciet)  |
| REQ-LOG-004 | Phase 4, ruff regels  | AC-MOD1-004 (ruff check) |
| REQ-LOG-005 | Phase 2, portal-api   | AC-MOD1-005              |

---

## Module 2: Alloy Log Collection -- core-01

### Omgeving

- Grafana Alloy draait als container op core-01 in het `monitoring` Docker-netwerk
- `deploy/alloy/config.alloy` ontbreekt in de repository (mogelijk draait Alloy zonder configuratie)
- Docker-containers schrijven logs naar de Docker daemon socket

### Aannames

- Alloy heeft toegang tot de Docker socket (`/var/run/docker.sock`)
- Het `monitoring`-netwerk is verbonden met VictoriaLogs
- Alloy-configuratie kan live herladen worden (via `/-/reload` endpoint of container restart)

### Requirements

**REQ-COLLECT-001** (Event-Driven):
WHEN een Docker-container op core-01 naar stdout/stderr schrijft, THEN SHALL Alloy de log binnen 5 seconden collecten.

**REQ-COLLECT-002** (Event-Driven):
WHEN Alloy een log collecteert, THEN SHALL het de labels `service` (uit `com.docker.compose.service`), `server=core-01`, en `env=production` toevoegen.

**REQ-COLLECT-003** (State-Driven):
IF een logregel valid JSON is, THEN SHALL Alloy het `level`-veld extraheren als label.

**REQ-COLLECT-004** (Ubiquitous):
Het systeem SHALL `deploy/alloy/config.alloy` in de repository gecommit hebben als enige bron van waarheid voor de Alloy-configuratie.

### Specificaties

- Alloy component: `loki.source.docker` met Docker socket discovery
- Label extraction: `loki.process` stage met JSON-parsing voor `level`
- Output: `loki.write` naar `http://victorialogs:9428/insert/jsonline`
- Service label: uit Docker-label `com.docker.compose.service`
- Configuratiebestand: `deploy/alloy/config.alloy`

### Traceability

| Requirement     | Plan referentie | Acceptance test |
|-----------------|-----------------|-----------------|
| REQ-COLLECT-001 | Phase 1         | AC-MOD2-001     |
| REQ-COLLECT-002 | Phase 1         | AC-MOD2-001     |
| REQ-COLLECT-003 | Phase 1         | AC-MOD2-002     |
| REQ-COLLECT-004 | Phase 1         | AC-MOD2-003     |

---

## Module 3: VictoriaLogs Storage

### Omgeving

- VictoriaLogs draait op core-01 op port 9428, intern in het `monitoring`-netwerk
- Geen Grafana datasource-provisioning geconfigureerd in de repository

### Aannames

- VictoriaLogs ondersteunt Loki-compatible push API (`/insert/jsonline`)
- VictoriaLogs ondersteunt retentieconfiguratie via `-retentionPeriod` flag
- Grafana ondersteunt VictoriaLogs als datasource (via plugin of Loki-compatibel endpoint)

### Requirements

**REQ-STORE-001** (Event-Driven):
WHEN Alloy logs shipt, THEN SHALL VictoriaLogs deze opslaan met een retentieperiode van 30 dagen.

**REQ-STORE-002** (Ubiquitous):
Het systeem SHALL een Grafana datasource provisioning-bestand hebben op `deploy/grafana/provisioning/datasources/victorialogs.yaml` dat gecommit is in de repository.

### Specificaties

- VictoriaLogs retentie: `-retentionPeriod=30d`
- Datasource type: `victoriametrics-logs-datasource` (Grafana plugin) of `loki` (compatibiliteitsmodus)
- Provisioning pad: `deploy/grafana/provisioning/datasources/victorialogs.yaml`
- URL in datasource: `http://victorialogs:9428`

### Traceability

| Requirement    | Plan referentie | Acceptance test |
|----------------|-----------------|-----------------|
| REQ-STORE-001  | Phase 1         | AC-MOD3-001     |
| REQ-STORE-002  | Phase 1         | AC-MOD3-002     |

---

## Module 4: Grafana Log Explorer

### Omgeving

- Grafana draait op core-01, verbonden met het `monitoring`-netwerk
- Geen provisioned dashboards aanwezig in de repository

### Aannames

- Grafana ondersteunt provisioned dashboards via YAML-configuratie
- Ontwikkelaars hebben toegang tot Grafana via de bestaande Grafana-URL

### Requirements

**REQ-DASH-001** (Event-Driven):
WHEN een ontwikkelaar Grafana opent, THEN SHALL deze logs kunnen querien op `service`, `server`, en `level`.

**REQ-DASH-002** (Ubiquitous):
Het systeem SHALL een provisioned dashboard hebben op `deploy/grafana/provisioning/dashboards/logs.json` dat gecommit is in de repository.

**REQ-DASH-003** (Ubiquitous):
Het systeem SHALL een dashboard provider-configuratie hebben op `deploy/grafana/provisioning/dashboards/dashboards.yaml`.

### Specificaties

- Dashboard: Log Explorer met filters voor `service`, `server`, `level`
- Visualisaties: log-tabel met tijdstempel, service, level, event; histogram van logs per service
- Provider config: `deploy/grafana/provisioning/dashboards/dashboards.yaml` met pad naar dashboard-directory
- Dashboard JSON: `deploy/grafana/provisioning/dashboards/logs.json`

### Traceability

| Requirement   | Plan referentie | Acceptance test |
|---------------|-----------------|-----------------|
| REQ-DASH-001  | Phase 1         | AC-MOD4-001     |
| REQ-DASH-002  | Phase 1         | AC-MOD4-002     |
| REQ-DASH-003  | Phase 1         | AC-MOD4-002     |

---

## Module 5: public-01 Log Shipping

### Omgeving

- public-01 draait Coolify met diverse containers (o.a. Uptime Kuma, website)
- Geen Alloy-agent aanwezig op public-01
- VictoriaLogs insert endpoint is niet bereikbaar vanuit public-01 (intern netwerk)
- Caddy draait als reverse proxy op public-01

### Aannames

- Caddy op public-01 kan routes toevoegen voor interne services
- Een bearer token is voldoende voor authenticatie van log-ingest
- public-01 heeft netwerkconnectiviteit naar core-01 via het publieke internet (HTTPS)

### Requirements

**REQ-SHIP-001** (Event-Driven):
WHEN een container op public-01 naar stdout schrijft, THEN SHALL Alloy op public-01 de log collecten en shippen naar VictoriaLogs op core-01.

**REQ-SHIP-002** (Ubiquitous):
Het systeem SHALL een Caddy-geproxied VictoriaLogs insert endpoint exposen op `https://logs-ingest.${DOMAIN}`, beveiligd met bearer token-authenticatie.

**REQ-SHIP-003** (Ubiquitous):
Het systeem SHALL het label `server=public-01` toevoegen aan alle logs afkomstig van public-01.

**REQ-SHIP-004** (Unwanted):
IF een push-request naar `logs-ingest.${DOMAIN}` geen geldig bearer token bevat, THEN SHALL Caddy een 401 Unauthorized-response retourneren. Ongeauthenticeerde toegang tot het ingest-endpoint is verboden.

### Specificaties

- Caddy route: `logs-ingest.{$DOMAIN}` met `reverse_proxy victorialogs:9428`
- Authenticatie: `header_up Authorization "Bearer {env.VICTORIALOGS_INGEST_TOKEN}"` validatie in Caddy
- Alloy op public-01: `loki.source.docker` met output naar `https://logs-ingest.${DOMAIN}/insert/jsonline`
- Alloy authenticatie: bearer token header in `loki.write` configuratie
- Extra labels: `server=public-01`, `env=production`
- Environment variable: `VICTORIALOGS_INGEST_TOKEN` in `.env` template

### Traceability

| Requirement   | Plan referentie | Acceptance test |
|---------------|-----------------|-----------------|
| REQ-SHIP-001  | Phase 3         | AC-MOD5-001     |
| REQ-SHIP-002  | Phase 3         | AC-MOD5-002     |
| REQ-SHIP-003  | Phase 3         | AC-MOD5-001     |
| REQ-SHIP-004  | Phase 3         | AC-MOD5-003     |

---

## Services Inventory

| Docker service       | Pad in klai-mono               | Module |
|----------------------|--------------------------------|--------|
| `portal-api`         | `portal/backend/`              | 1, 2   |
| `klai-connector`     | `deploy/klai-connector/`       | 1, 2   |
| `klai-mailer`        | `deploy/klai-mailer/`          | 1, 2   |
| `klai-knowledge-mcp` | `deploy/klai-knowledge-mcp/`   | 1, 2   |
| `scribe-api`         | `scribe/scribe-api/`           | 1, 2   |
| `whisper-server`     | `scribe/whisper-server/`       | 1, 2   |
| `research-api`       | `focus/research-api/`          | 1, 2   |
| `retrieval-api`      | `retrieval-api/`               | 1, 2   |

---

## Afhankelijkheden

- VictoriaLogs moet draaien en bereikbaar zijn vanuit het `monitoring`-netwerk (bestaand)
- Grafana moet draaien en provisioning-volumes gemount hebben (bestaand)
- Alloy moet draaien met Docker socket-toegang (bestaand, maar configuratie ontbreekt)
- Caddy op core-01 moet de `logs-ingest`-subdomain kunnen routen (nieuw)
- DNS voor `logs-ingest.${DOMAIN}` moet verwijzen naar core-01 (nieuw)
