# Klai — Agent Instructions

## Brand (HARD rule)

For ANY user-facing image reference (email, embed, social card, third-party config), use ONLY the canonical logo URLs listed in `.claude/rules/klai/design/tokens.md` § "Logo — canonical sources". The current Klai wordmark lives at `https://getklai.com/logo-black.svg`. NEVER use `klai-icon-square.png`, `klay-icon.png`, or any `cdn.getklai.com/klai-logo.png` URL — those are the OLD "ai" branding or 404s. Always `curl -sI <url>` and confirm `content-type: image/*` before shipping.

## Model & Review Standard (global)

Subagent model routing follows the global standard in `~/.claude/CLAUDE.md` (model-standard section): haiku for mechanical/read-only fan-out, sonnet as the default worker, opus only on literal triggers (subtle multi-file bugs, architecture, security-sensitive code, or a double failure on the same task). Pin models explicitly; never use `inherit` for teammates.

Code review follows the global two-pass Sol contract: GPT-5.6 Sol does the recall pass (`codex --profile review`; `review-deep` for security/migrations), Claude verifies and filters the findings.

## Environment Contract

Local dev is Makefile-driven — run `make help` for the full list. Key targets:

- `make setup` — first-time setup (env files, keys, deps)
- `make dev-up` — start Docker services (Postgres, Redis, Mongo, Meilisearch, LiteLLM)
- `make migrate` — run Alembic migrations
- `make seed` — seed demo data
- `make backend` / `make frontend` — run the API (port 8010) / Vite dev server (port 5174)
- `make lint` — ruff + eslint
- `make check` — pyright + tsc

`make lint` MUST pass before a session can be reported done — it is the Stop-gate.

<!-- codeindex:start -->
# CodeIndex MCP

This project is indexed by CodeIndex as **klai** (0 symbols, 0 relationships, 0 execution flows).

## Rules (MUST follow)

- **Before ANY code modification**: call `impact` on the symbol(s) you will change
- **Before searching code**: try `query` first — only use Grep/Glob if CodeIndex returns nothing useful
- **"How does X work?" questions**: use `query` or `context` — do NOT start with Grep
- **Skip CodeIndex only for**: non-code conversations, config edits, or single known-file changes

## Always Start Here

1. **Read `codeindex://repo/{name}/context`** — codebase overview + check index freshness
2. **Match your task to a skill below** and **read that skill file**
3. **Follow the skill's workflow and checklist**

> If step 1 warns the index is stale, treat CodeIndex results as advisory and verify with source files. Do not block the task on re-indexing unless the user explicitly asked to refresh the index.

## Skills

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/codeindex/codeindex-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/codeindex/codeindex-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/codeindex/codeindex-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/codeindex/codeindex-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/codeindex/codeindex-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/codeindex/codeindex-cli/SKILL.md` |

## If CodeIndex tools appear missing

If you don't see CodeIndex tools in your active toolset, they are almost certainly **deferred/lazy-loaded** by the agent harness — **NOT disconnected**. The MCP server is fine.

Use your harness's tool-discovery mechanism once, then continue with the loaded CodeIndex MCP tools:

- **Claude Code**: call `ToolSearch` with `select:mcp__codeindex__query,mcp__codeindex__context,mcp__codeindex__impact,mcp__codeindex__detect_changes,mcp__codeindex__rename,mcp__codeindex__cypher,mcp__codeindex__remember,mcp__codeindex__recall,mcp__codeindex__forget`
- **Codex / Conductor**: call `tool_search` with the same `select:mcp__codeindex__...` query above

After that the tools are directly callable, usually as `mcp__codeindex__query` / `mcp__codeindex__.query` or plain `query`, depending on the harness. If a `list_repos` tool is not exposed, read the `codeindex://repos` resource instead.

Do **NOT** run `npx codeindex`, `codeindex analyze`, or `codeindex update` as a workaround for "missing MCP" or a stale index. The CLI is for explicit setup/maintenance requests. If the index is stale or an update is already running, use CodeIndex results as advisory and verify against source files.

<!-- codeindex:end -->
