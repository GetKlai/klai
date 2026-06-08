# Klai Architecture Overview

## Workspace Layout
This public monorepo is `GetKlai/klai`. Use your local checkout path and current
branch; do not encode workstation-specific paths in shared memory.

## Monorepo Structure

| Directory | Purpose | Language | Status |
|-----------|---------|----------|--------|
| `klai-portal/backend/` | Customer portal API | Python (FastAPI) | Active |
| `klai-portal/frontend/` | Customer portal UI | TypeScript (React/Vite) | Active |
| `klai-docs/` | Documentation site (per-tenant) | TypeScript (Next.js 15) | Active |
| `deploy/` | Public self-hosting templates + microservices | Docker Compose / Python | Active |
| `.claude/` | Claude Code tooling — agents, rules, commands, skills | Markdown | Active |
| `docs/` | Project documentation — specs, architecture, runbooks, pitfalls | Markdown | Active |
| `klai-private/` | Private business docs — research, GTM, pricing | Markdown | Private repo, ignored by Serena |
| `klai-infra/` | Private production infrastructure and SOPS secrets | Shell/YAML | Private repo, ignored by Serena |
| `klai-scribe/` | Meeting transcription service | Python | Present |
| `klai-website/` | Marketing website | Astro/TypeScript | Separate repo (gitignored) |
| `klai-focus/` | Research services (research-api) | Python (FastAPI) | Active |
| `klai-retrieval-api/` | Hybrid retrieval service | Python (FastAPI) | Active |
| `scripts/` | Repo management utilities | Shell | Active |

## Knowledge Base Location
Claude-specific patterns and pitfalls live in `.claude/rules/klai/`:
- `patterns/` — devops, infrastructure, platform, frontend, logging, code-quality, backend, testing
- `pitfalls/` — process-rules, git, devops, infrastructure, platform, backend, code-quality, docs-app
- Claude auto-loads these via `paths:` frontmatter when working on matching files.
  Codex does not; Codex reads `AGENTS.md` and only sees deeper rules if it opens
  them explicitly.

## Core Platform Stack
- **Auth:** Zitadel-compatible identity provider
- **AI chat:** LibreChat (per-tenant containers, provisioned by portal)
- **LLM routing:** LiteLLM (per-tenant team keys, EU-only models)
- **Knowledge base:** Qdrant (vector DB) + FalkorDB (graph)
- **Billing:** Moneybird (NL-based, EU-only)
- **Meetings:** Vexa (bot manager for Google Meet/Zoom/Teams)
- **Infra:** private production hosting plus public self-hosting templates

## Deployment

Public docs describe product services and self-hosting templates only. Klai
production server inventory, SSH access, host addresses, and operator procedures
live in the private `GetKlai/klai-infra` repository.

## Repo Layout

| What | GitHub remote |
|------|---------------|
| **This monorepo** | `GetKlai/klai` |
| Infrastructure operations | private repo, commonly cloned at `klai-infra/` |
| Website | separate repo, commonly cloned at `klai-website/` |
| Private docs | private repo, commonly cloned at `klai-private/` |
