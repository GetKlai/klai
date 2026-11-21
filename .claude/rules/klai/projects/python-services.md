---
paths:
  - "klai-connector/**/*.py"
  - "klai-knowledge-mcp/**/*.py"
  - "klai-mailer/**/*.py"
  - "klai-retrieval-api/**/*.py"
  - "klai-scribe/**/*.py"
  - "klai-knowledge-ingest/**/*.py"
---
# Python Microservices

## Shared patterns
- All services use structlog (see `projects/portal-logging-py.md`).
- Config via `pydantic-settings` BaseSettings — defaults must match real production values.
- Use `asyncio.gather()` for parallel calls, `asyncio.wait_for()` for per-call deadlines.

## httpx client patterns
- Always set `timeout=` on external calls. Default httpx timeout is the safety net, not the deadline.
- For portal-api inter-service calls: include `**get_trace_headers()` from `app.trace` — propagates X-Request-ID correlation. Other services receive trace context via `RequestContextMiddleware` in their `logging_setup.py`.
- Log status code + response body before returning generic errors.
- Catch `ConnectError` separately (no `.response` attribute).

## New field on existing request models
- Always `Optional` with safe default — required fields break existing callers with 422.
- Guard with explicit check when empty string is valid: `if body.field:`.


## Alembic revision ids fit a varchar(32) (HIGH)

`alembic_version.version_num` is a `varchar(32)`. A longer revision id passes
every test and then fails at container start, when alembic records it: the
service restarts in a loop until the deploy rolls back (klai-connector, twice,
last on 2026-11-12 with a 37-character id). klai-connector checks the length in
`tests/test_migration_revision_ids.py`; a service that gains migrations gets the
same test.
