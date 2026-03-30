# Research: SPEC-DEVOPS-001 — Centralized Structured Logging

## Current State Assessment

### Monitoring Infrastructure (core-01)

All in `deploy/docker-compose.yml`, section starting at line 437:

| Component | Docker service | Port | Network | Status |
|-----------|---------------|------|---------|--------|
| VictoriaLogs | `victorialogs` | 9428 (internal) | `monitoring` only | Running |
| Grafana Alloy | `alloy` | 12345 (localhost) | `klai-net` + `monitoring` | Running, **config.alloy missing from repo** |
| Grafana | `grafana` | internal | `monitoring` | Running, **provisioning missing from repo** |
| VictoriaMetrics | `victoriametrics` | 8428 (internal) | `monitoring` only | Running |

Critical gaps:
- `deploy/alloy/config.alloy` is volume-mounted but does NOT exist in the repo — only on the server (or nowhere)
- `deploy/grafana/provisioning/` is volume-mounted but does NOT exist in the repo
- `monitoring` network is `internal: true` — VictoriaLogs is NOT reachable from outside core-01

VictoriaLogs Loki-compatible insert endpoint (from Alloy on core-01):
`http://victorialogs:9428/insert/loki/api/v1/push`

### Python Services Inventory

All 8 Python services are in `deploy/docker-compose.yml` (single compose file):

| Service | Docker service name | Path | Current logging setup | log var |
|---------|---------------------|------|-----------------------|---------|
| Portal API | `portal-api` | `klai-portal/backend/` | No `basicConfig` — relies on uvicorn default | `logger` |
| Klai Connector | `klai-connector` | `deploy/klai-connector/` | No `basicConfig` seen in main.py | unknown |
| Klai Mailer | `klai-mailer` | `deploy/klai-mailer/` | `logging.basicConfig(...)` in main.py | unknown |
| Knowledge MCP | `klai-knowledge-mcp` | `deploy/klai-knowledge-mcp/` | No `basicConfig` — only `getLogger` | `logger` |
| Scribe API | `scribe-api` | `klai-scribe/scribe-api/` | `logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))` | unknown |
| Whisper Server | `whisper-server` | `klai-scribe/whisper-server/` | `logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))` | `logger` |
| Research API | `research-api` | `klai-focus/research-api/` | `logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))` | unknown |
| Retrieval API | `retrieval-api` | `klai-retrieval-api/` | `logging.basicConfig(level=logging.INFO, ...)` | `logger` |

No service uses structlog or python-json-logger. All output is plain text.

### Non-Python Services (already in docker-compose.yml)

Most emit plain text or their own JSON format. Alloy can collect them all via Docker socket without code changes:

| Service | Log format | Notes |
|---------|-----------|-------|
| Caddy | JSON (structured) | `--log.format=json` or default JSON |
| Zitadel | JSON | Go service, structured by default |
| LiteLLM | Plain text | Python, mixed format |
| LibreChat | Plain text / mixed | Node.js |
| Redis | Plain text | `--loglevel warning` set |
| Qdrant | JSON | Rust service |
| Gitea | Plain/JSON | `GITEA__log__LEVEL: Warn` set |

### public-01 Services

Runs via Coolify (no docker-compose in this repo):
- Website (Astro)
- Twenty CRM
- Fider
- Uptime Kuma

**Key constraint:** `monitoring` network on core-01 is `internal: true`. Public-01's Alloy cannot reach VictoriaLogs directly. Two options:
1. **Expose via Caddy** — add a Caddy route for `logs.${DOMAIN}` that proxies to `victorialogs:9428/insert/` with bearer token auth. Alloy on public-01 pushes to this HTTPS endpoint.
2. **WireGuard** — private tunnel between servers (no such tunnel documented in deployment context).

**Recommended: Caddy reverse proxy** — simpler, consistent with existing Caddy usage for all other services.

## Gaps Identified

1. `deploy/alloy/config.alloy` — missing from repo, likely doesn't exist on server either (Alloy may be failing to start or using an empty config)
2. `deploy/grafana/provisioning/` — missing from repo; Grafana datasource and dashboards are not version-controlled
3. No structured (JSON) logging in any Python service
4. `log` vs `logger` variable naming inconsistency across services
5. No `G` or `LOG` ruff rules enforcing logging format consistency
6. No mechanism to ship public-01 logs to VictoriaLogs

## Patterns & Conventions

- Most services use `logging.getLogger(__name__)` — correct pattern
- Some use `log =`, some use `logger =` — inconsistent
- LOG_LEVEL env var already used in 3 services (scribe-api, whisper-server, research-api) — good pattern to standardize
- portal-api does not use `basicConfig` — uvicorn controls the root logger by default

## Risks & Constraints

1. **Alloy config may not exist on server** — need to check before deploying new config (risk of overwriting nothing vs. something)
2. **public-01 connectivity** — requires exposing VictoriaLogs insert endpoint via Caddy; needs auth token to prevent open log injection
3. **structlog migration** — adding structlog changes log format immediately; any log parsing scripts or manual grep patterns will break
4. **uvicorn access logs** — portal-api uses uvicorn which controls its own access log format; structlog setup must integrate with uvicorn's logging config
5. **LibreChat tenant containers** — dynamically created containers, not in docker-compose; Alloy's Docker discovery will pick them up automatically via socket
6. **Log volume** — retrieval-api and research-api may be high-volume; VictoriaLogs retention is 30d with 2G memory limit

## Recommended Approach

### Python structured logging: `structlog`
- `structlog` with `stdlib` integration (not a full replacement) — keeps `logging.getLogger(__name__)` pattern working in all modules
- JSON renderer for production, ConsoleRenderer for local dev (detected via `LOG_FORMAT=json|console` env var)
- Shared setup function: create one `logging_setup.py` per service (not a shared library — each service is independent)
- Variable naming standard: `logger = logging.getLogger(__name__)` (standardize on `logger`, rename `log` instances)

### Alloy config strategy
- Use `loki.source.docker` component — reads from Docker socket, discovers all containers
- Label extraction: `service` from `com.docker.compose.service`, `server` hardcoded to `core-01`
- JSON log detection: try-parse JSON, fall back to plain text
- Forward to VictoriaLogs Loki API

### Grafana provisioning
- VictoriaLogs datasource via `victoriametrics-logs-datasource` plugin (already installed)
- One basic dashboard: log explorer with service/level filters
- Committed to `deploy/grafana/provisioning/`

### public-01 Alloy
- Caddy route for `logs-ingest.${DOMAIN}` → VictoriaLogs insert API
- Bearer token auth (new env var `VICTORIALOGS_INGEST_TOKEN`)
- Alloy agent deployed on public-01 as a Coolify service

## Reference Implementations

- VictoriaLogs Loki API: `POST /insert/loki/api/v1/push` with JSON body
- Alloy Docker source: `loki.source.docker` + `discovery.docker` components
- Grafana VictoriaLogs datasource type: `victoriametrics-logs-datasource`
- structlog FastAPI integration pattern: configure in `lifespan` or module-level before app creation
