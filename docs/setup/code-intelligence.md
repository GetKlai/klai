# Code intelligence: Serena, zvec-grep, codebase-memory-mcp

> Three tools that answer "where is this, who calls it, what does this area do"
> without reading whole files. This page is about how they stay wired up.
> Installing them is `docs/setup/mcp-servers.md`; using them is the
> `codebase-memory` block in `AGENTS.md` and Serena's own project prompt in
> `.serena/project.yml`.

`rules/tests/test_code_intelligence_wiring.py` pins the claims below, including
the existence of this file. If you are deleting or rewriting this page, that
test tells you what else has to move with it.

## Why this page exists

This setup has been lost twice, both times silently. In March 2026 Serena's
`.mcp.json` entry was moved to a parent directory outside the repo, which then
stopped existing; a week later the rule file holding the restore instructions
was deleted in a token-efficiency pass. Nothing went red either time, because a
missing code index does not fail — the agent just quietly falls back to `grep`
and produces worse answers at a higher token cost.

So the rule for this area is: configuration lives in files that break when they
are wrong, and every claim on this page has a command that proves it.

## One command per worktree

```bash
make code-tools
```

Conductor runs this automatically on workspace creation, via `scripts.setup` in
`.conductor/settings.toml`. Run it by hand in a plain clone, after merging a
branch that moved a lot of code, or whenever a tool reports a missing or stale
index. It takes about 45 seconds on a cold worktree and is incremental after
that.

All three tools key their state off the working directory and none of that
state is committed, which is the whole reason a fresh Conductor worktree starts
with nothing:

| Tool | State | Committed? |
|---|---|---|
| zvec-grep | `.zvec-grep/` at the worktree root | no, gitignored |
| Serena | `.serena/cache` (config in `.serena/project.yml`) | cache no, config yes |
| codebase-memory-mcp | `~/.cache/codebase-memory-mcp` | outside the repo |

Note that Conductor reads `.conductor/settings.toml` from the **default branch
on the remote**. A change to the setup script only affects workspaces created
after it is merged to `main`.

## Serena — semantic navigation over MCP

Declared in `.mcp.json`, so every session in every worktree gets it with no
per-machine configuration. The flags are load-bearing and the `$comment` in
`.mcp.json` says why; the two that matter most:

