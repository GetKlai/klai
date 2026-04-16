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
      "args": ["@playwright/mcp@0.0.70", "--config", ".claude/playwright-config-win.json"],
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
    },
    "victorialogs": {
      "type": "stdio",
      "command": "/Users/mark/bin/mcp-victorialogs",
      "env": {
        "VL_INSTANCE_ENTRYPOINT": "http://localhost:9428",
        "VL_INSTANCE_HEADERS": "Authorization=Basic ${VICTORIALOGS_BASIC_AUTH_B64}"
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
| **grafana** | Read-only access to Grafana dashboards, Prometheus/VictoriaMetrics queries, and alerts. Cannot query VictoriaLogs — use the `victorialogs` MCP for log queries instead. |
| **victorialogs** | Production log queries via LogsQL against VictoriaLogs. Requires SSH tunnel (`./scripts/victorialogs-tunnel.sh`) and `VICTORIALOGS_BASIC_AUTH_B64` env var. Preferred over `docker logs` for investigating issues. |

## 3. Set up Playwright (per machine)

Playwright uses **platform-specific config files** committed to git. The `.mcp.json` points to
the Windows config by default. Mac developers update the `--config` arg to point to the Mac file.

| Platform | Config file |
|----------|-------------|
| **Windows** | `.claude/playwright-config-win.json` |
| **macOS** | `.claude/playwright-config-mac.json` |

Update `.mcp.json` to point to the right file for your platform:

```json
"args": ["@playwright/mcp@0.0.70", "--config", ".claude/playwright-config-mac.json"]
```

### Config files

**Windows** (`.claude/playwright-config-win.json`):

```json
{
  "browser": {
    "browserName": "chromium",
    "userDataDir": "C:\\Users\\yourname\\.claude\\mcp-brave-profile"
  }
}
```

**macOS** (`.claude/playwright-config-mac.json`):

```json
{
  "browser": {
    "browserName": "chromium",
    "userDataDir": "/Users/yourname/.claude/mcp-brave-profile"
  }
}
```

Update `yourname` to match your local username.

### How it works: bundled Chromium + userDataDir

Playwright uses its **bundled Chromium** (not Brave or Chrome). This avoids a Windows limitation
where Brave/Chrome cannot run two simultaneous instances with different user profiles — the second
instance becomes a background process with no visible window.

Login state persists across Claude Code sessions via `userDataDir` — a directory on disk where
Chromium stores cookies, localStorage, and session data. The profile accumulates logins over time;
you only need to log in once per site.

### First-time login

On first use, Playwright opens Chromium at the login page. Log in manually in that window.
Credentials are saved to `userDataDir` automatically. Subsequent sessions start logged in.

### Starting from scratch

Delete the profile directory to reset to a logged-out state:

```bash
# Windows (Git Bash)
rm -rf ~/.claude/mcp-brave-profile

# macOS / Linux
rm -rf ~/.claude/mcp-brave-profile
```

Then restart Claude Code. Log in again on first use.

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

## 8. Install VictoriaLogs MCP

VictoriaLogs MCP provides direct LogsQL queries against production logs. It is the primary tool
for debugging production issues — preferred over `docker logs` for cross-service investigation.

**Install the binary:**

```bash
# Download from GitHub releases (macOS ARM64 example)
curl -sL "https://github.com/VictoriaMetrics/mcp-victorialogs/releases/download/v1.8.0/mcp-victorialogs_Darwin_arm64.tar.gz" | tar -xz -C ~/bin/
chmod +x ~/bin/mcp-victorialogs
```

For other platforms, download the appropriate archive from the
[releases page](https://github.com/VictoriaMetrics/mcp-victorialogs/releases).

**Set the auth credentials:**

VictoriaLogs requires basic auth. The base64-encoded credentials are stored in SOPS
(`VICTORIALOGS_BASIC_AUTH_B64`). Get the value from a team member or decrypt from SOPS, then add
to your shell profile:

```bash
# macOS / Linux — add to ~/.zshrc or ~/.bashrc
export VICTORIALOGS_BASIC_AUTH_B64="<base64-encoded user:password>"
```

**Start the SSH tunnel:**

VictoriaLogs is only accessible on Docker's internal network on core-01. The tunnel forwards
the port to your local machine:

```bash
./scripts/victorialogs-tunnel.sh          # start (auto-reconnect, health check)
./scripts/victorialogs-tunnel.sh --check  # verify tunnel is up
./scripts/victorialogs-tunnel.sh --stop   # stop tunnel
```

The tunnel must be running before starting Claude Code (or before making log queries).

**Verify:**

```bash
curl -s -H "Authorization: Basic $VICTORIALOGS_BASIC_AUTH_B64" \
  "http://localhost:9428/select/logsql/query?query=_time:5m&limit=1"
```

For usage patterns and LogsQL queries, see `.claude/rules/klai/infra/observability.md`.

## 9. Grafana MCP (dashboards and metrics only)

Grafana MCP provides read-only access to dashboards, Prometheus/VictoriaMetrics queries, and
alerts. It **cannot query VictoriaLogs** — the `query_loki_logs` tool speaks Loki protocol,
not the VictoriaLogs API. Use the `victorialogs` MCP for log queries.

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
```

**Verify:**

```bash
uvx mcp-grafana --help
```

## Common failure modes

1. **Serena binary missing** — `which serena` returns nothing. Cause: uv cache eviction or never
   installed with `uv tool install`. Fix: `uv tool install git+https://github.com/oraios/serena`
2. **uvx in .mcp.json** — If `command` is `"uvx"` instead of `"serena"`, it clones and rebuilds
   on every startup → MCP timeout → Serena never available. Fix: use `"command": "serena"`.
3. **MCP timeout** — Serena takes too long to index. Check `.serena/project.yml` for overly broad
   file patterns.
4. **Playwright config missing** — config file not found. Fix: verify `.claude/playwright-config-win.json` or `.claude/playwright-config-mac.json` exists (both are committed to git). Check `--config` arg in `.mcp.json` points to the right platform file.
5. **Playwright browser runs as background process, no visible window** — Brave/Chrome is already running. Playwright cannot launch a second instance of the same browser with a different profile on Windows. Fix: use bundled `"browserName": "chromium"` (no `executablePath`).
6. **Playwright browser window not visible after login** — `userDataDir` path incorrect or doesn't exist yet. Fix: verify the path in the config file matches your username and that the parent directory (`~/.claude/`) exists.
9. **CodeIndex not found** — `codeindex` command not available. Fix: `npm install -g klai-private/tools/codeindex-1.3.56.tgz`
10. **CodeIndex stale index** — Index behind HEAD. Symptoms: impact analysis misses recent code. Fix: `codeindex update && node scripts/codeindex-enrich.mjs`
11. **VictoriaLogs tunnel not running** — MCP queries fail silently or timeout. Fix: `./scripts/victorialogs-tunnel.sh` then restart Claude Code.
12. **VictoriaLogs auth missing** — `VICTORIALOGS_BASIC_AUTH_B64` not set in `~/.zshrc`. Symptoms: MCP connects but queries return 401. Fix: get the base64 value from SOPS and export it.
13. **VictoriaLogs container IP changed** — Tunnel connects but queries fail. Cause: VictoriaLogs container restarted, got a new IP. Fix: `./scripts/victorialogs-tunnel.sh --stop && ./scripts/victorialogs-tunnel.sh` (re-resolves IP).
14. **Grafana token missing** — `GRAFANA_SERVICE_ACCOUNT_TOKEN` not set. Symptoms: Grafana MCP fails to connect. Fix: create a per-developer service account in Grafana (see section 9) and export the token in your shell profile.
