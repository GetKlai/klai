# Research: AI-Optimized Observability voor Klai

## Huidige Staat

### Wat werkt
- **Structlog JSON pipeline**: Alle 9 Python services → stdout → Alloy → VictoriaLogs (30d retentie)
- **Alloy collector**: Docker socket discovery, label extraction (`container`, `service`, `host`, `level`)
- **Grafana dashboards**: Logs browser, container metrics, node metrics, web vitals
- **Portal-api context**: `LoggingContextMiddleware` bindt `request_id`, `org_id`, `user_id` per request
- **Metrics**: Web Vitals (portal-api), pipeline latency (retrieval-api), container/node metrics (cAdvisor)

### Architectuur: Log Pipeline
```
Python services (structlog JSON) ─┐
Third-party services (plain text) ─┤→ Docker stdout → Alloy (Docker socket) → VictoriaLogs
Caddy (file logging) ──────────────┘     ↓ metrics
                                    VictoriaMetrics ← cAdvisor, node-exporter
                                         ↓
                                      Grafana
```

### Configuratie Locaties
- Alloy: `deploy/alloy/config.alloy` (core-01), `config-public-01.alloy`
- VictoriaLogs: `deploy/docker-compose.yml` (30d retentie, 2 CPU, 2GB RAM)
- Grafana datasources: `deploy/grafana/provisioning/datasources/datasources.yaml`
- Dashboards: `deploy/grafana/provisioning/dashboards/` (logs, container-metrics, node-metrics, web-performance, klai-health)
- Caddyfile: `deploy/caddy/Caddyfile` (file logging, 10MB rotation x5)

---

## Gaps: Waarom AI Agents Moeilijk Debuggen

### 1. Geen Cross-Service Correlatie (KRITIEK)
- Portal-api genereert `request_id` (UUID) maar stuurt dit NIET mee in httpx calls
- Knowledge-ingest, retrieval-api etc. genereren hun eigen UUID
- **Impact**: Een request door 3 services = 3 ongerelateerde log entries
- **AI debug flow**: Agent moet op timestamp matchen — onbetrouwbaar bij concurrent requests

**Bewijs**: `klai-portal/backend/app/services/knowledge_ingest_client.py` stuurt alleen `X-Internal-Secret`, geen trace headers.

### 2. Context Middleware Alleen in Portal-API
- 8 van 9 Python services binden alleen `service_name`, geen `request_id` of `org_id`
- **Impact**: Logs van downstream services missen tenant context

### 3. GlitchTip Ongebruikt
- Container draait op `errors.${DOMAIN}` maar geen enkele service heeft `sentry-sdk`
- **Impact**: Exceptions zijn losse JSON regels in VictoriaLogs, niet gegroepeerd of gededupliceerd

### 4. Caddy Logs Geïsoleerd
- Access logs naar `/var/log/caddy/access.log` (plain text CLF)
- Alloy scrapt NIET uit Caddy logbestanden, alleen Docker stdout
- **Impact**: HTTP-level foutcodes niet queryable in Grafana

### 5. Incomplete Metrics
- `klai-health.json` dashboard verwijst naar `http_requests_total` en `http_request_duration_seconds_bucket` — deze metrics BESTAAN NIET
- Alleen web vitals (portal-api) en pipeline latency (retrieval-api) beschikbaar
- **Impact**: Geen error rate, latency p99, of request count per endpoint

### 6. Geen Log-Based Alerting
- VictoriaLogs heeft geen alert rules
- Grafana heeft geen log-notificaties
- **Impact**: Errors worden pas ontdekt als een gebruiker klaagt

### 7. Docker Log Rotation Niet Geconfigureerd
- `json-file` driver zonder size limits → logs groeien onbeperkt
- **Risico**: Disk vol op core-01

---

## Industry Research: AI-Optimized Logging

### Hoe AI Agents Logs Gebruiken
1. **`docker logs <service> | grep ERROR`** — snelle triage
2. **Read tool op logbestanden** — diepere analyse
3. **Structured API queries** — historisch onderzoek (VictoriaLogs LogsQL)
4. **MCP tools** — Grafana MCP server (`mcp-grafana`) geeft directe dashboard/query toegang

### Optimaal Log Formaat voor LLMs
- **JSON-per-line (JSONL)** is non-negotiable — LLMs extracten fields betrouwbaar uit JSON
- **Essentiële velden**: `timestamp`, `level`, `service`, `trace_id`, `tenant_id`, `message`, `error`
- **Context velden**: `duration_ms`, `http.method`, `http.path`, `http.status`, `db.statement` (truncated)
- **Principe**: Log de *beslissing* (welke handler, welke branch), niet alleen het *resultaat*

### Trace Correlatie is de Killer Feature
- Eén `trace_id` + `grep trace_id=<value>` = volledige request flow
- W3C `traceparent` header is de standaard
- Kan starten met simpele `X-Request-ID` propagatie voordat OTel nodig is

### VictoriaLogs als AI Interface
- LogsQL API: `GET /select/logsql/query?query=<LogsQL>&time=<timestamp>`
- Retourneert JSONL stream — direct parseable door AI
- Grafana MCP server (`mcp-grafana`) geeft AI agents directe query access

### Microsoft AgentRx Research
- Structured constraint checking op traces verbetert failure localization +23.6%
- **Implicatie**: Logs moeten genoeg context bevatten voor invariant validatie zonder broncode

### Self-Hosted Stack Consensus (2025-2026)
| Component | Aanbeveling | Klai Status |
|-----------|-------------|-------------|
| Collection | OTel Collector of Alloy | Alloy (goed) |
| Log Storage | VictoriaLogs | VictoriaLogs (goed) |
| Metrics | VictoriaMetrics | VictoriaMetrics (goed) |
| Traces | VictoriaTraces / Tempo | Niet aanwezig |
| Visualization | Grafana | Grafana (goed) |
| AI Access | Grafana MCP | Niet geconfigureerd |

---

## Bronnen
- VictoriaLogs vs Loki Benchmarks (truefoundry.com)
- Microsoft AgentRx Framework (microsoft.com/research)
- Grafana MCP Server (github.com/grafana/mcp-grafana)
- VictoriaLogs OpenTelemetry Setup (docs.victoriametrics.com)
- Structured Logging for AI Systems (dasroot.net)
- Adding Logs to AI Agents (mbrenndoerfer.com)
- VictoriaLogs LogsQL Docs (docs.victoriametrics.com)
