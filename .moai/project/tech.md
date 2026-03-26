# Tech Stack: Klai

## Overview

Klai is a multi-subproject TypeScript/Python monorepo. The frontend stack is TypeScript throughout (Astro, React, Next.js). The backend is Python (FastAPI). Infrastructure is Docker/Caddy on Hetzner Linux servers managed with SOPS-encrypted secrets.

---

## klai-website — Marketing Site

**Framework:** Astro 5 (TypeScript strict)
**Rationale:** Static-first rendering for SEO performance, component islands for interactivity, excellent i18n support

| Layer | Technology | Version |
|-------|-----------|---------|
| Framework | Astro | 5.x |
| Styling | Tailwind CSS | v4 |
| CMS | Keystatic | latest |
| i18n | Built-in (NL + EN) | — |
| Language | TypeScript (strict) | 5.x |
| Deployment | Coolify (Hetzner public-01) | — |
| CI/CD | GitHub Actions → Coolify webhook | — |

**Key Commands:**
```bash
npm run dev        # Development server
npm run build      # Production build
npm run preview    # Preview production build
```

---

## klai-portal — Customer SaaS Application

### Frontend

**Framework:** React 19 + Vite (SPA)
**Rationale:** Full client-side app for interactive dashboard; Vite for fast DX; TanStack for type-safe routing and data fetching

| Layer | Technology | Version |
|-------|-----------|---------|
| Framework | React | 19 |
| Bundler | Vite | latest |
| Routing | TanStack Router | v1 (file-based, type-safe) |
| Data Fetching | TanStack Query | v5 |
| i18n | Paraglide JS + Inlang | latest |
| Styling | Tailwind CSS | v4 |
| UI Components | Radix UI primitives + shadcn-style | — |
| Advanced UI | Mantine (tables, rich editor, modals) | v7 |
| Rich Text | BlockNote | latest |
| Error Tracking | Sentry | latest |
| Language | TypeScript (strict) | 5.x |

**Component Rules:**
- Always use `components/ui/` design system (Button, Input, Label, Select, Card)
- Never raw HTML elements with inline Tailwind in route files
- Reference implementation: `frontend/src/routes/admin/users/invite.tsx`

### Backend

**Framework:** FastAPI (Python 3.12)
**Rationale:** Async-first, excellent OpenAPI generation, Pydantic integration for validation

| Layer | Technology | Version |
|-------|-----------|---------|
| Framework | FastAPI | latest |
| Runtime | Python | 3.12 |
| ORM | SQLAlchemy | 2.x (async) |
| Migrations | Alembic | latest |
| Database | PostgreSQL | 16 |
| Async Driver | asyncpg | latest |
| Validation | Pydantic | v2 |
| Server | Uvicorn | latest |
| Auth | OIDC via Zitadel | — |
| Billing | Moneybird API (NL-based, EU-only) | — |
| Linting | ruff + pyright | latest |
| Containerization | Docker | — |

**Key Commands:**
```bash
# Backend
uvicorn app.main:app --reload     # Development
alembic upgrade head              # Apply migrations
alembic revision --autogenerate   # Generate migration

# Frontend
npm run dev                        # Development
npm run build                      # Production build
```

---

## klai-infra — AI Platform Stack

All services run on Docker Compose, managed by SOPS-encrypted secrets.

### AI Stack (core-01, Hetzner EX44)

| Category | Service | Purpose |
|---------|---------|---------|
| **Reverse Proxy** | Caddy | TLS, routing, per-tenant subdomains |
| **Identity** | Zitadel | OIDC, B2B multi-tenancy, SAML (Phase 4) |
| **Chat UI** | LibreChat | Per-tenant isolated chat interface |
| **Model Proxy** | LiteLLM | Model routing, virtual keys, complexity routing |
| **LLM Models** | Mistral API / Qwen3-8B / Qwen3-32B | Current: Mistral API; Phase 3: vLLM self-hosted |
| **Inference** | vLLM | GPU-accelerated inference (Phase 3, Nebius H100) |
| **Embeddings** | TEI (Text Embeddings Inference) | Dense embeddings for RAG |
| **Vector DB** | Qdrant | Semantic search for Focus (RAG) |
| **Graph DB** | FalkorDB (Redis protocol, port 6380 external / 6379 internal) | Knowledge graph for entity-relation retrieval (KB-011) |
| **Knowledge Graph** | graphiti-core[falkordb] >=0.28 (Zep) | Entity extraction, bi-temporal facts, contradiction detection; runs alongside Qdrant vector retrieval |
| **SQL DB** | PostgreSQL | Portal data, Meilisearch metadata, pgvector |
| **Document DB** | MongoDB | Per-tenant chat history (LibreChat) |
| **Cache** | Redis | Session caching, rate limiting |
| **Search** | Meilisearch | Full-text search |
| **Speech-to-Text** | Whisper Server (CPU) | Scribe product; GPU via vLLM Phase 3 |
| **Meeting Bot** | Vexa (`vexaai/vexa-lite:latest`) | Open-source (Apache 2.0) meeting bot framework; joins Google Meet/Zoom/Teams as browser participant for post-meeting transcription |
| **Document Parsing** | Docling | Upload processing for Focus |
| **Web Search** | SearXNG | AI research tool |
| **Metrics** | VictoriaMetrics + Alloy | Time-series metrics collection |
| **Logs** | VictoriaLogs | Centralized log aggregation |
| **Dashboards** | Grafana | Ops dashboards |
| **Error Tracking** | GlitchTip | Application error tracking |

