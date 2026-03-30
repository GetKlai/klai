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

The MCP config lives at `/Users/mark/Server/projects/.mcp.json` (one level above the klai repo —
not committed to git). This file is shared across all projects in the workspace.

**Required content** (restore this if the file is missing or any MCP server stops working):

```json
{
  "mcpServers": {
    "playwright": {
      "type": "stdio",
      "command": "npx",
      "args": [
        "@playwright/mcp@latest",
        "--executable-path",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "--user-data-dir",
        "/Users/mark/Library/Caches/ms-playwright/klai-profile"
      ],
      "env": {}
    },
    "serena": {
      "type": "stdio",
      "command": "serena",
      "args": ["start-mcp-server", "--project-from-cwd"],
      "env": {}
    },
    "context7": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@upstash/context7-mcp"],
      "env": {}
    }
  }
}
```

**Common failure mode:** If Serena stops loading, check that the `command` is `"serena"` (not
`"uvx"` with `--from git+...`). The uvx variant clones and rebuilds on every startup -> MCP
timeout -> Serena never available.

## 3. Disable Serena dashboard auto-open

By default Serena opens a browser tab every time Claude Code starts. Disable this in
`~/.serena/serena_config.yml`:

```yaml
web_dashboard_open_on_launch: false
```

The dashboard remains available at `http://localhost:24282/dashboard/` when needed.

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
