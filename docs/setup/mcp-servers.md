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
      "command": "node",
      "args": [".claude/scripts/playwright-launcher.mjs"],
      "env": {}
    },
    "playwright-isolated": {
      "type": "stdio",
      "command": "npx",
      "args": ["@playwright/mcp@0.0.70", "--browser", "chromium", "--isolated"],
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
| **playwright** | Browser automation for E2E spot-checks and visual verification. Headed Chromium with shared login state via `--storage-state` — every Claude Code session preloads the same auth, runs in its own ephemeral profile, parallel-safe. |
| **playwright-isolated** | Headed ephemeral Chromium with NO storage state. For CSS work or unauthenticated checks where preloaded login would just be noise. |
| **codeindex** | Graph-powered code intelligence — call graphs, impact analysis, semantic search, communities, and enrichment queries (git hotspots, SPEC links, test coverage, PageRank). |
| **grafana** | Read-only access to Grafana dashboards, Prometheus/VictoriaMetrics queries, and alerts. Cannot query VictoriaLogs — use the `victorialogs` MCP for log queries instead. |
| **victorialogs** | Production log queries via LogsQL against VictoriaLogs. Requires SSH tunnel (`./scripts/victorialogs-tunnel.sh`) and `VICTORIALOGS_BASIC_AUTH_B64` env var. Preferred over `docker logs` for investigating issues. |

## 3. Set up Playwright

No per-machine setup beyond a one-time login. The `playwright` server in `.mcp.json`
invokes `.claude/scripts/playwright-launcher.mjs` (committed, cross-platform). The launcher
spawns `@playwright/mcp@latest` with `--browser chrome --isolated --storage-state
~/.claude/mcp-storageState.json`. Parallel-safe (every session gets its own ephemeral
profile) and all sessions start authenticated by preloading the same storage-state file.

### Use case this is built for

AI-driven coding sessions where the assistant validates its own changes end-to-end via
Playwright MCP. You log in once; the AI takes over from there. Multiple coding sessions
can run in parallel — each gets a visible browser window with the same login already
loaded.

### Why `--isolated` + `--storage-state` (and not `--user-data-dir`)

A persistent `--user-data-dir` is single-instance-locked: a second concurrent
Claude Code session that needs the same profile fails with `Browser is already
in use`. The `--isolated --storage-state` pattern dodges that lock — every
session gets its own ephemeral profile, all preloaded with the same login
state at startup. This is Microsoft's recommended path for parallel + login
without the browser extension (Issue #1530, the alternative "named session
management" feature, was closed by Microsoft as `not planned`).

Storage state is read-only at startup. The AI does not log out, change
passwords, or otherwise mutate auth, so read-only is fine. Refresh the file
when Google's session cookies expire (~3 weeks, see seed flow below).

The reason this pattern looked broken for weeks in April 2026: PR #354 used
`npx playwright codegen --save-storage` as the seed step. On macOS that save
only fires on a specific Inspector-side close event; if you closed the
browser the wrong way, the file never appeared. Solved May 2026 by switching
the seed to the in-session `browser_storage_state` MCP tool (available in
`@playwright/mcp >= 0.0.67`), which writes the file directly from the running
MCP server — no external tooling, no Inspector window, no Ctrl+C dance.

### Why a launcher script and not a JSON `--config` file

[microsoft/playwright-mcp#1446](https://github.com/microsoft/playwright-mcp/issues/1446):
on `@playwright/mcp@0.0.70`, profile-related options set inside a JSON `--config` file
can be silently ignored. CLI flags work correctly. The launcher is the cross-platform
vehicle for those flags.

### One-time login (and refresh)

1. With the launcher in place but no storage-state file yet (or after deleting
   it for a refresh), restart Claude Code so the MCP server picks up the new
   config. Open a new Playwright MCP session — the browser starts logged-out.
2. Have the AI `browser_navigate` to a login URL, e.g. `https://voys.getklai.com`.
3. Log in by hand (Google SSO + 2FA), wait until you're on the chat home.
4. Have the AI call the MCP tool `browser_storage_state` — it writes the
   current cookies + localStorage to `~/.claude/mcp-storageState.json`.
5. Restart Claude Code so the launcher picks the file up via `--storage-state`.
6. From now on all MCP sessions, including parallel Claude Code instances,
   start authenticated.

Refresh the same way every ~3 weeks when Google's session cookies expire.

For the full failure-mode cycle and anti-patterns to avoid, see
`playwright-mcp-config-cycle (HIGH)` in
`.claude/rules/klai/pitfalls/process-rules.md` (8 anti-patterns and many
"fixes" since April 2026 — this section is the May 2026 canonical, anchored
on Microsoft Issue #1530's outcome).

### Starting from scratch (logged-out state)

Delete the storage-state file, then re-run the codegen command above:

```powershell
# Windows (PowerShell)
Remove-Item -Force "$env:USERPROFILE\.claude\mcp-storageState.json"
```

```bash
# macOS / Linux
rm -f ~/.claude/mcp-storageState.json
```

Then re-run the codegen command above to regenerate the file.

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

## 9. Install Agent Browser (CLI, not MCP)

Agent Browser is a Rust-based browser CLI by vercel-labs. **Not an MCP server** — agents call
it directly via Bash. Used alongside (not instead of) Playwright MCP. Routing rules and
parallel-session patterns: `.claude/rules/klai/lang/agent-browser.md`.

```bash
npm install -g agent-browser   # installs CLI globally
agent-browser install          # downloads bundled Chrome (~170MB, one-time)
```

Verify: `npx agent-browser --version`.

For authenticated portal flows, generate a state file once:

```bash
agent-browser open https://app.getklai.com   # log in manually in opened window
agent-browser state save ~/.claude/agent-browser-state.json
chmod 600 ~/.claude/agent-browser-state.json
agent-browser close
```

Refresh when sessions start logged-out (cookie expiry, ~weeks).

**When to use which browser tool:**

| You know exactly what to verify? | Tool |
|---|---|
| Yes — bekende selectors, regression test | `playwright` MCP |
| No — exploratory, smoke check, a11y/copy audit | Agent Browser CLI |
| One-off CSS check, no auth needed | `playwright-isolated` MCP |

## 10. Grafana MCP (dashboards and metrics only)

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
4. **Playwright launcher fails to start** — `node` not on PATH or the launcher script missing. Fix: verify `node --version` works in your shell and `.claude/scripts/playwright-launcher.mjs` exists. Restart Claude Code.
5. **Playwright sessions start logged-out** — `~/.claude/mcp-storageState.json` is missing or its cookies have expired. Fix: re-run the codegen command in Section 3 to regenerate the file, then restart Claude Code.
6. **Playwright fails with `Browser is already in use`** — should not happen with `--isolated`. If it does, confirm `.mcp.json` still uses `--isolated --storage-state` and not a `--user-data-dir` flag. A leftover Chromium process can be killed with `taskkill /F /IM chrome.exe` (Windows) or `pkill -f playwright` (Mac/Linux).
7. **Playwright window opens but immediately closes** — storage-state file is corrupt or in a non-Playwright format. Fix: delete `~/.claude/mcp-storageState.json` and re-run the codegen command.
8. **Login state visible in one site but missing in another** — codegen capture only saved the sites you visited during the login dance. Fix: re-run codegen and visit + log in to every site you need before closing the window.
9. **CodeIndex not found** — `codeindex` command not available. Fix: `npm install -g klai-private/tools/codeindex-1.3.56.tgz`
10. **CodeIndex stale index** — Index behind HEAD. Symptoms: impact analysis misses recent code. Fix: `codeindex update && node scripts/codeindex-enrich.mjs`
11. **VictoriaLogs tunnel not running** — MCP queries fail silently or timeout. Fix: `./scripts/victorialogs-tunnel.sh` then restart Claude Code.
12. **VictoriaLogs auth missing** — `VICTORIALOGS_BASIC_AUTH_B64` not set in `~/.zshrc`. Symptoms: MCP connects but queries return 401. Fix: get the base64 value from SOPS and export it.
13. **VictoriaLogs container IP changed** — Tunnel connects but queries fail. Cause: VictoriaLogs container restarted, got a new IP. Fix: `./scripts/victorialogs-tunnel.sh --stop && ./scripts/victorialogs-tunnel.sh` (re-resolves IP).
14. **Grafana token missing** — `GRAFANA_SERVICE_ACCOUNT_TOKEN` not set. Symptoms: Grafana MCP fails to connect. Fix: create a per-developer service account in Grafana (see section 10) and export the token in your shell profile.