### Marketing Stack (public-01, Hetzner CX42)

| Service | Purpose |
|---------|---------|
| Coolify | CI/CD and app hosting platform |
| klai-website | Astro marketing site |
| Twenty | Open-source CRM |
| Fider | Customer feedback collection |
| Uptime Kuma | Status page and monitoring |

### Secrets Management

**Tool:** SOPS + age encryption
**Source of truth:** `klai-infra/core-01/.env.sops`
**Rule:** NEVER edit `/opt/klai/.env` via shell — all secret changes through SOPS
**Pattern:** See `klai-claude/docs/patterns/infrastructure.md#sops-secret-edit`

```bash
# Edit secrets
sops klai-infra/core-01/.env.sops

# Deploy (decrypts secrets, applies docker-compose)
./klai-infra/core-01/deploy.sh
```

---

## klai-docs — Internal Documentation Portal

| Layer | Technology | Version |
|-------|-----------|---------|
| Framework | Next.js | 15 |
| Runtime | React | 19 |
| Database | PostgreSQL | — |
| Markdown | react-markdown + rehype | — |
| Auth | JOSE JWT | — |
| Language | TypeScript | 5.x |

---

## Development Environment Requirements

### Prerequisites

| Tool | Purpose | Installation |
|------|---------|-------------|
| Node.js 22 LTS | JS/TS runtime | nvm or fnm |
| Python 3.12 | Backend runtime | pyenv |
| Docker + Docker Compose | Local containers | Docker Desktop |
| SOPS | Secret decryption | `brew install sops` |
| age | SOPS encryption backend | `brew install age` |

### LSP Servers (for Claude Code)
- TypeScript: `typescript-language-server` (`npm i -g typescript-language-server`)
- Python: `pyright` (`pip install pyright`)

### Workspace Setup
```bash
# Full workspace setup (run from klai/ root)
./klai-claude/scripts/setup.sh

# Sync Claude assets from klai-claude to all projects
./klai-claude/scripts/sync-to-root.sh
```

---

## Code Quality

| Tool | Scope | Config |
|------|-------|--------|
| ruff | Python linting + formatting | `pyproject.toml` |
| pyright | Python type checking (strict) | `pyproject.toml` |
| ESLint 9 | TypeScript linting | `eslint.config.js` |
| Tailwind CSS | Utility-first styling | `tailwind.config.*` |
| pre-commit | Git hooks (linting before commit) | `.pre-commit-config.yaml` |

**Patterns:** `klai-claude/docs/patterns/code-quality.md`
**Pitfalls:** `klai-claude/docs/pitfalls/process.md` (14 AI dev workflow rules)

---

## Deployment

### klai-website
1. Push to `main` branch
2. GitHub Action triggers Coolify webhook
3. Coolify builds Astro, deploys on public-01

### klai-portal
1. Push to `main` branch
2. GitHub Action builds frontend, rsyncs to `core-01:/opt/klai/portal/`
3. Backend Docker image built, pushed to registry
4. SSH deploy script on core-01 pulls image, restarts containers

### klai-infra
1. Edit `docker-compose.yml` or `.env.sops`
2. Run `./core-01/deploy.sh` (SSH to core-01, decrypt secrets, apply compose)

**Patterns:** `klai-claude/docs/patterns/devops.md`
**Pitfalls:** `klai-claude/docs/pitfalls/devops.md`, `pitfalls/infrastructure.md`

---

## Architecture Decisions

| Decision | Choice | Rationale |
|---------|--------|-----------|
| LLM Provider | Mistral API → vLLM (Phase 3) | EU provider, self-hostable path |
| Model Routing | LiteLLM Complexity Router | < 1ms overhead, 7-dimension analysis |
| Auth | Zitadel | B2B multi-tenancy, OIDC, future SAML |
| Chat UI | LibreChat | Open-source, per-tenant isolation |
| Vector DB | Qdrant + pgvector | Qdrant for RAG, pgvector for metadata |
| Secrets | SOPS + age | Encrypted in git, no separate secret manager |
| Reverse Proxy | Caddy | Automatic TLS, per-tenant routing |
| Billing | Mollie | EU payment provider, direct debit mandates |
| Monorepo | Single git repo | Shared Claude assets, coordinated deploys |

**Full architecture documentation:** `klai-claude/docs/architecture/platform.md`
