---
paths:
  - "klai-connector/**/*.py"
  - "klai-knowledge-mcp/**/*.py"
  - "klai-mailer/**/*.py"
  - "klai-retrieval-api/**/*.py"
  - "klai-scribe/**/*.py"
  - "klai-focus/**/*.py"
  - "klai-knowledge-ingest/**/*.py"
---
# Python Microservices

## Shared patterns
- All services use structlog (see `projects/portal-logging-py.md`).
- Config via `pydantic-settings` BaseSettings — defaults must match real production values.
- Use `asyncio.gather()` for parallel calls, `asyncio.wait_for()` for per-call deadlines.

## httpx client patterns
- Always set `timeout=` on external calls. Default httpx timeout is the safety net, not the deadline.
- Always include `**get_trace_headers()` from `app.trace` for inter-service calls — enables X-Request-ID correlation.
- Log status code + response body before returning generic errors.
- Catch `ConnectError` separately (no `.response` attribute).

## New field on existing request models
- Always `Optional` with safe default — required fields break existing callers with 422.
- Guard with explicit check when empty string is valid: `if body.field:`.
