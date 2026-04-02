# MCP Server Setup

> Developer workstation setup for Claude Code MCP integrations.
> Read this when setting up a new machine or when an MCP server stops working.

## 1. Install Serena

```bash
uv tool install git+https://github.com/oraios/serena
```

Do NOT use `uvx --from git+...` in `.mcp.json` — that clones and rebuilds Serena on every Claude
Code startup, which exceeds the MCP timeout and causes Serena to fail silently.

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
    "sequential-thinking": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
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
| **sequential-thinking** | Step-by-step reasoning for complex problems (UltraThink mode). Used by specialized agents for architecture decisions and deep analysis. |
| **playwright** | Browser automation for E2E spot-checks and visual verification. Uses Brave Browser via a dedicated profile. |

The `.playwright-mcp/config.json` is tracked in git with the macOS config (default dev machine).
For Windows, overwrite it locally — git will show a diff but the file won't be gitignored.

**macOS** (committed, default):
```json
{
  "executablePath": "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
  "userDataDir": "/Users/mark/.claude/mcp-brave-profile"
}
```

**Windows** (local override):
```json
{
  "executablePath": "C:/Program Files/BraveSoftware/Brave-Browser/Application/brave.exe",
  "userDataDir": "C:/Users/markv/.claude/mcp-brave-profile"
}
```

For session management rules (when to open/close the browser, profile locking), see
`.claude/rules/klai/patterns/testing.md`.

**Common failure mode:** If Serena stops loading, check that the `command` is `"serena"` (not
`"uvx"` with `--from git+...`). The uvx variant clones and rebuilds on every startup -> MCP
timeout -> Serena never available.

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
