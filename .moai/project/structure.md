# Structure: Klai Monorepo (klai-mono)

## Architecture Pattern

**Monorepo.** All Klai products live in a single git repo (`GetKlai/klai`). Shared Claude Code assets are managed via `.claude/` at the root, synced from the canonical source `klai-claude` (separate repo: `GetKlai/klai-claude`).

```
klai-mono/                     # Monorepo root (GetKlai/klai)
├── portal/                    # Customer SaaS application (FastAPI + React)
│   ├── backend/               # FastAPI API server
│   └── frontend/              # React SPA (Vite + TanStack Router)
├── docs/                      # Docs app -- per-tenant documentation portal (Next.js)
├── deploy/                    # AI platform deploy configs (Docker Compose, Caddy, LiteLLM, etc.)
│   ├── klai-connector/        # External source connector service
│   ├── klai-mailer/           # Zitadel email notification provider
│   ├── klai-knowledge-mcp/    # Knowledge MCP server for LibreChat
│   └── knowledge-ingest/      # Document ingestion pipeline (RAG)
├── scribe/                    # Transcription services
│   ├── scribe-api/            # Transcription management API
│   └── whisper-server/        # Self-hosted Whisper inference
├── focus/                     # Research services
│   └── research-api/          # Deep research API
├── retrieval-api/             # Hybrid retrieval service (vector + graph)
├── scripts/                   # Shared utilities and deploy scripts
├── claude-docs/               # Knowledge base (patterns, pitfalls, architecture docs)
├── .claude/                   # Claude Code assets (synced from klai-claude)
│   ├── agents/                # MoAI + Klai agents
│   ├── commands/              # Slash commands (/plan, /run, /sync, /retro, etc.)
│   ├── rules/                 # Auto-loaded rules
│   └── skills/                # MoAI skills
├── .moai/                     # MoAI orchestration config
│   ├── project/               # This documentation (product.md, structure.md, tech.md)
│   ├── specs/                 # SPEC documents (feature specifications)
│   └── state/                 # Session and workflow state
└── CLAUDE.md                  # Project-level Claude instructions
```

---

## portal/ -- Customer SaaS Application

The customer-facing product: dashboard, admin, billing, usage tracking, knowledge management, meeting bots.

```
portal/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI app initialization, lifespan, middleware
│   │   ├── api/               # API routers
│   │   │   ├── auth.py        # OIDC authentication (Zitadel integration)
│   │   │   ├── billing.py     # Billing management
│   │   │   ├── admin.py       # Platform admin endpoints
│   │   │   ├── me.py          # Current user profile
│   │   │   ├── signup.py      # Organization signup + provisioning
│   │   │   ├── groups.py      # User group management
│   │   │   ├── meetings.py    # Meeting bot management (/api/bots/*)
│   │   │   ├── knowledge.py   # Knowledge document endpoints
│   │   │   ├── knowledge_bases.py  # Knowledge base CRUD
│   │   │   ├── app_knowledge_bases.py  # App-level knowledge base endpoints
│   │   │   ├── connectors.py  # External source connector management
│   │   │   ├── internal.py    # Internal API (service-to-service)
│   │   │   ├── webhooks.py    # Webhook receivers
│   │   │   └── dependencies.py # Shared FastAPI dependencies
│   │   ├── models/            # SQLAlchemy ORM models
│   │   ├── services/          # Business logic
│   │   │   ├── zitadel.py     # Zitadel API client
│   │   │   ├── vexa.py        # Vexa meeting bot client
│   │   │   └── bot_poller.py  # Background meeting bot polling
│   │   └── core/
│   │       └── config.py      # Pydantic settings
│   ├── alembic/               # Database migrations
│   ├── tests/                 # pytest test suite
│   ├── pyproject.toml         # Python dependencies + ruff/pyright config
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── routes/            # TanStack Router file-based routing
│   │   │   ├── app/           # Authenticated app routes
│   │   │   │   ├── chat.tsx       # Chat redirect/embed
│   │   │   │   ├── account.tsx    # Account settings
│   │   │   │   ├── scribe.tsx     # Transcription UI
│   │   │   │   ├── meetings/      # Meeting bot pages
│   │   │   │   ├── knowledge/     # Knowledge base management
│   │   │   │   ├── docs/          # Docs management
│   │   │   │   ├── focus/         # Research UI
│   │   │   │   └── transcribe/    # Direct transcription
│   │   │   ├── admin/         # Admin panel routes
│   │   │   ├── setup/         # Organization setup wizard
│   │   │   ├── login.tsx      # Login page
│   │   │   ├── signup.tsx     # Signup page
│   │   │   └── callback.tsx   # OIDC callback
│   │   ├── components/        # Shared React components
│   │   │   └── ui/            # Design system (Button, Input, Label, etc.)
│   │   ├── hooks/             # Custom React hooks
│   │   ├── lib/               # Utilities and API client
│   │   └── paraglide/         # Auto-compiled i18n messages (NL + EN)
│   ├── project.inlang/        # Inlang i18n config
│   ├── vite.config.ts
│   └── Dockerfile
```

---

## deploy/ -- AI Platform Stack

All Docker Compose configs, Caddy config, LiteLLM config, and microservice source code for services deployed on core-01.

