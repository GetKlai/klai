# Klai — Monorepo

Open-source AI platform: self-hostable, multi-tenant, production-ready.

## Repository structure

| Directory | Contents |
|-----------|----------|
| `klai-portal/backend/` | FastAPI API — auth, tenant provisioning, knowledge base |
| `klai-portal/frontend/` | React 19 + Vite + TanStack Router — portal UI |
| `klai-docs/` | Next.js 15 documentation site |
| `deploy/` | Self-hosting templates — Docker Compose, Caddy, LiteLLM, Zitadel |
| `.claude/` | Shared Claude Code tooling — agents, rules (incl. patterns/pitfalls), commands, skills |
| `docs/` | Project documentation — architecture, research, runbooks, specs, GTM |
| `klai-private/` | Private business docs — research, GTM, pricing (team only, git submodule) |
| `scripts/` | Repo management utilities |

## Package instructions

Each package has its own CLAUDE.md that loads automatically when you work in that directory:

- [klai-portal/CLAUDE.md](klai-portal/CLAUDE.md) — FastAPI backend + React/Vite frontend

## Knowledge base

Domain patterns and pitfalls live in `.claude/rules/klai/` and load automatically
when you work on matching files (via `paths:` frontmatter). Universal rules
(`process-rules.md`, `git.md`) load every session.

Full index: `.claude/rules/klai/patterns.md` and `.claude/rules/klai/pitfalls.md`

## Shared rules

@.claude/rules/klai/knowledge.md
@.claude/rules/klai/server-secrets.md
@.claude/rules/klai/serena.md

## Model policy

**[HARD] Never use OpenAI, Anthropic, or other US cloud provider model names anywhere in Klai code.**

Klai is a privacy-first, EU-only platform. Using OpenAI/Anthropic model names in code would route data to US providers, violate GDPR, and break the product promise.

**Forbidden:** `gpt-*`, `claude-*`, `text-davinci-*`, `text-embedding-*`, or any model from openai.com, anthropic.com, cohere.com.

**Use ONLY these LiteLLM tier aliases:**

| Alias | Use for |
|---|---|
| `klai-fast` | Lightweight, high-volume, latency-sensitive |
| `klai-primary` | Standard quality, user-facing |
| `klai-large` | Agentic, tool use, MCP flows |

For tier details and model mappings, see `patterns/platform.md`.

## Tech stack

**Portal backend:** Python 3.12, FastAPI, SQLAlchemy (async), Alembic, PostgreSQL, uv
**Portal frontend:** React 19, Vite, TypeScript 5.9, TanStack Router, TanStack Query, Mantine 8, Paraglide i18n, Tailwind 4
**Docs:** Next.js 15, React 19, PostgreSQL, TypeScript
**Deploy stack:** Docker Compose, Caddy (wildcard TLS), Zitadel (OIDC/auth), LiteLLM, LibreChat, PostgreSQL, MongoDB, Redis, Meilisearch, VictoriaMetrics
