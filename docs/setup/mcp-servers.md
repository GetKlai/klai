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
      "args": ["@playwright/mcp@0.0.70", "--config", ".playwright-mcp/config.json"],
      "env": {}
    },
    "codeindex": {
      "type": "stdio",
      "command": "codeindex",
      "args": ["mcp"],
      "env": {}
    },
    "grafana": {
      "type": "stdio",
      "command": "uvx",
      "args": ["mcp-grafana", "--disable-write"],
      "env": {
        "GRAFANA_URL": "https://grafana.getklai.com"
      }
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
| **codeindex** | Graph-powered code intelligence — call graphs, impact analysis, semantic search, communities, and enrichment queries (git hotspots, SPEC links, test coverage, PageRank). |
| **grafana** | Read-only access to Grafana dashboards, VictoriaLogs queries, and alerts for production debugging. Preferred over `docker logs` for investigating issues. |

## 3. Set up Playwright (per machine)

Playwright uses a **local config file** for platform-specific settings. This file is gitignored —
each developer creates their own from the example.

```bash
cp .playwright-mcp/config.example.json .playwright-mcp/config.json
```

Then edit `.playwright-mcp/config.json` and set `executablePath` and `storageState` for your
platform:

| Platform | `executablePath` |
|----------|------------------|
| **macOS** | `/Applications/Brave Browser.app/Contents/MacOS/Brave Browser` |
| **Windows** | `C:/Program Files/BraveSoftware/Brave-Browser/Application/brave.exe` |
| **Linux** | `/usr/bin/brave-browser` |

**Example config (macOS):**

```json
{
  "browser": "chromium",
  "executablePath": "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
  "isolated": true,
  "launchOptions": {
    "ignoreDefaultArgs": ["--no-sandbox"]
  },
  "contextOptions": {
    "storageState": "/Users/yourname/.claude/mcp-brave-storageState.json"
  }
}
```

**Example config (Windows):**

```json
{
  "browser": "chromium",
  "executablePath": "C:/Program Files/BraveSoftware/Brave-Browser/Application/brave.exe",
  "isolated": true,
  "launchOptions": {
    "ignoreDefaultArgs": ["--no-sandbox"]
  },
  "contextOptions": {
    "storageState": "C:/Users/yourname/.claude/mcp-brave-storageState.json"
  }
}
```

> **Note:** All paths must be absolute. Use forward slashes on all platforms (including Windows).
> No `~` expansion in JSON — write the full path.

### How it works: isolated mode + storageState

`"isolated": true` creates a fresh browser context per Claude Code session and **closes it
automatically on disconnect**. This means:

- Browser and tabs close when the Claude Code session ends — no lingering Brave windows.
- Multiple Claude Code windows can run in parallel without profile locking.

Login state persists across sessions via `storageState.json` — a JSON snapshot of cookies and
localStorage that is loaded at context creation.

### First-time setup: initialize the session file

Run the setup script once to create the `storageState.json` file. If you have an existing
persistent profile, it exports your current login cookies. If not, it creates an empty file.

```bash
node scripts/export-mcp-session.mjs
```

Then restart Claude Code.

### Refreshing the session (after login expiry)

If your login expires (e.g., after 30 days), log in again during a test session, then re-run:

```bash
node scripts/export-mcp-session.mjs
```

This re-exports the updated cookies from the last active profile or prompts for a fresh login.

### Starting from scratch

Delete the storageState file to reset to a logged-out state:

```bash
# macOS / Linux
rm ~/.claude/mcp-brave-storageState.json

# Windows (Git Bash)
rm ~/AppData/../.claude/mcp-brave-storageState.json
```

Then re-run `node scripts/export-mcp-session.mjs` (creates empty file) and log in on first use.

For session management rules (when to open/close the browser), see
`.claude/rules/klai/lang/testing.md`.

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

## 7. Install CodeIndex

CodeIndex provides graph-powered code intelligence (call graphs, impact analysis, semantic search).
It is distributed as a private npm package.

```bash
# Install from klai-private
npm install -g klai-private/tools/codeindex-1.3.56.tgz

# Configure MCP, hooks, and skills
codeindex setup

# Index the codebase (creates KuzuDB graph in ~/.codeindex/klai/)
codeindex analyze

# Run enrichment (git hotspots, SPEC links, test mapping, PageRank)
node scripts/codeindex-enrich.mjs
```

After code changes, refresh the index:

```bash
codeindex update && node scripts/codeindex-enrich.mjs
```

Or force a full re-index:

```bash
./scripts/codeindex-analyze-and-enrich.sh --force
```

**File locations:**

| What | Where | Committed |
|------|-------|-----------|
| KuzuDB graph | `~/.codeindex/klai/kuzu` | No (per-machine) |
| Enrichment sidecar | `~/.codeindex/klai/enrichment.json` | No |
| CodeIndex hooks | `~/.claude/hooks/codeindex/` | No (installed by setup) |
| CodeIndex skills | `.claude/skills/codeindex/` | Yes |
| Enrichment script | `scripts/codeindex-enrich.mjs` | Yes |
| Wrapper script | `scripts/codeindex-analyze-and-enrich.sh` | Yes |

For usage guidelines (when to use CodeIndex vs Serena), see `.claude/rules/klai/codeindex.md`.

## 8. Install Grafana MCP

Grafana MCP provides read-only access to dashboards, VictoriaLogs, and alerts for production
debugging. It runs via `uvx` (acceptable here since `mcp-grafana` is a small, fast package).

**Prerequisites:**

Create a **per-developer** service account token in Grafana (one per machine, so tokens can be
revoked individually):

1. Go to Grafana → Admin → Service Accounts → Add service account
2. Name: `claude-<yourname>`, Role: **Viewer**
3. Click the account → Add service account token → name it `claude-code-<yourname>`
4. Copy the token (`glsa_...`)

Set the token as environment variable `GRAFANA_SERVICE_ACCOUNT_TOKEN`. Add it to your shell
profile:

```bash
# macOS / Linux — add to ~/.zshrc or ~/.bashrc
export GRAFANA_SERVICE_ACCOUNT_TOKEN="glsa_..."

# Windows (Git Bash) — create ~/.bashrc if it doesn't exist
echo 'export GRAFANA_SERVICE_ACCOUNT_TOKEN="glsa_..."' >> ~/.bashrc
```

**Verify:**

```bash
uvx mcp-grafana --help
```

For usage patterns and LogsQL queries, see `.claude/rules/klai/infra/observability.md`.

## Common failure modes

1. **Serena binary missing** — `which serena` returns nothing. Cause: uv cache eviction or never
   installed with `uv tool install`. Fix: `uv tool install git+https://github.com/oraios/serena`
2. **uvx in .mcp.json** — If `command` is `"uvx"` instead of `"serena"`, it clones and rebuilds
   on every startup → MCP timeout → Serena never available. Fix: use `"command": "serena"`.
3. **MCP timeout** — Serena takes too long to index. Check `.serena/project.yml` for overly broad
   file patterns.
4. **Playwright config missing** — `config.json` not found. Fix: `cp .playwright-mcp/config.example.json .playwright-mcp/config.json` and edit paths.
5. **Playwright storageState missing** — `storageState.json` not found → MCP fails on startup. Fix: `node scripts/export-mcp-session.mjs` then restart Claude Code.
6. **Playwright opens Chrome instead of Brave** — `"browser": "chromium"` missing from `config.json`. Fix: add that field.
7. **Playwright browser not closing** — `"isolated": true` missing from `config.json`. Without it, the browser stays open across sessions.
8. **Brave warns "unsupported command-line flag: --no-sandbox"** — Playwright injects `--no-sandbox` by default for all Chromium launches. Brave treats it as unsupported. Fix: add `"launchOptions": { "ignoreDefaultArgs": ["--no-sandbox"] }` to `config.json`.
9. **CodeIndex not found** — `codeindex` command not available. Fix: `npm install -g klai-private/tools/codeindex-1.3.56.tgz`
10. **CodeIndex stale index** — Index behind HEAD. Symptoms: impact analysis misses recent code. Fix: `codeindex update && node scripts/codeindex-enrich.mjs`
11. **Grafana token missing** — `GRAFANA_SERVICE_ACCOUNT_TOKEN` not set. Symptoms: Grafana MCP fails to connect. Fix: create a per-developer service account in Grafana (see section 8) and export the token in your shell profile. On Windows (Git Bash), `~/.bashrc` may not exist — create it manually.
