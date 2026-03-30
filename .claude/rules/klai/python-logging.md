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

## Log levels

| Level | When |
|---|---|
| `debug` | Internal flow tracing, dev-only diagnostics |
| `info` | Business events: sync started, item ingested, user action |
| `warning` | Recoverable issues: retry, missing optional field, fallback |
| `error` | Failures with impact: sync failed, external call failed |
| `exception` | Unexpected errors — use instead of `error(..., exc_info=True)` |

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

## Full reference

Pattern guide: `.claude/rules/klai/patterns/logging.md` — includes LogsQL query examples for debugging.
