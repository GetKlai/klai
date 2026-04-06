---
paths:
  - "**/*.py"
  - "**/pyproject.toml"
---
# Python Rules

## Async patterns
- Use `asyncio.gather()` for parallel calls, never `await` in a for loop (latency = sum vs max).
- Wrap each gather task with `asyncio.wait_for(coro, timeout=N)` for per-call deadlines.
- The outer httpx timeout is a safety net — `wait_for` is the real deadline.

## Error handling
- Before returning a generic error message, always `logger.error()` with: status code, response body (truncated), context vars.
- For `ConnectError` (no `.response`), log exception message and target URL.
- In except blocks, use `logger.exception()` (includes traceback) not `logger.error()`.
- `except (TimeoutError, Exception)` is dead code — `TimeoutError` is a subclass of `Exception`. Use `except Exception` alone. Similarly, never list a more-specific exception before a broader one in a tuple catch.

## FastAPI
- New fields on existing request models: always `Optional` with safe default. Required fields break existing callers with 422.
- Config defaults (`pydantic-settings`): always match the real production value. Wrong defaults are masked by env vars until a fresh deploy.

## Refactoring safety
- Run `ruff check` after each refactor step, not only at the end.
- F821 (undefined name) = always a runtime crash. Treat as blocker.
- After removing a function, grep for all symbols it imported — they may still be used elsewhere in the file.

## Tooling (ruff + pyright)
- Both ruff and pyright run in CI for portal-api. `# noqa: F401` only suppresses ruff, not pyright.
- For `__init__.py` re-exports, use `__all__` — satisfies both tools at once.
- ruff rules: E, F, I, UP, C90, B, TRY, S, RUF. Max complexity: 15.
- Run: `uv run ruff check .` and `uv run --with pyright pyright`

## Debug-first investigation
- When investigating API errors, add debug logging first to see actual payload/response before writing any fix.

## Server checks
- Use `lsof -nP -iTCP:PORT -sTCP:LISTEN` to check if a port is in use. Curl without timeouts hangs indefinitely.
- If you must use curl: `--connect-timeout 2 --max-time 3`.

## Service restarts
- Always restart via restart scripts or `docker compose restart`, with output visible in foreground.
- Never use `run_in_background=true` to start servers — hides startup failures.
