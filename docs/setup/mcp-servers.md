# MCP Server Setup

> Developer workstation setup for Claude Code MCP integrations.
> Read this when setting up a new machine or when an MCP server stops working.

## 1. Install Serena

```bash
uv tool install git+https://github.com/oraios/serena
```

This places a permanent symlink at `~/.local/bin/serena`. Verify after install:

```bash
which serena   # should print ~/.local/bin/serena
serena --version
```

**Why `uv tool install` and not `uvx`?**

`uvx` creates a temporary cached environment in `~/.cache/uv/environments-v2/`. This cache is
**not permanent** — uv garbage-collects old environments automatically. When the cache is pruned,
the `serena` binary silently disappears and the MCP server fails to start on the next Claude Code
session. `uv tool install` avoids this by creating a persistent installation.

Do NOT use `uvx --from git+...` in `.mcp.json` — besides the cache eviction risk, it clones and
rebuilds Serena on every Claude Code startup, which exceeds the MCP timeout.

**If Serena stops working after it was previously fine:** the most likely cause is uv cache
eviction. Re-run `uv tool install git+https://github.com/oraios/serena` and restart Claude Code.

## 2. Configure `.mcp.json`

The MCP config lives at `.mcp.json` in the klai repo root (committed to git).
It is **cross-platform** — all platform-specific settings live in local config files (see below).

**Current content:**

```json
{
  "$schema": "https://raw.githubusercontent.com/anthropics/claude-code/main/.mcp.schema.json",
  "mcpServers": {
    "serena": {
      "type": "stdio",
      "command": "serena",
      "args": ["start-mcp-server", "--project-from-cwd"],
      "env": {}
    },
    "context7": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@upstash/context7-mcp@latest"],
      "env": {}
    },
    "playwright": {
      "type": "stdio",
      "command": "npx",
      "args": ["@playwright/mcp@latest", "--config", ".playwright-mcp/config.json"],
      "env": {}
    }
  }
}
```

### What each server does

| Server | Purpose |
|--------|---------|
| **serena** | Semantic code navigation (symbol search, references, go-to-definition) and persistent project memories. Uses LSP for Python and TypeScript. |
| **context7** | Up-to-date library documentation (React, FastAPI, Next.js, etc.). Prefer over web search for API docs. |
| **playwright** | Browser automation for E2E spot-checks and visual verification. Uses a persistent browser profile so login sessions survive across Claude Code restarts. |

## 3. Set up Playwright (per machine)

Playwright uses a **local config file** for platform-specific settings (browser path, profile
directory). This file is gitignored — each developer creates their own from the example.

```bash
cp .playwright-mcp/config.example.json .playwright-mcp/config.json
```

Then edit `.playwright-mcp/config.json` and set `executablePath` for your platform:

| Platform | `executablePath` |
|----------|------------------|
| **macOS** | `/Applications/Brave Browser.app/Contents/MacOS/Brave Browser` |
| **Windows** | `C:\\Program Files\\BraveSoftware\\Brave-Browser\\Application\\brave.exe` |
| **Linux** | `/usr/bin/brave-browser` |

The config also sets `userDataDir` for persistent login sessions. Default: `~/.claude/mcp-brave-profile`.

**Example config (macOS):**

```json
{
  "browser": "chromium",
  "executablePath": "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
  "userDataDir": "/Users/yourname/.claude/mcp-brave-profile"
}
```

> **Note:** `userDataDir` must be an absolute path (no `~` expansion in JSON).
> On Windows, use forward slashes or escaped backslashes: `C:/Users/yourname/.claude/mcp-brave-profile`.

### Persistent login sessions

The `userDataDir` setting keeps cookies and login state across Claude Code sessions. This means
you log in once (e.g., to `getklai.getklai.com`) and subsequent sessions reuse that session.

If you want to start fresh, delete the profile directory:

```bash
rm -rf ~/.claude/mcp-brave-profile
```

### Profile locking

If a previous browser process crashed or was not properly closed, the profile directory may be
locked. Symptoms: MCP server fails to start, or error mentioning "already in use".

Fix: close all Brave/Chromium processes, or delete the lock files:

```bash
rm -f ~/.claude/mcp-brave-profile/SingletonLock
rm -f ~/.claude/mcp-brave-profile/SingletonCookie
rm -f ~/.claude/mcp-brave-profile/SingletonSocket
```

For session management rules (when to open/close the browser), see
`.claude/rules/klai/patterns/testing.md`.

## 4. Disable Serena web dashboard

By default Serena starts a web dashboard and opens a browser tab on every Claude Code launch.
Disable it completely in `~/.serena/serena_config.yml`:

```yaml
web_dashboard: false
```

If you want the dashboard running but not auto-opening a tab, use this instead:

```yaml
web_dashboard: true
web_dashboard_open_on_launch: false
```

The dashboard is then available at `http://localhost:24282/dashboard/` when needed.

## 5. Restore Serena memories and project config

Serena memories and `project.yml` are stored in the workspace at `.serena/` (not committed to git).
After a fresh clone, activate the project and Serena will initialise a fresh memory store.

## 6. Install GitHub CLI

Required for CI verification after `git push` (see `.claude/rules/klai/post-push.md`).

```bash
brew install gh
gh auth login
```

For other platforms: https://github.com/cli/cli#installation

## Common failure modes

1. **Serena binary missing** — `which serena` returns nothing. Cause: uv cache eviction or never
   installed with `uv tool install`. Fix: `uv tool install git+https://github.com/oraios/serena`
2. **uvx in .mcp.json** — If `command` is `"uvx"` instead of `"serena"`, it clones and rebuilds
   on every startup → MCP timeout → Serena never available. Fix: use `"command": "serena"`.
3. **MCP timeout** — Serena takes too long to index. Check `.serena/project.yml` for overly broad
   file patterns.
4. **Playwright config missing** — `config.json` not found. Fix: `cp .playwright-mcp/config.example.json .playwright-mcp/config.json` and edit paths.
5. **Playwright profile locked** — Browser didn't close cleanly. Fix: remove `SingletonLock` files (see above) or kill lingering browser processes.
