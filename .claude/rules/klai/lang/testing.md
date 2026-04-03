---
paths:
  - "**/test_*.py"
  - "**/*_test.py"
  - "**/*.test.ts"
  - "**/*.spec.ts"
  - "**/conftest.py"
---
# Testing Rules

## Playwright MCP workflow
1. Kill Brave before starting: `pkill -x "Brave Browser"`
2. Navigate: `browser_navigate({ url: '...' })`
3. Inspect: `browser_snapshot()` (prefer over screenshots for assertions)
4. Interact via `ref` from snapshot, never CSS selectors: `browser_click({ ref: 'e66' })`
5. [HARD] Close browser when done: close all tabs, then `browser_close()`

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

## Frontend test patterns
- UI bugfixes require browser verification — code reading scores zero.
- After bulk migrations (>10 files): run `tsc --noEmit` + `npm run lint`.
