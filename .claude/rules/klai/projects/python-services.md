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
- For portal-api inter-service calls: include `**get_trace_headers()` from `app.trace` — propagates X-Request-ID correlation. Other services receive trace context via `RequestContextMiddleware` in their `logging_setup.py`.
- Log status code + response body before returning generic errors.
- Catch `ConnectError` separately (no `.response` attribute).

## New field on existing request models
- Always `Optional` with safe default — required fields break existing callers with 422.
- Guard with explicit check when empty string is valid: `if body.field:`.

## pydantic-settings validates env vars at import time (HIGH)

Services that instantiate `Settings()` at module level fail test imports with `ValidationError: field required` if required env vars are absent.

**Why:** `pydantic-settings` reads and validates environment variables when `Settings()` is constructed — not lazily. Any test file that imports from such a module triggers this before any fixture runs.

**Prevention:** Add a `conftest.py` at the test root that sets required env vars with `os.environ.setdefault(...)` before any imports. This runs before pytest collects test modules.

## Stored config beats per-request plumbing

**When:** A downstream call needs a piece of configuration that originates from a user-created entity (e.g., which knowledge base to query for a notebook).

Store the config on the entity itself (e.g., `kb_slug` column on `Notebook`) and read it at call time. Do not pass it as a field in each request from the frontend.

**Why:** Per-request fields create an API contract that must be maintained by every caller (frontend, integrations, tests). Storing it once on the entity gives a single update point and eliminates the field from every request payload.

**Rule:** Entity-scoped config belongs on the entity, not in every request.

## Return bool from functions that may early-exit (MED)

**When:** A helper function can early-exit without performing its primary action (e.g., threshold not met, token missing, name clash).

Return `bool` — `True` only when the action was actually taken. Never `None` from a function that callers treat as "did it run?".

**Why:** Callers that set a counter/flag unconditionally after calling a void function will count no-ops as successes. `maybe_generate_proposal()` returned `None` on early-exit; the caller incremented `proposals_submitted = 1` regardless.
