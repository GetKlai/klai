---
id: SPEC-DEVOPS-001
type: acceptance
version: 1.0.0
status: completed
created: 2026-03-26
updated: 2026-03-26
---

# SPEC-DEVOPS-001: Acceptance Criteria

## Module 1: Structured Logging -- Python Services

### AC-MOD1-001: JSON-output in productie

```gherkin
Given portal-api draait met standaard configuratie (geen LOG_FORMAT gezet)
When portal-api een error logt
Then bevat `docker logs portal-api | tail -1 | python -m json.tool` valid JSON
And bevat het JSON-object de velden: timestamp, level, service, logger, event
And is het timestamp-veld in ISO 8601-formaat
And is het service-veld gelijk aan "portal-api"
```

### AC-MOD1-002: Console-output voor lokale ontwikkeling

```gherkin
Given LOG_FORMAT=console is gezet als environment variable
When portal-api een logmelding schrijft
Then is de output human-readable tekst (geen JSON)
And bevat de output het logniveau en het bericht
```

### AC-MOD1-003: Alle 8 services produceren JSON

```gherkin
Given alle 8 Python-services draaien met standaard configuratie
When elke service ten minste 1 logmelding schrijft (bijv. startup-log)
Then produceert elke service valid JSON op stdout
And bevatten alle JSON-objecten de verplichte velden: timestamp, level, service, logger, event
```

### AC-MOD1-005: Request context automatisch in logs (portal-api)

```gherkin
Given portal-api draait met de logging context middleware
And een ingelogde gebruiker van org "acme" doet een API-call
When de request een fout veroorzaakt die gelogd wordt
Then bevat de log entry het veld org_id="acme"
And bevat de log entry een request_id veld
And zijn org_id en request_id aanwezig zonder dat de logging-aanroep ze expliciet meegeeft
```

### AC-MOD1-004: Geen logging.basicConfig() meer

```gherkin
Given de volledige codebase
When een grep wordt uitgevoerd op "logging.basicConfig"
Then zijn er 0 resultaten in productie-code (tests uitgezonderd)
```

---

## Module 2: Alloy Log Collection -- core-01

### AC-MOD2-001: Log collection en labeling

```gherkin
Given Alloy draait op core-01 met de nieuwe config uit deploy/alloy/config.alloy
And portal-api draait en produceert JSON-logs
When portal-api een error logt
Then is de log binnen 10 seconden terug te vinden in VictoriaLogs
And bevat de log entry het label service="portal-api"
And bevat de log entry het label server="core-01"
And bevat de log entry het label env="production"
And bevat de log entry het label level="error"
```

### AC-MOD2-002: Automatische discovery van nieuwe containers

```gherkin
Given Alloy draait op core-01 met Docker socket discovery
When een nieuwe Docker-container start (bijv. een LibreChat tenant)
And de container schrijft naar stdout
Then collecteert Alloy automatisch de logs zonder configuratiewijziging
And wordt het correcte service-label toegepast op basis van com.docker.compose.service
```

### AC-MOD2-003: Configuratie in repository

```gherkin
Given de klai-mono repository
When het bestand deploy/alloy/config.alloy wordt gelezen
Then bevat het een loki.source.docker component
And bevat het een loki.process stage voor label-extractie
And bevat het een loki.write output naar VictoriaLogs
```

---

## Module 3: VictoriaLogs Storage

### AC-MOD3-001: Retentie van 30 dagen

```gherkin
Given een log is 29 dagen geleden ge-ingest in VictoriaLogs
When VictoriaLogs wordt gequeried voor deze log
Then is de log nog steeds beschikbaar en opvraagbaar
```

### AC-MOD3-002: Grafana datasource provisioning

```gherkin
Given Grafana start vers (zonder persisted state, clean provisioning)
When Grafana volledig is opgestart
Then is de VictoriaLogs datasource beschikbaar zonder handmatige configuratie
And kan de datasource succesvol een test-query uitvoeren
```

---

## Module 4: Grafana Log Explorer

### AC-MOD4-001: Filteren op service, server en level

```gherkin
Given het Log Explorer dashboard is geladen in Grafana
And er zijn logs aanwezig van meerdere services en servers
When een ontwikkelaar filtert op service="portal-api" en level="error"
Then worden alleen error-logs van portal-api getoond
And zijn logs van andere services niet zichtbaar in de resultaten
```

### AC-MOD4-002: Dashboard provisioning

```gherkin
Given Grafana start met de provisioned configuratie uit de repository
When Grafana volledig is opgestart
Then is het Log Explorer dashboard beschikbaar zonder handmatige import
And bevat het dashboard filters voor service, server en level
And bevat het dashboard een log-tabel en een histogram panel
```

