# Klai Architecture Overview

## Workspace Layout
Monorepo lives at `/Users/mark/Server/projects/klai` (GetKlai/klai).
Current branch: `main`.

## Monorepo Structure

| Directory | Purpose | Language | Status |
|-----------|---------|----------|--------|
| `klai-portal/backend/` | Customer portal API | Python (FastAPI) | Active |
| `klai-portal/frontend/` | Customer portal UI | TypeScript (React/Vite) | Active |
| `klai-docs/` | Documentation site (per-tenant) | TypeScript (Next.js 15) | Active |
| `deploy/` | Self-hosting templates + microservices | Docker Compose / Python | Active |
| `.claude/` | Claude Code tooling — agents, rules, commands, skills | Markdown | Active |
| `docs/` | Project documentation — specs, architecture, runbooks, pitfalls | Markdown | Active |
| `klai-private/` | Private business docs — research, GTM, pricing | Markdown | Submodule |
| `klai-infra/` | Infrastructure secrets (SOPS) | Shell/YAML | Separate repo (gitignored) |
| `klai-scribe/` | Meeting transcription service | Python | Present |
| `klai-website/` | Marketing website | Astro/TypeScript | Separate repo (gitignored) |
| `klai-focus/` | Research services (research-api) | Python (FastAPI) | Active |
| `klai-retrieval-api/` | Hybrid retrieval service | Python (FastAPI) | Active |
| `scripts/` | Repo management utilities | Shell | Active |

## Knowledge Base Location
All patterns and pitfalls live in `.claude/rules/klai/`:
- `patterns/` — devops, infrastructure, platform, frontend, logging, code-quality, backend, testing
- `pitfalls/` — process-rules, git, devops, infrastructure, platform, backend, code-quality, docs-app
- Auto-loaded via `paths:` frontmatter when working on matching files

## Core Platform Stack
- **Auth:** Zitadel (self-hosted at auth.getklai.com)
- **AI chat:** LibreChat (per-tenant containers, provisioned by portal)
- **LLM routing:** LiteLLM (per-tenant team keys, EU-only models)
- **Knowledge base:** Qdrant (vector DB) + FalkorDB (graph)
- **Billing:** Moneybird (NL-based, EU-only)
- **Meetings:** Vexa (bot manager for Google Meet/Zoom/Teams)
- **Infra:** Hetzner servers, Caddy reverse proxy

## Deployment

| Server | Spec | Services |
|--------|------|----------|
| **core-01** | Hetzner EX44 dedicated | Full AI stack (30+ containers) |
| **public-01** | Hetzner CX42 | Website, Twenty CRM, Fider, Uptime Kuma |

SSH: `ssh core-01` (klai user), `ssh -i ~/.ssh/klai_ed25519 root@65.109.237.64` for public-01

## Repo Layout

| What | GitHub remote |
|------|---------------|
| **This monorepo** | `GetKlai/klai` |
| Infrastructure secrets | `GetKlai/klai-infra` (private, cloned at `klai-infra/`) |
| Website | `GetKlai/klai-website` (cloned at `klai-website/`) |
| Private docs | `GetKlai/klai-private` (git submodule at `klai-private/`) |
