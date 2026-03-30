# Klai — Monorepo

Open-source AI platform: self-hostable, multi-tenant, production-ready.

## Repository structure

| Directory | Contents |
|-----------|----------|
| `klai-portal/backend/` | FastAPI API — auth, tenant provisioning, knowledge base |
| `klai-portal/frontend/` | React 19 + Vite + TanStack Router — portal UI |
| `klai-docs/` | Next.js 15 documentation site |
| `deploy/` | Self-hosting templates — Docker Compose, Caddy, LiteLLM, Zitadel |
| `.claude/` | Shared Claude Code tooling — agents, rules, commands, skills |
| `claude-docs/` | Klai knowledge base — patterns, pitfalls, styleguide |
| `klai-private/` | Private business docs — research, GTM, pricing (team only, git submodule) |
| `scripts/` | Repo management utilities |

## Package instructions

Before working on a specific package, read its CLAUDE.md:

@klai-portal/CLAUDE.md
@klai-docs/CLAUDE.md

## Knowledge base

Before making changes, read the relevant domain docs in `claude-docs/`:

@claude-docs/patterns/frontend.md
@claude-docs/patterns/platform.md
@claude-docs/patterns/devops.md
@claude-docs/pitfalls/process.md

Full index: `claude-docs/patterns.md` and `claude-docs/pitfalls.md`

## Shared rules

@.claude/rules/klai/context7-usage.md
@.claude/rules/klai/knowledge.md
@.claude/rules/klai/server-secrets.md
@.claude/rules/klai/serena-workflow.md

## Tech stack

**Portal backend:** Python 3.12, FastAPI, SQLAlchemy (async), Alembic, PostgreSQL, uv
**Portal frontend:** React 19, Vite, TypeScript 5.9, TanStack Router, TanStack Query, Mantine 8, Paraglide i18n, Tailwind 4
**Docs:** Next.js 15, React 19, PostgreSQL, TypeScript
**Deploy stack:** Docker Compose, Caddy (wildcard TLS), Zitadel (OIDC/auth), LiteLLM, LibreChat, PostgreSQL, MongoDB, Redis, Meilisearch, VictoriaMetrics
