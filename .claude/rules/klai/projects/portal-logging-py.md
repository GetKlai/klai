---
paths:
  - "klai-portal/backend/**/*.py"
  - "klai-connector/**/*.py"
  - "klai-knowledge-mcp/**/*.py"
  - "klai-mailer/**/*.py"
  - "klai-retrieval-api/**/*.py"
  - "klai-scribe/**/*.py"
  - "klai-focus/**/*.py"
---

# Python Logging Standards

All Klai Python services use **structlog** with `ProcessorFormatter` to produce uniform JSON on both
`structlog.get_logger()` and `logging.getLogger()` call sites.

## Always use structlog

```python
import structlog
logger = structlog.get_logger()

# Basic
logger.info("Sync started", connector_id=connector_id)
logger.warning("Retry attempt", attempt=3, max=5)
logger.error("Sync failed", connector_id=connector_id, error=str(e))
logger.exception("Unexpected error")  # automatically includes traceback
```

**Never** use `logging.getLogger()` for new log statements — it outputs plain text in dev
but JSON in production thanks to `ProcessorFormatter`. The inconsistency makes local testing
harder. Use `structlog.get_logger()` everywhere.

## Bind request/task context

```python
import structlog

# Bind context for all logs inside a request or task — use contextvars, not logger.bind()
structlog.contextvars.bind_contextvars(org_id=org_id, connector_id=str(connector_id))
logger.info("Processing document", doc_id=doc_id)  # org_id + connector_id included automatically
structlog.contextvars.unbind_contextvars("org_id", "connector_id")  # or clear_contextvars()
```

portal-api's `LoggingContextMiddleware` binds `request_id`, `org_id`, `user_id` automatically on each request.

## Cross-service trace correlation

Caddy generates `X-Request-ID` per request. Portal-api reads it (or generates a UUID fallback)
and propagates it to all downstream services via `get_trace_headers()`:

```python
from app.trace import get_trace_headers

# In every httpx client call to internal services:
async with httpx.AsyncClient(
    headers={"X-Internal-Secret": secret, **get_trace_headers()},
) as client:
    resp = await client.get("/ingest/v1/...")
```

Downstream services (`knowledge-ingest`, `retrieval-api`, `connector`, `scribe`, `mailer`,
`research-api`) bind `X-Request-ID` and `X-Org-ID` from incoming headers via
`RequestContextMiddleware` in their `logging_setup.py`.

**Result:** One `request_id:<uuid>` query in VictoriaLogs shows the full chain across all services.

## Debugging with Grafana MCP

The `grafana` MCP server in `.mcp.json` gives AI agents direct access to VictoriaLogs.
Use it for production debugging instead of `docker logs`:

- All errors for a service: `service:portal-api AND level:error`
- Trace a request across services: `request_id:<uuid>`
- Tenant-scoped logs: `org_id:<org> AND level:error`
- Caddy access logs: `service:caddy AND status:5*`

## Log levels

| Level | When |
|---|---|
| `debug` | Internal flow tracing, dev-only diagnostics |
| `info` | Business events: sync started, item ingested, user action |
| `warning` | Recoverable issues: retry, missing optional field, fallback |
| `error` | Failures with impact: sync failed, external call failed |
| `exception` | Unexpected errors — use instead of `error(..., exc_info=True)` |

### except blocks MUST capture traceback (HARD)

Every `except Exception` block that logs MUST include a traceback. Two
acceptable forms:

```python
# Graceful degradation — upstream failure, caller continues
try:
    await upstream.maybe_do_thing()
except Exception:
    logger.warning("upstream_degraded", exc_info=True)   # traceback!

# Unexpected — state likely inconsistent, re-raising would be better
try:
    ...
except Exception:
    logger.exception("unexpected_failure")               # traceback by default
```

**Why:** `logger.warning("failed", error=str(exc))` throws away the
stack frame. When the same warning fires in production at 3am you have
no idea where it came from. `exc_info=True` on warning preserves level
semantics (still a warning, not an error) while keeping the traceback
queryable in VictoriaLogs.

**Prevention:** `ruff` rule `TRY401` catches
`logger.error(..., str(exc))` and `logger.warning(..., str(exc))` —
enabled in `pyproject.toml`'s ruff config. Prefer `exc_info=True` over
string interpolation of the exception.

## What to include as kwargs

Pass structured key/value pairs — not string concatenation:

```python
# Good
logger.error("Sync failed", connector_id=str(connector_id), kb_id=kb_id, error=str(e))

# Bad — loses structure, not queryable
logger.error(f"Sync failed for {connector_id}: {e}")
```

IDs, counts, and status values as separate kwargs make logs queryable in VictoriaLogs.

## Never log

- Secrets, passwords, tokens, API keys
- Full request/response bodies (log IDs and status codes instead)
- Every iteration of a loop (log totals or use sampling)

## Adding logging to a new service

1. Add `structlog>=25.0` to `pyproject.toml` (or `requirements.txt` for simple services)
2. Copy `logging_setup.py` from `klai-portal/backend/app/logging_setup.py`
3. Change the `service_name` default to the Docker service name
4. Call `setup_logging("my-service-name")` at module level in `main.py` (before any imports that log)
5. Use `structlog.get_logger()` everywhere
