# Frontend Testing with Playwright

> Standard workflow for browser-based UI testing via the Playwright MCP tool.

## Index
> Keep this index in sync — add a row when adding a section below.

| Section | When to use |
|---|---|
| [Setup](#setup) | Initial Playwright MCP configuration |
| [Standard workflow](#standard-workflow) | Step-by-step browser testing process |
| [Checking HTTP headers](#checking-http-headers) | Verifying response headers in tests |
| [Debugging with GlitchTip](#debugging-with-glitchtip) | Using GlitchTip for error monitoring |

---

## Setup

The Playwright MCP is configured to use **Brave Browser** from `/Applications/Brave Browser.app`.
This is a persistent browser profile stored at `~/.cache/ms-playwright/klai-profile`.

Because it's a persistent profile, login sessions survive across test runs — you typically only need to log in once.

---

## Standard workflow

### 1. Kill Brave before starting

Playwright cannot launch its own Brave instance while Brave is already running.
Always kill it first:

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

### 5. Close tabs and browser when done

Close all open tabs first, then close the browser so Brave can resume normally:

```js
// Close all non-essential tabs (repeat for each open tab)
browser_tabs({ action: 'close', index: 1 })

// Then close the browser entirely
browser_close()
```

**Always close the browser after testing.** Leaving it open blocks the next Playwright session.

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
