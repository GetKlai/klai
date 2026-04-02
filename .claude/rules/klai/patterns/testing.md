---
paths:
  - "**/test_*.py"
  - "**/*_test.py"
  - "**/*.test.ts"
  - "**/*.spec.ts"
  - "**/conftest.py"
---
# Frontend Testing with Playwright

> Standard workflow for browser-based UI testing via the Playwright MCP tool.

## Index
> Keep this index in sync — add a row when adding a section below.

| Section | When to use | Evidence |
|---|---|---|
| [Setup](#setup) | Initial Playwright MCP configuration and profile | `browser_navigate` opens Brave with profile |
| [Standard workflow](#standard-workflow) | Step-by-step browser testing process | `browser_snapshot()` returns page elements |
| [Session management rules](#session-management-rules) | When to open/close browser, profile locking | `browser_close()` releases SingletonLock file |
| [Checking HTTP headers](#checking-http-headers) | Verifying response headers in tests | `curl -sI <url>` shows expected header values |
| [Debugging with GlitchTip](#debugging-with-glitchtip) | Using GlitchTip for error monitoring | GlitchTip issue list shows captured error |

---

## Setup

The Playwright MCP is configured in `.mcp.json` with `.playwright-mcp/config.json` (tracked in git).

**Browser:** Brave Browser at `/Applications/Brave Browser.app/Contents/MacOS/Brave Browser`
**Profile:** Persistent profile at `~/.claude/mcp-brave-profile` — login sessions survive across test runs.

Because it's a persistent profile, you typically only need to log in once. Cookies, localStorage, and session data carry over between Claude Code sessions.

**Config file** (`.playwright-mcp/config.json`):
```json
{
  "executablePath": "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
  "userDataDir": "/Users/mark/.claude/mcp-brave-profile"
}
```

---

## Standard workflow

### 1. Kill Brave before starting

Playwright cannot launch Brave while it's already running — Chrome/Brave locks the profile directory (`SingletonLock`). Always kill it first:

```bash
pkill -x "Brave Browser"
```

Then navigate:

```js
browser_navigate({ url: 'https://...' })
```

### 2. Inspect the page

Use `browser_snapshot()` to get an accessibility tree of the current state.
This is faster and more reliable than screenshots for asserting UI state.

```js
browser_snapshot()
```

Use `browser_take_screenshot()` only when you need to visually verify layout.

### 3. Grant browser permissions (mic, camera, etc.)

Playwright's browser won't show permission dialogs automatically.
Grant permissions programmatically before loading the page:

```js
browser_run_code(async (page) => {
  const context = page.context()
  await context.grantPermissions(['microphone'], { origin: 'https://getklai.getklai.com' })
  await page.reload()
  await page.waitForTimeout(1500)
})
```

Other grantable permissions: `'camera'`, `'geolocation'`, `'notifications'`, `'clipboard-read'`.

### 4. Interact with elements

Always use the `ref` from a snapshot, not CSS selectors:

```js
browser_click({ ref: 'e66', element: 'Start recording button' })
```

### 5. Close tabs and browser when done (HARD)

**[HARD] Always close all tabs and the browser after testing.** Leaving it open blocks other Claude Code sessions from using Playwright.

Close procedure — execute in order:

```js
// 1. List open tabs
browser_tabs({ action: 'list' })

// 2. Close all tabs except the first (repeat from highest index down)
browser_tabs({ action: 'close', index: N })  // N = highest tab index
browser_tabs({ action: 'close', index: 1 })  // keep closing until only tab 0 remains

// 3. Close the browser entirely
browser_close()
```

**Why this matters:**
- Brave/Chrome locks `userDataDir` with `SingletonLock` — only one process can use it
- If a previous session left the browser open, the next session gets "Profile already in use"
- The persistent profile preserves login state even after `browser_close()` — closing is safe

**If browser_close() fails or reports "Browser is already in use":**
```bash
pkill -x "Brave Browser"
```

### Session management rules

1. **Start of testing:** kill Brave, then `browser_navigate`
2. **During testing:** use snapshots over screenshots, interact via refs
3. **End of testing:** close all tabs, then `browser_close()` — do this IMMEDIATELY when done
4. **Never leave the browser open** between tasks or when switching to non-Playwright work
5. **Login state persists** in the profile — no need to re-login after closing

---

## Checking HTTP headers

Before debugging browser-side API issues, verify what headers the server sends:

```bash
curl -sI https://getklai.getklai.com/ | grep -i permissions-policy
```

Common blockers:
- `Permissions-Policy: microphone=()` — blocks `getUserMedia` entirely, browser never prompts
- `Content-Security-Policy` — can block media, inline scripts, or API calls

The Caddyfile is at `klai-infra/core-01/caddy/Caddyfile`. After editing, copy and restart:

```bash
scp klai-infra/core-01/caddy/Caddyfile core-01:/opt/klai/caddy/Caddyfile
ssh core-01 'docker restart klai-core-caddy-1'
```

---

## Debugging with GlitchTip

The portal uses `@sentry/react` pointing at **GlitchTip** at `https://errors.getklai.com`.
Frontend errors and unhandled exceptions are captured automatically in production.

**When to check GlitchTip:**
- A feature fails in production but not locally
- Playwright's console shows a vague error (e.g. `Failed to load resource: 403`)
- You want to see the full stack trace including component tree

**How to use:**
1. Open `https://errors.getklai.com` and log in
2. Filter by project: `portal-frontend`
3. Look for recent issues matching your test window

**During a Playwright session**, also check the browser console directly:

```js
browser_console_messages({ level: 'error' })
```

This captures errors that GlitchTip may not have sent yet (e.g. network failures before Sentry initialises).

---

## See Also

- [patterns/frontend.md](frontend.md) - Component patterns, button placement, form structure
- [pitfalls/process.md](../pitfalls/process.md) - General process rules
