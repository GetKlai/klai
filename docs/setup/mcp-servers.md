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
    "codeindex": {
      "type": "stdio",
      "command": "bash",
      "args": [".claude/scripts/codeindex-mcp-launcher.sh"],
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
| **playwright** | Browser automation for E2E spot-checks and visual verification. Headed Chrome with a per-workspace **persistent profile** (login state survives Claude Code restarts). Parallel-safe across workspaces via `{workspace-hash}` profile naming. Set `PLAYWRIGHT_ISOLATED=1` to opt into an ephemeral, logged-out profile. See Section 3. |
| **codeindex** | Graph-powered code intelligence — call graphs, impact analysis, semantic search, communities, and enrichment queries (git hotspots, SPEC links, test coverage, PageRank). |
| **grafana** | Read-only access to Grafana dashboards, Prometheus/VictoriaMetrics queries, and alerts. Cannot query VictoriaLogs — use the `victorialogs` MCP for log queries instead. |
| **victorialogs** | Production log queries via LogsQL against VictoriaLogs. Requires SSH tunnel (`./scripts/victorialogs-tunnel.sh`) and `VICTORIALOGS_BASIC_AUTH_B64` env var. Preferred over `docker logs` for investigating issues. |

### CodeIndex stale-index prevention

The CodeIndex MCP server starts through `.claude/scripts/codeindex-mcp-launcher.sh`
instead of calling `codeindex mcp` directly. The launcher runs
`scripts/codeindex-health.sh --repair --quiet` before handing stdout to MCP.
This prevents the recurring failure mode where an old `codeindex serve` process
holds the local database open and every Conductor workspace starts reporting a
stale index.

Important details:

- CodeIndex stores one canonical repo path for `klai`, currently
  `/Users/mvletter/Developer/Klai`. Conductor worktrees may be on different
  branches, but the stale check compares against that canonical path.
- The launcher writes preflight output to
  `.context/codeindex-mcp-launcher.log`, not stdout, because MCP uses stdout for
  JSON-RPC.
- The launcher does not kill existing `codeindex mcp` processes. That would be
  unsafe while starting an MCP server.

Manual recovery, if an already-running agent still sees stale CodeIndex context:

```bash
scripts/codeindex-health.sh --repair --restart-mcp
```

For diagnosis only:

```bash
scripts/codeindex-health.sh
```

## 3. Set up Playwright

No per-machine setup beyond a one-time login per workspace. The `playwright`
server in `.mcp.json` invokes `.claude/scripts/playwright-launcher.mjs`
(committed, cross-platform). The launcher spawns `@playwright/mcp@latest`
with `--browser chrome`. By default it uses Playwright MCP's persistent
profile, which is `{workspace-hash}`-keyed and therefore parallel-safe
across distinct workspaces.

### Use case this is built for

AI-driven coding sessions where the assistant validates its own changes
end-to-end via Playwright MCP. You log in once per workspace; the AI takes
over from there. Multiple Conductor workspaces can run Playwright in
parallel — each gets its own persistent Chrome profile (different
workspace root → different profile dir → no lock).

### How parallel + persistent coexist

`@playwright/mcp@latest` stores its persistent profile at:

- **macOS:** `~/Library/Caches/ms-playwright/mcp-{channel}-{workspace-hash}`
- **Linux:** `~/.cache/ms-playwright/mcp-{channel}-{workspace-hash}`
- **Windows:** `%LOCALAPPDATA%\ms-playwright\mcp-{channel}-{workspace-hash}`

The `{workspace-hash}` is derived from the MCP client's workspace root.
Different Conductor workspaces (`/Users/<you>/conductor/workspaces/Klai/lyon`,
`.../moscow`, `.../nairobi`, …) all hash to different directories, so each
runs its own Chrome instance against its own profile. No conflict.

**The only collision case:** two Claude Code instances inside the **same**
workspace touching Playwright simultaneously. That's what
`PLAYWRIGHT_ISOLATED=1` is for — set it on the second instance and it gets
an ephemeral profile instead.

(Verified 2026-05-13 against Microsoft's
[Profile & State docs](https://playwright.dev/mcp/configuration/user-profile)
and [issue #1294](https://github.com/microsoft/playwright-mcp/issues/1294).)

### When to use `PLAYWRIGHT_ISOLATED=1`

Opt-in fallback for when persistent + logged-in is **wrong**:

- Testing the login flow itself (Google SSO, password reset, MFA setup).
- Verifying unauthenticated routes / public marketing pages.
- Disposable runs where you don't want to mutate the workspace profile.
- A second Claude Code instance inside the same workspace.

Set the env var in your shell or `.mcp.json` env block for the session
that needs it. The launcher reads `process.env.PLAYWRIGHT_ISOLATED` at
spawn time.

### Storage-state seed (optional first-boot preload)

The launcher also passes `--storage-state ~/.claude/mcp-storageState.json`
when that file exists. With the persistent default this is mostly
**redundant** — the profile auto-saves login on first hand-login and keeps
it forever. The seed file is still useful for:

- Bootstrapping a brand-new workspace with cookies you already have.
- Isolated sessions (`PLAYWRIGHT_ISOLATED=1`) that need to start
  authenticated.

To (re)seed once, ask Claude something like *"navigate naar
voys.getklai.com en seed de storage-state na mijn login"*. Claude opens
the login page, you log in by hand, then Claude calls `browser_run_code_unsafe`:

```js
async (page) => {
  await page.context().storageState({
    path: '/Users/<you>/.claude/mcp-storageState.json'
  });
  const cookies = await page.context().cookies();
  return {
    url: page.url(),
    cookieCount: cookies.length,
    klaiCookies: cookies.filter(c => c.domain.includes('getklai')).length,
  };
}
```

Sanity-check the return: `klaiCookies` should be ≥ 6 (PARAGLIDE_LOCALE × 2,
zitadel.useragent, klai_sso, __Secure-klai_session, __Secure-klai_csrf).

**Note:** there is no separate `browser_storage_state` MCP tool — that was
a hallucination in an earlier doc draft. The functionality lives in the
generic `browser_run_code_unsafe` tool.

You do **not** need to refresh the seed file every ~3 weeks anymore. That
refresh cadence was a workaround for the old `--isolated` pattern where
the ephemeral profile dropped cookies every session. The persistent
profile keeps Google's session alive on its own.

### Why a launcher script and not a JSON `--config` file

[microsoft/playwright-mcp#1446](https://github.com/microsoft/playwright-mcp/issues/1446):
on `@playwright/mcp@0.0.70`, profile-related options set inside a JSON `--config` file
can be silently ignored. CLI flags work correctly. The launcher is the cross-platform
vehicle for those flags.

### One-time login (per workspace)

1. Start Claude Code in the workspace. The Playwright MCP server boots
   with a fresh persistent profile (no login yet).
2. Ask Claude to `browser_navigate` to a login URL (e.g.
   `https://voys.getklai.com`).
3. Log in by hand in the headed Chrome window (Google SSO + 2FA).
4. Done — the workspace profile now holds your login. Close the browser
   when you're finished; the cookies persist for the next session.

Optionally (and only once on this machine, if you want isolated sessions
to also start authenticated), have Claude write the storage-state file
via `browser_run_code_unsafe` as shown above.

### Starting from scratch (logged-out state)

To force a workspace back to logged-out, delete its profile directory:

```bash
# macOS — workspace-hash is opaque; clear the lot if you're not sure which:
rm -rf ~/Library/Caches/ms-playwright/mcp-chrome-*
```

```powershell
# Windows (PowerShell)
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\ms-playwright\mcp-chrome-*"
```

```bash
# Linux
rm -rf ~/.cache/ms-playwright/mcp-chrome-*
```

If you also want to drop the storage-state seed:

```bash
# macOS / Linux
rm -f ~/.claude/mcp-storageState.json
```

```powershell
# Windows (PowerShell)
Remove-Item -Force "$env:USERPROFILE\.claude\mcp-storageState.json"
```

Restart Claude Code afterwards so the launcher spawns a fresh MCP server.

For session management rules (when to open/close the browser, don't click
Log out in a persistent profile, etc.), see
`.claude/rules/klai/lang/testing.md`.

For the full failure-mode history, see `playwright-mcp-config-cycle` in
`.claude/rules/klai/pitfalls/process-rules.md` — anchored on the
2026-05-13 confirmation that `{workspace-hash}` makes persistent profiles
parallel-safe.

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
| One-off CSS check, no auth needed | `playwright` MCP — open a new tab via `browser_tabs(action: "new")` |

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
5. **Playwright sessions start logged-out in a workspace** — the workspace's persistent profile was never logged in (new workspace, or you deleted the profile dir). Fix: `browser_navigate` to a login URL and log in by hand once; the profile remembers it. Optional: seed the shared `~/.claude/mcp-storageState.json` via the snippet in Section 3 so future new workspaces start authenticated.
6. **Playwright fails with `Browser is already in use`** — two MCP clients inside the same workspace are trying to open the same persistent profile. Fix: set `PLAYWRIGHT_ISOLATED=1` on the second instance (ephemeral profile, no lock). A leftover Chromium process from a previous crash can be killed with `taskkill /F /IM chrome.exe` (Windows) or `pkill -f playwright` (Mac/Linux).
7. **Playwright window opens but immediately closes** — corrupt profile directory or corrupt storage-state file. Fix: nuke the workspace's profile (`rm -rf ~/Library/Caches/ms-playwright/mcp-chrome-*` on macOS — see Section 3 "Starting from scratch") and, if used, the storage-state file. Restart Claude Code.
8. **Login state visible in one site but missing in another** — only applies to the storage-state seed: it captured cookies from the sites you visited before calling `browser_run_code_unsafe`. With the persistent default this is rarely the right diagnosis; just log into the missing site once and the workspace profile keeps it. If you DO maintain a storage-state seed, re-seed after visiting + logging into every site you need.
9. **CodeIndex not found** — `codeindex` command not available. Fix: `npm install -g klai-private/tools/codeindex-1.3.56.tgz`
10. **CodeIndex stale index** — Index behind HEAD. Symptoms: impact analysis misses recent code. Fix: `codeindex update && node scripts/codeindex-enrich.mjs`
11. **VictoriaLogs tunnel not running** — MCP queries fail silently or timeout. Fix: `./scripts/victorialogs-tunnel.sh` then restart Claude Code.
12. **VictoriaLogs auth missing** — `VICTORIALOGS_BASIC_AUTH_B64` not set in `~/.zshrc`. Symptoms: MCP connects but queries return 401. Fix: get the base64 value from SOPS and export it.
13. **VictoriaLogs container IP changed** — Tunnel connects but queries fail. Cause: VictoriaLogs container restarted, got a new IP. Fix: `./scripts/victorialogs-tunnel.sh --stop && ./scripts/victorialogs-tunnel.sh` (re-resolves IP).
14. **Grafana token missing** — `GRAFANA_SERVICE_ACCOUNT_TOKEN` not set. Symptoms: Grafana MCP fails to connect. Fix: create a per-developer service account in Grafana (see section 10) and export the token in your shell profile.
