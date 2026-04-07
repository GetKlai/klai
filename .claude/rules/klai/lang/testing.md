---
paths:
  - "**/test_*.py"
  - "**/*_test.py"
  - "**/*.test.ts"
  - "**/*.spec.ts"
  - "**/conftest.py"
---
# Testing Rules

## [HARD] Close browser when done
After ANY Playwright testing, call `browser_close()` explicitly. Even in isolated mode (which
auto-closes on session disconnect), closing explicitly is required — do not leave it for the
disconnect event.

## Playwright MCP workflow
1. Navigate: `browser_navigate({ url: '...' })`
2. Inspect: `browser_snapshot()` (prefer over screenshots for assertions)
3. Interact via `ref` from snapshot, never CSS selectors: `browser_click({ ref: 'e66' })`
4. **Always call `browser_close()` as the final step**

## Playwright session management
- **Isolated mode**: each Claude Code session gets a fresh browser context. Auto-closes on
  disconnect. No profile locking — multiple sessions can run in parallel.
- **Login persistence**: stored in `~/.claude/mcp-brave-storageState.json` (cookies + localStorage).
  Load fresh with `node scripts/export-mcp-session.mjs` if session expires.
- If login is gone after a session: re-log in, then re-run the export script.
- Grant permissions programmatically: `context.grantPermissions(['microphone'], { origin: '...' })`

## Browser console + GlitchTip
- Check browser errors: `browser_console_messages({ level: 'error' })`
- Production errors: `https://errors.getklai.com` → filter by project
- Check HTTP headers before debugging browser issues: `curl -sI <url> | grep -i permissions-policy`

## Python test patterns
- Use `pytest` with `asyncio` mode for async tests.
- Fixtures in `conftest.py` — keep test files focused on assertions.
- For Prometheus tests: use `REGISTRY` from fixture, not global `REGISTRY`.
- When writing async tests in a Python service for the first time, verify
  `pytest-asyncio` is actually installed — not just listed in `pyproject.toml`.
  `asyncio_mode = "auto"` in config with no package installed produces
  confusing failures. Fix: `uv sync --extra dev`.
- MagicMock is truthy for `.headers.get()` — set `request.headers = {}` explicitly when
  testing middleware that reads optional headers. Otherwise the mock returns a MagicMock
  object that passes truthiness checks.

## Inner-function import patching (HIGH)

When a function imports all its dependencies inside the function body (common in task workers), patching the module-level name fails.

**Why:** `patch("my_module.AsyncQdrantClient")` fails with AttributeError when `AsyncQdrantClient` is only imported inside `_run_backfill()`, not at module level.

**Prevention:** Patch at the source module: `patch("qdrant_client.AsyncQdrantClient")`. When in doubt, look at the actual `import` statement inside the function to find the correct patch target.

## asyncio.gather + AsyncMock produces coroutine-never-awaited warnings (MED)

Patching `asyncio.gather` with `AsyncMock` when the gather call wraps `asyncio.wait_for(inner_fn(), ...)` creates coroutines for the inner calls that are never awaited.

**Why:** `asyncio.wait_for(inner_fn(), ...)` creates a coroutine object before the mocked `gather` discards it, producing `RuntimeWarning: coroutine was never awaited`.

**Prevention:** Extract inner functions as module-level helpers so they can be patched individually. The caller then calls the helper directly, and tests patch the helper — no orphaned coroutines.

## Frontend test patterns
- UI bugfixes require browser verification — code reading scores zero.
- After bulk migrations (>10 files): run `tsc --noEmit` + `npm run lint`.