- `--project .` binds Serena to the directory Claude Code spawns it in.
  `--project-from-cwd` guesses instead, and guesses wrong when Agent Teams
  spawn inside a worktree ([oraios/serena#1496]). That failure is invisible:
  Serena starts, advertises its tools, and answers about a different checkout.
- `--context claude-code` drops Serena's own read, search and shell tools, so
  it contributes only the symbolic ones on top of Claude Code's native
  `Read`/`Grep`/`Bash`.

`make code-tools` also runs `serena project index .`, which is a warm-up only:
the tools work without it, they are just slow on the first symbol lookup per
file.

## zvec-grep — semantic search over the workspace

The `zg` MCP server is declared once in your user config (`~/.claude.json`), not
per repo, so there is nothing to wire here. What a worktree needs is the index,
which `make code-tools` builds in about 25 seconds with a local embedding model
— no API key, no network.

`zg` resolves the "nearest workspace root" by walking **up** from the current
directory. A Conductor workspace is its own root and gets its own index. A
nested agent worktree under `.claude/worktrees/` finds the main checkout's index
first and shares it, so results there can lag the branch.

**The failure mode to recognise** is covered in its own section below, because
it is not really a zvec-grep problem: it is what happens to any shared daemon
when the directory it was started in gets archived.

## codebase-memory-mcp — the code graph, CLI only

Deliberately **not** an MCP server and not a file watcher; see the
`codebase-memory` block in `AGENTS.md` for how to query it. Two things about it
cost time repeatedly:

- **The project name is the worktree path as a slug**, not `klai`. Passing
  `klai` returns "project not found or not indexed". `make code-tools` prints
  the right value for the current worktree; the formula is
  `pwd -P | sed 's#^/##; s#/#-#g'`.
- **A warm daemon halves the cost of every call.** Without one, each CLI
  invocation starts a throwaway daemon: measured 3.9s cold against 1.35s warm
  for the same `get_architecture` call. `scripts/code-daemon-guard.sh` starts
  it, from `$HOME`, for the reason in the next section — it has the same
  inherited-cwd trap as `zg`, and on 2026-09-15 it was found rooted in a
  disposable `.claude/worktrees/` directory. This is the CLI's warm process,
  not the `auto_watch` file watcher, which stays off.

## The archived-workspace failure, and the invariant that prevents it

This is the one that has actually cost time, and it is worth stating precisely
because the symptom ("zvec-grep never has an index") points at the wrong tool.

`zg` and `codebase-memory-mcp` each run **one daemon per machine**, shared by
every repo and every session. Neither picks its own working directory; each
inherits the cwd of whichever process started it. For `zg` that starter is
normally `zg server --stdio`, the MCP bootstrap Claude Code spawns *inside the
workspace the session is running in*. So the machine-wide daemon ends up rooted
in one arbitrary Conductor workspace, and when that workspace is archived the
directory is deleted underneath it.

What makes it expensive is that nothing reports the problem:

- `zg server status --check-ready` still prints `ready` and **exits 0**.
- Every index write, for **every** workspace on the machine, fails with
  `ENOENT: process.cwd failed ... uv_cwd`.
- `--mode direct` cannot route around it, because the daemon still holds the
  write lease for the root: `ZVEC_GREP.ENGINE.DAEMON_LEASE_ACTIVE`.

There is no upstream cwd health check to switch on, so the invariant is
enforced here instead, and it is deliberately blunt:

> A shared code-intelligence daemon runs from `$HOME`. Nothing else may start
> one.

`scripts/code-daemon-guard.sh` enforces it. It restarts any daemon whose cwd is
missing or is not `$HOME`, and — the half that prevents rather than repairs —
starts a *missing* daemon from `$HOME`, so that a later `zg server --stdio`
finds a healthy daemon to reuse and never becomes the starter. It runs from
three places: `make code-tools`, Conductor's `setup`, and Conductor's
`archive`, that last one being the moment a daemon is about to be orphaned and
the last moment the directory still exists.

```bash
make code-doctor   # report only, exits 1 if the invariant is broken
make code-tools    # enforces it, along with building the indexes
```

Restarting either daemon is non-destructive: the zvec-grep index lives in
`.zvec-grep/` and the codebase-memory graph in `~/.cache/codebase-memory-mcp`,
both on disk.

## Running many sessions and many repos at once

Measured on 2026-09-15, because "one shared daemon" sounds like it should not
survive 38 worktrees and it mostly does.

| | Shared state | Port | Concurrency |
|---|---|---|---|
| Serena | none; one stdio subprocess per session, `.serena/cache` per worktree | none (`web_dashboard: false`) | unlimited |
| zvec-grep | one daemon, index per workspace root | `127.0.0.1:7999`, fixed | safe across roots; same root serialises on the write lease |
| codebase-memory | one daemon, one SQLite DB per path slug | `127.0.0.1:9749` (UI) | safe; concurrent indexing of the same and of different roots both succeeded |

So one machine serves Klai, Engram, herald, Garage, RakkenRakker,
relation-intelligence and workflowy at the same time, and nothing has to be
per-repo except building the per-worktree index. Two caveats worth knowing:

- **The zvec-grep port is not configurable through the bootstrap.** A second
  daemon fails with `EADDRINUSE` on 7999 even under a different
  `ZVEC_GREP_HOME`, and `ZVEC_GREP_SERVER_URL` does not move the bind address
  (only `zg server on --listen` does). One daemon is therefore not a tuning
  choice, it is the only option — which is exactly why its cwd is a
  machine-wide single point of failure and why the guard exists.
- **Nested agent worktrees under `.claude/worktrees/` share the parent's
  zvec-grep index**, because `zg` walks up to the nearest workspace root.
  Results there can lag the branch.

## Rolling this out to the other repos

The guard is machine-wide in effect but lives in this repo, so today Klai is
the only repo that repairs the daemons. For each of the other active repos the
per-repo work is the same three lines, and none of it is done yet:

| Repo | `.mcp.json` Serena entry | `code-tools` + guard | wiring test |
|---|---|---|---|
| Klai | yes | yes | yes |
| Engram, herald, Garage, RakkenRakker, relation-intelligence, workflowy | not checked | no | no |

Until that happens, `make code-doctor` from a Klai worktree is the machine-wide
check — the daemons it inspects are the same processes every other repo uses.

## Proving it works

```bash
make mcp-smoke
```

This performs a real MCP stdio handshake — `initialize`, then `tools/list` —
against every server declared in `.mcp.json`, and reads that list from the file
so a newly added server cannot escape the check. `tools/list` itself never
queries anything upstream, but it does start the real launcher, so Grafana still
needs `GRAFANA_SERVICE_ACCOUNT_TOKEN` and VictoriaLogs still opens its SSH
tunnel: for those two the credential is part of the wiring being checked.
Expected:

```text
PASS serena: handshake ok, 19 tools
PASS serena (default): bound to this worktree (.serena/project.yml prompt present)
PASS context7: handshake ok, 2 tools
PASS playwright: handshake ok, 26 tools
PASS grafana: handshake ok, 58 tools
PASS victorialogs: handshake ok, 13 tools

5/5 MCP servers passed.
```

The Serena line is the one worth understanding. A handshake alone proves almost
nothing — Serena pointed at the wrong directory still starts and still lists
tools. The second line calls `initial_instructions` and looks for this repo's
project prompt in the answer, which only arrives when `--project .` resolved to
this worktree's `.serena/project.yml`.

Add `--deep` to also prove the Grafana and VictoriaLogs credentials, and pass
server names to narrow the run:

```bash
node .claude/scripts/mcp-smoke.mjs --deep grafana victorialogs
```

## Does a new session pick the servers up automatically?

It depends on how the session starts, and it is worth being exact because
"committed to `.mcp.json`" does not by itself mean "automatic".

- **Conductor workspaces and any Agent SDK or `claude -p` session: yes, with no
  prompt.** Claude Code cannot show an approval prompt in those, so it loads
  project-scoped servers without asking.
- **A plain `claude` session in a terminal, in a directory you have not opened
  before: one trust prompt.** Approval is keyed by directory path in
  `~/.claude.json`, so each new worktree path asks once. A repository *cannot*
  approve its own servers — `enableAllProjectMcpServers` or
  `enabledMcpjsonServers` committed to `.claude/settings.json` is ignored in an
  untrusted folder, which is deliberate and is why this repo does not try it.
  To pre-approve across worktrees, put the setting in your own
  `~/.claude/settings.json`, and understand that it then applies to every
  repository you open, including ones you cloned from strangers.

Sources: [Claude Code MCP docs](https://code.claude.com/docs/en/mcp),
[settings reference](https://code.claude.com/docs/en/settings-reference).

[oraios/serena#1496]: https://github.com/oraios/serena/issues/1496
