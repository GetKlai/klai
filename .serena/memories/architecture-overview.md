# Klai Architecture Overview

## Workspace Layout
Monorepo lives at `/Users/mark/Server/projects/klai` (GetKlai/klai).
Current branch: `main`.

## Monorepo Structure

| Directory | Purpose | Language | Status |
|-----------|---------|----------|--------|
| `portal/backend/` | Customer portal API | Python (FastAPI) | Active |
| `portal/frontend/` | Customer portal UI | TypeScript (React/Vite) | Active |
| `docs/` | Documentation site | TypeScript (Next.js 15) | Active |
| `deploy/` | Self-hosting templates | Docker Compose / YAML | Active |
| `.claude/` | Shared Claude Code tooling — agents, rules, commands, skills | Markdown | Active |
| `claude-docs/` | Living knowledge base — patterns, pitfalls, styleguide | Markdown | Active |
| `klai-private/` | Private business docs — research, GTM, pricing | Markdown | Submodule |
| `klai-infra/` | Infrastructure secrets (SOPS) | Shell/YAML | Submodule (private) |
| `klai-portal/` | Portal repo (legacy name, now `portal/`) | — | Present |
| `klai-scribe/` | Meeting transcription service | Python | Present |
| `klai-website/` | Marketing website | Astro/TypeScript | Present |
| `scripts/` | Repo management utilities | Shell | Active |

## Core Platform Stack
- **Auth:** Zitadel (self-hosted at auth.getklai.com)
- **AI chat:** LibreChat (per-tenant containers, provisioned by portal)
- **LLM routing:** LiteLLM (per-tenant team keys)
- **Knowledge base:** Qdrant (vector DB)
- **Billing:** Moneybird (NL-based, EU-only)
- **Meetings:** Vexa (bot manager for Google Meet/Zoom/Teams)
- **Infra:** Hetzner servers, Coolify for deployments, Caddy reverse proxy

## Deployment
- **core-01:** Portal backend, LibreChat tenants, LiteLLM, Zitadel, Qdrant, Caddy
- **public-01:** Website, Twenty CRM, Fider, Uptime Kuma
- SSH: `ssh core-01` (klai user), `ssh -i ~/.ssh/klai_ed25519 root@65.109.237.64` for public-01