---

## Module 5: public-01 Log Shipping

### AC-MOD5-001: Logs van public-01 in Grafana

```gherkin
Given Alloy draait op public-01 met configuratie voor log shipping
And Uptime Kuma draait op public-01
When Uptime Kuma een event logt
Then verschijnt de log binnen 30 seconden in Grafana
And bevat de log entry het label server="public-01"
And bevat de log entry het label env="production"
```

### AC-MOD5-002: Caddy ingest endpoint

```gherkin
Given de Caddy-configuratie op core-01 bevat de logs-ingest route
And VICTORIALOGS_INGEST_TOKEN is geconfigureerd
When een POST-request met geldig bearer token wordt gestuurd naar https://logs-ingest.${DOMAIN}/insert/jsonline
Then accepteert het endpoint de request (HTTP 2xx)
And worden de logs opgeslagen in VictoriaLogs
```

### AC-MOD5-003: Authenticatie op ingest endpoint

```gherkin
Given het logs-ingest endpoint is actief op core-01
When een POST-request zonder bearer token wordt gestuurd naar https://logs-ingest.${DOMAIN}/insert/jsonline
Then retourneert Caddy HTTP 401 Unauthorized
And worden er geen logs opgeslagen in VictoriaLogs
```

### AC-MOD5-004: Ongeldig token geweigerd

```gherkin
Given het logs-ingest endpoint is actief op core-01
When een POST-request met een ongeldig bearer token wordt gestuurd
Then retourneert Caddy HTTP 401 Unauthorized
```

---

## Quality Gates

### QG-001: structlog in alle services

```gherkin
Given de volledige codebase van alle 8 Python-services
When de dependencies worden ge-inspecteerd
Then bevat elke service "structlog" in pyproject.toml
And heeft elke service een logging_setup module
And roept elke service setup_logging() aan voor app-creatie
```

### QG-002: Ruff logging rules

```gherkin
Given ruff is geconfigureerd met G en LOG rules in alle pyproject.toml-bestanden
When `ruff check .` wordt uitgevoerd vanuit de repository root
Then zijn er 0 violations voor de G- en LOG-regelsets
```

### QG-003: Geen log = variabelen

```gherkin
Given de volledige Python-codebase
When een grep wordt uitgevoerd op het patroon "^(\s*)log\s*=\s*logging\.getLogger"
Then zijn er 0 resultaten (alleen logger = is toegestaan)
```

### QG-004: End-to-end verificatie

```gherkin
Given alle 5 modules zijn ge-implementeerd en gedeployed
When portal-api op core-01 een error logt
And Uptime Kuma op public-01 een event logt
Then zijn beide logs binnen 60 seconden zichtbaar in het Grafana Log Explorer dashboard
And zijn de logs correct gelabeld met hun respectieve service en server
And kan gefilterd worden op service, server en level
```

---

## Definition of Done

- [ ] Alle 8 Python-services produceren JSON-logs naar stdout (REQ-LOG-001)
- [ ] `LOG_FORMAT=console` werkt voor lokale ontwikkeling (REQ-LOG-002)
- [ ] Geen `logging.basicConfig()` in productie-code (REQ-LOG-003)
- [ ] `deploy/alloy/config.alloy` gecommit en werkend (REQ-COLLECT-004)
- [ ] Alloy collecteert logs binnen 5 seconden (REQ-COLLECT-001)
- [ ] Labels `service`, `server`, `env` aanwezig op alle logs (REQ-COLLECT-002)
- [ ] JSON `level`-extractie werkt (REQ-COLLECT-003)
- [ ] VictoriaLogs datasource provisioned in Grafana (REQ-STORE-002)
- [ ] 30-dagen retentie geconfigureerd (REQ-STORE-001)
- [ ] Log Explorer dashboard provisioned met filters (REQ-DASH-001, REQ-DASH-002)
- [ ] public-01 logs bereiken core-01 VictoriaLogs (REQ-SHIP-001)
- [ ] Bearer token-authenticatie op ingest endpoint (REQ-SHIP-004)
- [ ] Ruff `G` en `LOG` rules actief en passing (Phase 4)
- [ ] Geen `log =` variabelen meer in codebase (REQ-LOG-004)
- [ ] Logging context middleware in portal-api: org_id, user_id, request_id automatisch in logs (REQ-LOG-005)
- [ ] End-to-end test: log op core-01 en public-01 zichtbaar in Grafana (QG-004)
