---
paths: "**/*.py,**/*.ts,**/*.tsx"
---
# Logging Patterns

> Structured logging with structlog, querying VictoriaLogs via LogsQL, and debugging production issues.

## Index
> Keep this index in sync — add a row when adding a section below.

| Section | When to use |
|---|---|
| [Architecture](#architecture) | Understanding the full log pipeline |
| [Log fields](#log-fields) | Required structlog fields for Python services |
| [Writing logs](#writing-logs) | How to write logs with structlog |
| [Adding logging to a new service](#adding-logging-to-a-new-service) | Setting up logging in a new Python service |
| [Querying logs (LogsQL)](#querying-logs-logsql) | Querying VictoriaLogs via Grafana |
| [Accessing logs as an agent](#accessing-logs-as-an-agent) | How Claude agents access production logs |

---

## Architecture

```
Python service (structlog JSON)
    → Docker stdout
        → Alloy (loki.source.docker)
            → loki.process (extract level label)
                → loki.write → VictoriaLogs (https://logs-ingest.{DOMAIN})
                                    → Grafana (LogsQL queries)
```

Two Alloy instances collect logs:

- **core-01**: `klai-core-alloy-1` — all core-01 containers, tags `host=core-01`
- **public-01**: separate Alloy — klai-scribe/whisper containers, tags `host=public-01`

## Log fields

Every Klai service log line is JSON with these fields:

| Field | Source | Description |
|---|---|---|
| `event` | service | The log message |
| `level` | service | `info`, `warning`, `error`, `critical` |
| `logger` | service | Python module (e.g. `app.services.sync_engine`) |
| `timestamp` | service | ISO 8601 |
| `service` | service | Docker service name (e.g. `portal-api`, `klai-connector`) |
| `host` | Alloy | Server: `core-01` or `public-01` |
| `container` | Alloy | Full Docker container name |
| `request_id` | middleware | Per-request UUID (portal-api only) |
| `org_id` | middleware | Zitadel org ID (portal-api only) |
| `user_id` | middleware | Zitadel user ID (portal-api only) |
| extra kwargs | service | Any fields passed as kwargs: `connector_id`, `kb_id`, `error`, etc. |

---

## Writing logs {#logging-write}

Always use `structlog`. Never use `logging.getLogger()` for new code.

```python
import structlog
logger = structlog.get_logger()

# Basic levels
logger.info("Sync started", connector_id=connector_id)
logger.warning("Retry attempt", attempt=3, max=5)
logger.error("Sync failed", connector_id=connector_id, error=str(e))
logger.exception("Unexpected error")  # includes full traceback automatically

# Bind context for the duration of a request or background task
# All subsequent log calls in this scope will include these fields automatically
structlog.contextvars.bind_contextvars(org_id=org_id, connector_id=str(connector_id))
logger.info("Processing document", doc_id=doc_id)
structlog.contextvars.unbind_contextvars("org_id", "connector_id")
```

### What NOT to do

- Do not use `logging.getLogger()` for new log statements — outputs plain text without JSON fields
- Do not call `logging.basicConfig()` manually — `setup_logging()` handles stdlib routing
- Do not log secrets, passwords, or tokens as field values

---

## Adding logging to a new service {#logging-new-service}

1. Add `structlog>=25.0` to `pyproject.toml`
2. Copy `logging_setup.py` from an existing service (e.g. `klai-portal/backend/app/logging_setup.py`)
3. Change the `service_name` default to match the Docker service name
4. Call `setup_logging("my-service-name")` at module level in `main.py`, before any logger usage
5. Use `structlog.get_logger()` everywhere

---

## Querying logs (LogsQL) {#logging-query}

Query via **Grafana Explore → VictoriaLogs datasource** or the Klai Logs dashboard.

### Basic queries

```
# All logs for a service
{service="portal-api"}

# Filter by level
{service="portal-api"} level:error

# Full-text search
{service="portal-api"} "sync failed"

# Combine service and keyword
{service="klai-connector"} level:error "ConnectError"
```

### Structured field filters

```
# Exact field match (use :="" for exact value)
{service="portal-api"} org_id:="abc123"
{host="core-01"} request_id:="uuid-here"

# Filter by logger module
{service="portal-api"} level:error logger:="app.services.provisioning"

# All errors on a host
{host="core-01"} level:error
{host="public-01"} level:error
```

### Useful debug patterns {#logging-debug-patterns}

```
# All errors last hour on core-01
{host="core-01"} level:error

# Trace a specific connector sync run
{service="klai-connector"} connector_id:="<uuid>"

# Is a service healthy / producing logs?
{service="knowledge-ingest"}

# Full request trace in portal-api
{service="portal-api"} request_id:="<uuid>"

# All activity for a specific org
{service="portal-api"} org_id:="<org-id>"
```

---

## Accessing logs as an agent {#logging-agent-access}

When debugging production issues via SSH:

```bash
# Quick tail — human-readable, last 50 lines
ssh core-01 docker logs --tail 50 klai-core-portal-api-1 2>&1

# Follow live logs
ssh core-01 docker logs -f klai-core-portal-api-1 2>&1
```

VictoriaLogs is on the `klai-monitoring` network and is not reachable from the host directly. Use a temporary curl container:

```bash
# Query VictoriaLogs via curl container
ssh core-01 "docker run --rm --network klai-monitoring curlimages/curl:latest \
  'http://victorialogs:9428/select/logsql/query?query={service%3D\"portal-api\"}+level%3Aerror&limit=20'"
```

URL-encode the LogsQL query when passing it as a query parameter:
- `{` → `%7B`, `}` → `%7D`
- `"` → `%22`
- `=` → `%3D`
- space → `+`
