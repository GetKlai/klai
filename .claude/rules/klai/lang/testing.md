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
After ANY Playwright testing, close all tabs then `browser_close()`. Brave locks `userDataDir` with `SingletonLock` — leaving it open blocks the next session.

## Playwright MCP workflow
1. Kill Brave before starting: `pkill -x "Brave Browser"`
2. Navigate: `browser_navigate({ url: '...' })`
3. Inspect: `browser_snapshot()` (prefer over screenshots for assertions)
4. Interact via `ref` from snapshot, never CSS selectors: `browser_click({ ref: 'e66' })`
5. Close browser when done (see rule above)

## Playwright session management
- Persistent profile at `~/.claude/mcp-brave-profile` — login sessions survive across runs.
- Brave locks `userDataDir` with `SingletonLock` — only one session at a time.
- If "Profile already in use": `pkill -x "Brave Browser"`
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

## Coroutine-never-awaited when mocking asyncio.create_task (MED)

Patching `asyncio.create_task` with `MagicMock` creates a coroutine that is never
awaited. Python fires `RuntimeWarning` via `sys.unraisablehook` during GC — after
pytest fixtures have torn down, so `warnings.filterwarnings` does not catch it.

**Why:** `sys.unraisablehook` fires at interpreter shutdown, outside pytest's capture scope.

**Prevention:** Replace the function that *produces* the coroutine with `MagicMock` —
no coroutine is created, no warning fires.

```python
@pytest.fixture(autouse=True)
def _mock_retrieval_log(monkeypatch):
    monkeypatch.setattr("app.api.partner.write_retrieval_log", MagicMock())
```

## setup_db result order must match db.execute call order (MED)

`setup_db(mock_db, [r1, r2, r3])` feeds results sequentially to each `db.execute` call
(last element cycles). A wrong order returns the right type with the wrong data — the
test may pass while asserting the wrong thing.

**Prevention:** Trace the exact `db.execute` call sequence in the production code before
writing the result list.

## Frontend test patterns
- UI bugfixes require browser verification — code reading scores zero.
- After bulk migrations (>10 files): run `tsc --noEmit` + `npm run lint`.
