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

**Required content** (restore this if the file is missing or any MCP server stops working):

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
      "args": ["@playwright/mcp@latest", "--isolated", "--browser", "chromium", "--executable-path", "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"],
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
| **playwright** | Browser automation for E2E spot-checks and visual verification. Uses Brave Browser via a dedicated profile. |

Browser config is passed as direct CLI args — no config file needed. The `--executable-path` is the standard Brave install location on macOS (same for all users).

**`--isolated` flag (required)**

The `--isolated` flag starts each session with a fresh temporary profile directory. Without it, a crashed or leftover browser process holds a lock on the profile and the next request fails with:

```
Error: Browser is already in use for .../mcp-chrome-for-testing-..., use --isolated to run multiple instances
```

Trade-off: login sessions are not preserved between Claude Code restarts. Use `PLAYWRIGHT_MCP_USER_DATA_DIR` (below) if you need persistent sessions.

**Persistent login sessions (required for autonomous testing)**

The `--user-data-dir` is NOT in `.mcp.json` because it contains a username-specific path. Instead, set it once in your shell profile so the MCP server inherits it automatically:

```bash
# Add to ~/.zshrc (or ~/.bashrc)
export PLAYWRIGHT_MCP_USER_DATA_DIR=~/.claude/mcp-brave-profile
```

Then `source ~/.zshrc` (or open a new terminal) and restart Claude Code. The `~` is expanded by the shell before the MCP server starts, so this works correctly.

Note: when using a persistent `--user-data-dir`, the `--isolated` flag is still recommended to avoid profile locking issues across sessions.

For session management rules (when to open/close the browser, profile locking), see
`.claude/rules/klai/patterns/testing.md`.

**Common failure modes:**

1. **Binary missing** — `which serena` returns nothing. Cause: uv cache eviction removed the
   environment, or Serena was never installed with `uv tool install`. Fix: `uv tool install git+https://github.com/oraios/serena`
2. **uvx in .mcp.json** — If `command` is `"uvx"` instead of `"serena"`, it clones and rebuilds
   on every startup → MCP timeout → Serena never available. Fix: use `"command": "serena"`.
3. **MCP timeout** — Serena takes too long to index. Check `.serena/project.yml` for overly broad
   file patterns.

## 3. Disable Serena web dashboard

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

## 4. Restore Serena memories and project config

Serena memories and `project.yml` are stored in the workspace at `.serena/` (not committed to git).
After a fresh clone, activate the project and Serena will initialise a fresh memory store.

## 5. Install GitHub CLI

Required for CI verification after `git push` (see `.claude/rules/klai/post-push.md`).

```bash
brew install gh
gh auth login
```

For other platforms: https://github.com/cli/cli#installation
