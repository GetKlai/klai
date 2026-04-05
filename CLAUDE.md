# Klai — Monorepo

Open-source AI platform: self-hostable, multi-tenant, production-ready.

## Repository structure

| Directory | Contents |
|-----------|----------|
| `klai-portal/backend/` | FastAPI API — auth, tenant provisioning, knowledge base |
| `klai-portal/frontend/` | React 19 + Vite + TanStack Router — portal UI |
| `klai-docs/` | Next.js 15 documentation site |
| `deploy/` | Docker Compose, Caddy, LiteLLM, Zitadel |
| `docs/` | Architecture, research, runbooks, specs |

## Model policy

**[HARD] Never use OpenAI, Anthropic, or other US cloud provider model names anywhere in Klai code.**

Klai is a privacy-first, EU-only platform. Forbidden: `gpt-*`, `claude-*`, `text-davinci-*`, `text-embedding-*`.

**Use ONLY these LiteLLM tier aliases:**

| Alias | Use for |
|---|---|
| `klai-fast` | Lightweight, high-volume, latency-sensitive |
| `klai-primary` | Standard quality, user-facing |
| `klai-large` | Agentic, tool use, MCP flows |

## Tech stack

**Backend:** Python 3.12, FastAPI, SQLAlchemy (async), Alembic, PostgreSQL, uv
**Frontend:** React 19, Vite, TypeScript 5.9, TanStack Router, TanStack Query, Mantine 8, Paraglide i18n, Tailwind 4
**Docs:** Next.js 15, React 19, TypeScript
**Deploy:** Docker Compose, Caddy, Zitadel (OIDC), LiteLLM, LibreChat, PostgreSQL, MongoDB, Redis, Meilisearch

## Serena

Serena auto-activates via `--project-from-cwd` — no `activate_project` needed.

**[HARD] Parameter names — wrong names fail silently:**

| Tool | Correct parameter | NOT |
|---|---|---|
| `find_symbol` | `name_path_pattern` | ~~name_path~~, ~~name~~ |
| `search_for_pattern` | `substring_pattern` | ~~pattern~~, ~~search~~ |
| `find_referencing_symbols` | `name_path_pattern` | ~~name_path~~, ~~symbol~~ |
| `replace_symbol_body` | `name_path_pattern` | ~~name_path~~ |

**[HARD] Always scope `search_for_pattern`** with `relative_path`, `paths_include_glob`, or `restrict_search_to_code_files`. Unscoped searches return truncated garbage.

**Before editing code:** use `find_referencing_symbols` to check callers. Prefer `replace_symbol_body` over Edit for whole functions.

**Subagents** in `.claude/agents/` can access Serena tools by omitting the `tools` field (inherits all) or adding `mcpServers: [serena]` to frontmatter. Plugin subagents cannot — gather context first for those.

**Memories** — read relevant ones at session start (not all):
`architecture-overview`, `backend-patterns`, `domain-model`, `frontend-standards`, `services-overview`, `deployment-context`

## Agent output policy

Never write prompts or audit templates to .md files. Output in chat directly.

## External libraries

Use context7 MCP for current library/framework docs — not for internal code or business logic. During SPEC plan phase with external dependencies, run context7 during the research sub-phase.

## CodeIndex

Graph-powered code intelligence (call graphs, communities, execution flows). Enriched with git hotspots, SPEC links, test mapping, and PageRank.

**When to use CodeIndex vs Serena:**

| Question | Tool | Why |
|---|---|---|
| "What's in this file?" | Serena `get_symbols_overview` | Real-time, always up-to-date |
| "Where is X defined?" | Serena `find_symbol` | Type-accurate LSP resolution |
| "Who calls X?" (direct) | Serena `find_referencing_symbols` | Precise, 1-hop |
| "What's the blast radius?" | CodeIndex `impact` | Full call chain traversal |
| "How does auth work?" | CodeIndex `query` | Semantic search + process flows |
| "What breaks if I change X?" | CodeIndex `impact` + `context` | Upstream/downstream analysis |
| "Is this code tested?" | CodeIndex cypher `_tested_by` | Enrichment data |
| "How often does this change?" | CodeIndex cypher `git_hotspot` | Enrichment data |
| "Which SPEC does this implement?" | CodeIndex cypher `_specs` | Enrichment data |
| Replace a function body | Serena `replace_symbol_body` | Type-safe editing |

**Serena is primary for editing. CodeIndex is primary for understanding.**

**After code changes:** run `codeindex update` then `node scripts/codeindex-enrich.mjs` to refresh.

**Memory:** Serena memories are primary for long-term project knowledge. CodeIndex memory is available for short-lived observations.

<!-- codeindex:start -->
# CodeIndex MCP

This project is indexed by CodeIndex as **klai** (8122 symbols, 19567 relationships, 300 execution flows).

## Rules (MUST follow)

- **Before ANY code modification**: call `impact` on the symbol(s) you will change
- **Before searching code**: try `query` first — only use Grep/Glob if CodeIndex returns nothing useful
- **"How does X work?" questions**: use `query` or `context` — do NOT start with Grep
- **Skip CodeIndex only for**: non-code conversations, config edits, or single known-file changes

## Always Start Here

1. **Read `codeindex://repo/{name}/context`** — codebase overview + check index freshness
2. **Match your task to a skill below** and **read that skill file**
3. **Follow the skill's workflow and checklist**

> If step 1 warns the index is stale, run `codeindex update` in the terminal first.

## Skills

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/codeindex/codeindex-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/codeindex/codeindex-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/codeindex/codeindex-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/codeindex/codeindex-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/codeindex/codeindex-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/codeindex/codeindex-cli/SKILL.md` |

<!-- codeindex:end -->
