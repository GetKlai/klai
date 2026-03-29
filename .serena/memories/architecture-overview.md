# Klai Architecture Overview

## Workspace Layout
`C:\Users\markv\stack\02 - Voys\Code\klai` — NOT a git repo, it's a workspace container.
Active monorepo lives at `C:\Users\markv\stack\02 - Voys\Code\klai-mono` (GetKlai/klai).

## Repos in workspace

| Repo | Purpose | Language | Status |
|------|---------|----------|--------|
| klai-portal | Customer portal (UI + API) | Python + TypeScript | Active |
| klai-connector | GitHub→Knowledge sync service | Python (FastAPI) | Active |
| klai-scribe | Meeting transcription (Whisper + API) | Python | Active |
| klai-website | Marketing site | Astro/TypeScript | Active |
| klai-focus | Research API | Python (FastAPI) | Active |
| klai-claude | Claude Code assets (agents/rules/commands) | Markdown | Active |
| klai-infra | Infrastructure secrets (SOPS) | Shell/YAML | Private |
| klai-compliance | ISO27001 policies + SOA | Markdown | Active |
| klai-docs | Documentation site | — | Present |

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
