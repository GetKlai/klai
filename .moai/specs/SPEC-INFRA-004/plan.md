# SPEC-INFRA-004: AI-Optimized Observability

## Doel

Klai's logging en observability verbeteren zodat AI agents (Claude Code) snel en betrouwbaar cross-service issues kunnen debuggen. Focus op: trace correlatie, gestructureerde context, en directe AI-toegang tot logs.

## Scope

9 Python microservices + infra services (Caddy, Zitadel, PostgreSQL, etc.) op Docker Compose (core-01).

---

## Modules (in volgorde van prioriteit)

### Module 1: Cross-Service Trace Correlatie (KRITIEK)

**Probleem**: Elke service genereert een eigen `request_id`. Er is geen manier om een request door portal-api → knowledge-ingest → retrieval-api te volgen.

**Oplossing**:
1. Portal-api: stuur `X-Request-ID` header mee in alle httpx calls naar downstream services
2. Downstream services: lees `X-Request-ID` uit incoming request headers (of genereer nieuwe als afwezig)
3. Bind `request_id` in structlog contextvars bij elke service

**Bestanden**:
- `klai-portal/backend/app/middleware/logging_context.py` — stuur request_id mee als response header
- `klai-portal/backend/app/services/knowledge_ingest_client.py` — voeg X-Request-ID toe
- `klai-portal/backend/app/services/docs_client.py` — idem
- `klai-portal/backend/app/services/klai_connector_client.py` — idem
- `klai-portal/backend/app/services/vexa.py` — idem
- Alle downstream services: middleware die `X-Request-ID` leest en bindt

**Verificatie**: Eén `grep request_id=<uuid>` in VictoriaLogs toont logs van alle services in de chain.

### Module 2: Context Middleware voor Alle Services

**Probleem**: Alleen portal-api bindt `request_id`, `org_id`, `user_id`. Andere services loggen alleen `service_name`.

**Oplossing**:
1. Maak een generiek `RequestContextMiddleware` dat:
   - `X-Request-ID` leest (of genereert)
   - `X-Org-ID` leest (indien beschikbaar)
   - Beide bindt in structlog contextvars
2. Voeg middleware toe aan alle FastAPI services

**Bestanden**: Elke service's `main.py` + nieuwe middleware file per service (of shared pattern)

**Verificatie**: Logs van knowledge-ingest bevatten `request_id` en `org_id` bij inter-service calls.

### Module 3: Shared Logging Package

**Probleem**: 9 kopieën van `logging_setup.py` die uit sync kunnen raken. klai-connector wijkt al af.

**Oplossing**:
1. Maak `klai-common/` package in monorepo root met:
   - `klai_common/logging.py` — `setup_logging(service_name)` + `RequestContextMiddleware`
   - `klai_common/py.typed` marker
   - `pyproject.toml` met `uv` workspace support
2. Elke service: vervang lokale `logging_setup.py` door `from klai_common.logging import setup_logging`
3. Gebruik `uv` workspace path dependencies (`klai-common = { path = "../../klai-common" }`)

**Verificatie**: `ruff check` + `pyright` passeren; logging output identiek aan huidige JSON format.

### Module 4: Caddy Structured Logging → VictoriaLogs

**Probleem**: Caddy access logs gaan naar een file (plain text CLF), niet naar VictoriaLogs.

**Oplossing**:
1. Caddy Caddyfile: switch naar JSON format + output naar stdout (naast file)
2. Alloy pikt Caddy container stdout automatisch op via Docker discovery
3. Optioneel: Caddy injecteert `X-Request-ID` header naar backend (genereert UUID als niet aanwezig)

**Bestanden**: `deploy/caddy/Caddyfile`

**Verificatie**: Caddy access logs queryable in Grafana met `service="caddy"`.

### Module 5: Grafana MCP Server voor AI Agent Access

**Probleem**: AI agents moeten nu `docker logs` + grep gebruiken. Geen directe query interface.

**Oplossing**:
1. Deploy `mcp-grafana` als MCP server in Claude Code config
2. Configureer met Grafana API key (read-only)
3. AI agent kan dan: dashboards querien, LogsQL uitvoeren, alerts checken

**Bestanden**: `.mcp.json`, deploy docs

**Verificatie**: Claude Code kan via MCP tool VictoriaLogs queries uitvoeren en resultaten krijgen.

