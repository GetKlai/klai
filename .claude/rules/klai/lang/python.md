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

## Starlette middleware registration order (HIGH)

`app.add_middleware()` calls register in REVERSE execution order. The last-registered middleware is the outermost (runs first on request, last on response). The first-registered middleware is the innermost (runs last on request, closest to the route handler).

**Why:** Starlette wraps middlewares as a stack: each call wraps the current stack. So `add_middleware(A); add_middleware(B); add_middleware(C)` executes as C → B → A → route on request.

**Correct pattern for auth + CORS (register in this order):**
```python
app.add_middleware(AuthGuardMiddleware)        # innermost: runs 3rd on request
app.add_middleware(RequestContextMiddleware)   # middle: runs 2nd
app.add_middleware(CORSMiddleware, ...)        # outermost: runs 1st — wraps all 401s with CORS headers
```

**Prevention:** AuthGuard MUST be registered before CORSMiddleware. If it is registered after (= outermost), 401 responses bypass CORS and browsers block them silently. Mechanically enforced by `rules/cors_middleware_last.yml` per SPEC-SEC-CORS-001 REQ-6 — every klai FastAPI service workflow (portal-api, klai-connector, retrieval-api, scribe-api, knowledge-ingest, klai-mailer, klai-knowledge-mcp, klai-focus/research-api) runs the lint via `ast-grep/action` on every PR that touches its entry module. The lint fires on both the simple sibling case and the nested-if case (klai-connector pattern, where CORS lived inside `if allowed_origins:`).

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

## asyncio.to_thread() for sync SDKs

**When:** Wrapping a synchronous third-party SDK (e.g., minio, requests) in an async service.

`asyncio.to_thread(sync_func, *args)` runs the call in a thread pool without blocking the event loop. Cleaner than importing an async fork of the SDK that adds transitive dependencies (e.g., miniopy-async pulls in aiohttp).

```python
url = await asyncio.to_thread(client.presigned_get_object, bucket, key, expires=timedelta(hours=1))
```

**Rule:** Prefer `asyncio.to_thread()` over async SDK forks when the sync SDK is lightweight and call volume is moderate.

## Feature flag via empty env var

**When:** A feature depends on an external service that may not be deployed (e.g., S3 storage, analytics).

Use an empty-string default in pydantic-settings. The feature activates only when the env var is set:

```python
class Settings(BaseSettings):
    garage_s3_endpoint: str = ""  # empty = feature disabled

# Usage
if settings.garage_s3_endpoint:
    await upload_image(...)
```

**Rule:** Use empty string (not `None`, not a boolean flag) for optional service endpoints. One env var controls both "is configured" and "what to connect to."

## Temp directory cleanup — always use context manager (MED)

`tempfile.mkdtemp()` creates a directory but never cleans it up. In long-running services this leaks disk space silently.

**Why:** `mkdtemp()` returns a path string with no lifecycle management. If the caller forgets `shutil.rmtree()` (or an exception skips it), the dir persists forever.

**Prevention:** Always use `tempfile.TemporaryDirectory()` as a context manager:

```python
with tempfile.TemporaryDirectory() as tmpdir:
    # tmpdir is cleaned up on exit, even on exception
```

## Service restarts
- Always restart via restart scripts or `docker compose restart`, with output visible in foreground.
- Never use `run_in_background=true` to start servers — hides startup failures.