```
deploy/
├── docker-compose.yml         # Main compose file (30+ services)
├── deploy.sh                  # Deployment script
├── setup.sh                   # Initial server setup
├── config.example.env         # Example environment variables
├── caddy/                     # Caddy reverse proxy config (wildcard TLS, per-tenant routing)
├── litellm/                   # LiteLLM config (model routing, custom hooks)
│   ├── config.yaml            # Model definitions and routing rules
│   ├── klai_knowledge.py      # Knowledge retrieval hook (RAG injection)
│   └── custom_router.py       # Custom routing logic
├── librechat/                 # LibreChat per-tenant configs and patches
├── klai-connector/            # External source connector service (FastAPI)
├── klai-mailer/               # Zitadel email notification service (FastAPI)
├── klai-knowledge-mcp/        # Knowledge MCP server for LibreChat tool use
├── knowledge-ingest/          # RAG ingestion pipeline (document chunking, embedding, graph)
├── postgres/                  # PostgreSQL init scripts
├── alloy/                     # Grafana Alloy collector config
├── grafana/                   # Grafana provisioning (datasources, dashboards)
├── searxng/                   # SearxNG search engine config
├── bge-m3-sparse/             # Custom sparse embedding server (BGE-M3)
├── vexa-patches/              # Patches for Vexa meeting bot
├── zitadel/                   # Zitadel configuration
└── fail2ban/                  # Fail2ban configuration
```

---

## scribe/ -- Transcription Services

```
scribe/
├── scribe-api/                # Transcription management API (FastAPI)
│   ├── app/                   # Application code
│   ├── tests/
│   └── pyproject.toml
├── whisper-server/            # Self-hosted Whisper inference server
│   └── pyproject.toml
└── scripts/                   # Utility scripts
```

---

## focus/ -- Research Services

```
focus/
└── research-api/              # Deep research API (FastAPI)
    ├── app/                   # Application code
    ├── tests/
    └── pyproject.toml
```

---

## retrieval-api/ -- Hybrid Retrieval Service

Unified retrieval endpoint combining vector search (Qdrant) and knowledge graph (FalkorDB/Graphiti) with Reciprocal Rank Fusion.

```
retrieval-api/
├── retrieval_api/             # Application code
├── tests/
├── scripts/
├── pyproject.toml
└── Dockerfile
```

---

## docs/ -- Documentation Portal

Per-tenant documentation sites backed by Gitea git storage, with Next.js frontend and Zitadel SSO.

```
docs/
├── app/                       # Next.js app directory
├── components/                # React components
├── lib/                       # Utilities
├── migrations/                # Database migrations
├── middleware.ts               # Auth middleware
├── next.config.ts
└── package.json
```

---

## Data Flow

```
User Browser
    │
    ├─→ Caddy (reverse proxy, TLS, tenant routing)
    │     ├─→ portal-api (FastAPI) ─→ PostgreSQL (portal data)
    │     │     ├─→ Zitadel (auth, org management)
    │     │     ├─→ Docker Socket Proxy (tenant container mgmt)
    │     │     └─→ Vexa Bot Manager (meeting bots)
    │     ├─→ LibreChat (per-tenant) ─→ MongoDB (chat history)
    │     │     ├─→ LiteLLM (model proxy) ─→ Mistral API / Ollama
    │     │     │     └─→ retrieval-api (RAG hook) ─→ Qdrant + FalkorDB
    │     │     ├─→ klai-knowledge-mcp (tool use) ─→ docs-app
    │     │     ├─→ SearxNG (web search)
    │     │     └─→ Firecrawl (page extraction)
    │     ├─→ docs-app (Next.js) ─→ Gitea (git storage) + PostgreSQL
    │     │     └─→ knowledge-ingest (document indexing)
    │     ├─→ research-api ─→ Docling + TEI + SearxNG + Qdrant
    │     ├─→ scribe-api ─→ whisper-server (transcription)
    │     └─→ Grafana (monitoring dashboards)
    │
    └─→ Zitadel (OIDC login)
```

---

## Server Layout

| Server | Spec | Cost | Services |
|--------|------|------|----------|
| **core-01** | Hetzner EX44 dedicated | ~47 EUR/mo | Full AI stack (30+ containers) |
| **public-01** | Hetzner CX42 | ~17 EUR/mo | Website, Twenty CRM, Fider, Uptime Kuma |
| **ai-01** | Nebius H100 GPU (Phase 3+) | TBD | vLLM inference, Whisper GPU |

---

## Repo Layout (Multi-Repo Workspace)

| What | Local path | GitHub remote |
|------|-----------|---------------|
| **This monorepo** | `C:\Users\markv\stack\02 - Voys\Code\klai-mono` | `GetKlai/klai` |
| Claude assets source | `C:\Users\markv\stack\02 - Voys\Code\klai\klai-claude` | `GetKlai/klai-claude` |
| Infrastructure secrets | `C:\Users\markv\stack\02 - Voys\Code\klai\klai-infra` | `GetKlai/klai-infra` (private) |
| Website | `C:\Users\markv\stack\02 - Voys\Code\klai\klai-website` | `GetKlai/klai-website` |

---

## Development Workflow

```
Feature Development: /sparring → /moai plan → /moai run → /moai sync
Quick Fix:          /moai fix (or /moai loop)
Code Quality:       /moai review → /moai clean → /moai coverage
Knowledge Capture:  /retro "what happened"
```