### Module 6: FastAPI Request Metrics

**Probleem**: `klai-health.json` dashboard verwijst naar metrics die niet bestaan (`http_requests_total`, `http_request_duration_seconds_bucket`).

**Oplossing**:
1. Voeg `prometheus-fastapi-instrumentator` toe aan alle FastAPI services
2. Exposeert automatisch: request count, latency histogram, status code distributie
3. Alloy scrape config uitbreiden voor nieuwe `/metrics` endpoints
4. Fix `klai-health.json` dashboard panels

**Bestanden**: Elke service's `main.py` + `pyproject.toml`, `deploy/alloy/config.alloy`, Grafana dashboards

**Verificatie**: Grafana toont real-time request rates en error percentages per service.

### Module 7: GlitchTip/Sentry SDK Integratie

**Probleem**: GlitchTip draait maar geen enkele service stuurt exceptions.

**Oplossing**:
1. Voeg `sentry-sdk[fastapi]` toe aan alle Python services
2. Configureer DSN naar GlitchTip (`errors.${DOMAIN}`)
3. Automatische exception capture met request context, breadcrumbs
4. Bind `request_id`, `org_id`, `user_id` als Sentry tags

**Bestanden**: Elke service's `main.py` + `pyproject.toml`, env vars in docker-compose

**Verificatie**: Exception in portal-api verschijnt in GlitchTip met volledige context en stack trace.

### Module 8: Docker Log Rotation + Alerting (Housekeeping)

**Probleem**: Docker `json-file` driver heeft geen size limits. Geen log-based alerts.

**Oplossing**:
1. Docker daemon config: `max-size: 50m`, `max-file: 3` voor alle containers
2. Grafana alerting: ERROR count > 10 in 5 min per service → notification
3. Disk usage alert: VictoriaLogs storage > 80%

**Bestanden**: `deploy/docker-compose.yml` (logging driver config), Grafana alert rules

**Verificatie**: `docker inspect` toont log rotation config; test alert fires bij 10+ errors.

---

## Fasering

### Fase 1: Quick Wins (1-2 dagen)
- Module 1: Cross-service trace correlatie (X-Request-ID propagatie)
- Module 4: Caddy structured logging
- Module 8: Docker log rotation

### Fase 2: Foundation (2-3 dagen)
- Module 2: Context middleware alle services
- Module 3: Shared logging package (klai-common)
- Module 6: FastAPI request metrics

### Fase 3: AI Integration (1-2 dagen)
- Module 5: Grafana MCP server
- Module 7: GlitchTip/Sentry SDK

### Toekomst (buiten scope)
- OpenTelemetry auto-instrumentation (traces + spans)
- W3C `traceparent` propagatie (vervangt X-Request-ID)
- VictoriaTraces deployment
- Per-level log retention (ERROR 90d, INFO 30d, DEBUG 7d)

---

## Risico's

| Risico | Impact | Mitigatie |
|--------|--------|-----------|
| Shared package breekt builds | Alle services down | Incrementeel migreren, één service per keer |
| Sentry SDK performance overhead | Latency toename | Sample rate configureren (0.1 in productie) |
| Docker log rotation verliest logs | Debug data kwijt | Alloy vangt logs real-time op; rotation raakt alleen lokale Docker cache |
| Caddy JSON logging performance | Proxy latency | Benchmark voor/na; Caddy JSON logging is geoptimaliseerd |

---

## Afhankelijkheden

- `structlog` (al aanwezig in alle services)
- `prometheus-fastapi-instrumentator` (nieuw, PyPI)
- `sentry-sdk[fastapi]` (nieuw, PyPI)
- `mcp-grafana` (NPM/Go binary, voor Claude Code)
- Geen database migraties nodig
- Geen breaking API changes

---

## Success Criteria voor AI Debugging

Na implementatie moet een AI agent:
1. **In < 30 seconden** een cross-service request tracen via `request_id` grep
2. **Zonder raden** de volledige request chain zien: Caddy → portal-api → downstream
3. **Via MCP** direct VictoriaLogs queries uitvoeren vanuit de conversatie
4. **Error context** krijgen met stack trace, tenant info, en request details via GlitchTip
5. **Metrics** checken (error rate, latency p99) zonder log parsing
