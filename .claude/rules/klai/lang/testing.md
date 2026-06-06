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
After ANY Playwright testing, close all tabs then `browser_close()`. The persistent
profile dir is workspace-hashed (see below), but the running Chrome instance still
holds resources — leave a clean state for the next session.

## [HARD] Never click "Log out" in a persistent Playwright session
The default Playwright MCP browser uses a workspace-hashed PERSISTENT profile.
Clicking Log out wipes the login state for the entire workspace, forcing a fresh
hand-login next session. If you specifically need to test a logout/login flow,
spawn a session with `PLAYWRIGHT_ISOLATED=1` (ephemeral, logged-out profile) — the
workspace profile is untouched.

## Playwright MCP workflow
1. Navigate: `browser_navigate({ url: '...' })` — workspace profile is already logged in
2. Inspect: `browser_snapshot()` (prefer over screenshots for assertions)
3. Interact via `ref` from snapshot, never CSS selectors: `browser_click({ target: 'e66', element: '...' })`
4. Close browser when done (see rule above)

## [HARD] Route Klai E2E requests to the right environment
Klai has two different browser-test realities. They are not interchangeable.

| User wording | Environment | Command / first action |
|---|---|---|
| "test end-to-end", "test e2e", "test in de e2e/testomgeving" | isolated e2e tenant, bot user, synthetic but full stack | `cd klai-portal/frontend && set -a; source .env.local; set +a; npm run test:e2e:prod` |
| "test in Voys", "test in voice", "test met echte gebruiker", "real user" | Voys production tenant, captured Google SSO session, real user data | `cd klai-portal/frontend && npm run e2e:verify-voys-session` then `npm run test:e2e:prod:voys` |

When browser/auth context is involved, interpret "voice" as **Voys** unless the
user explicitly talks about audio, speech, or brand voice.

Rules:
- Do not satisfy an e2e/testomgeving request by testing Voys. The e2e tenant
  exists to prove the deployable stack with the bot user and cleanup-safe
  artifacts.
- Do not satisfy a Voys/voice request by testing `e2e.getklai.com`. Voys is the
  real-user environment and may reveal issues the bot tenant cannot simulate.
- If isolated e2e credentials fail (401, missing `E2E_TOTP_SECRET`, stale
  password), report that as a blocker. Do not silently fall back to Voys.
- If Voys storage-state is missing or `/api/me` is not 200, run
  `npm run e2e:capture-session` only when the user can complete Google SSO in
  the opened browser. Do not claim Voys testing worked without
  `npm run e2e:verify-voys-session` passing.
- Voys tests touch real production data. Only create/delete artifacts with the
  e2e prefix used by the suite; never click Log out in that session.

## Playwright session management
- Default profile: `~/Library/Caches/ms-playwright/mcp-chrome-{workspace-hash}` (macOS;
  analogous paths on Linux/Windows). Each Conductor workspace gets its own hash,
  so parallel sessions across workspaces don't collide.
- In this repo the Playwright MCP launcher preloads
  `klai-portal/frontend/e2e/prod-tenant/_config/storageState.voys.json` when it
  exists. That file is the source of truth for Voys/real-user MCP sessions.
  Override with `KLAI_PLAYWRIGHT_STORAGE_STATE=none|global|voys|/absolute/path`
  only when you are intentionally changing the session source.
- Login state persists across Claude Code restarts within a workspace — no periodic
  storage-state refresh needed (that was the pre-2026-05-13 `--isolated` pattern).
- `Browser is already in use` happens only when two MCP clients touch the SAME
  workspace's profile simultaneously. Fix: set `PLAYWRIGHT_ISOLATED=1` on the second
  one. Cross-workspace concurrency is fine.
- Storage-state seed (`~/.claude/mcp-storageState.json`) is optional first-boot
  preload, mostly useful for isolated sessions. The persistent profile auto-saves
  cookies on its own.
- To wipe a workspace's login: `rm -rf ~/Library/Caches/ms-playwright/mcp-chrome-*`
  (macOS) and restart Claude Code, then log in once more by hand.
- Grant permissions programmatically: `context.grantPermissions(['microphone'], { origin: '...' })`

For the full reasoning + anti-patterns, see `playwright-mcp-config-cycle` in
`.claude/rules/klai/pitfalls/process-rules.md`.

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

## AsyncMock makes db.add() async — override it (MED)

`AsyncMock` makes ALL methods async by default, including `db.add()`. SQLAlchemy's `Session.add()` is synchronous. Tests that call `await db.add(...)` or that never await `db.add()` produce `RuntimeWarning: coroutine was never awaited`.

**Why:** `AsyncMock` does not distinguish between async and sync methods — everything becomes a coroutine.

**Prevention:** After creating an `AsyncMock` for the DB session, explicitly override sync methods:

```python
db = AsyncMock()
db.add = MagicMock()  # keep add() synchronous
```

This preserves async behavior for `db.execute`, `db.commit`, etc. while matching SQLAlchemy's actual interface.

## Frontend test patterns
- UI bugfixes require browser verification — code reading scores zero.
- After bulk migrations (>10 files): run `tsc --noEmit` + `npm run lint`.
