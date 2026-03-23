# Structure: Klai Monorepo (klai-mono)

## Architecture Pattern

**Monorepo.** All Klai products live in a single git repo (`GetKlai/klai`). Shared Claude Code assets are managed via `.claude/` at the root, synced from the canonical source `klai-claude` (separate repo: `GetKlai/klai-claude`).

```
klai-mono/                     # Monorepo root (GetKlai/klai)
├── portal/                    # Customer SaaS application (FastAPI + React)
├── docs/                      # Internal documentation portal (Next.js)
├── deploy/                    # AI platform deploy configs (Docker Compose, Caddy, LiteLLM)
├── scribe/                    # Scribe transcription service
├── focus/                     # Focus RAG document Q&A service
├── scripts/                   # Shared utilities and deploy scripts
├── claude-docs/               # Knowledge base (patterns, pitfalls, architecture docs)
├── .claude/                   # Claude Code assets (synced from klai-claude)
│   ├── agents/                # MoAI + Klai agents (30+ specialized agents)
│   ├── commands/              # Slash commands (/plan, /run, /sync, /retro, etc.)
│   ├── rules/                 # Auto-loaded rules (Serena, context7, styleguide, secrets)
│   └── skills/                # MoAI skills (moai, moai:plan, moai:run, etc.)
├── .moai/                     # MoAI orchestration config
│   ├── config/                # Quality gates, language, development mode
│   ├── project/               # This documentation (product.md, structure.md, tech.md)
│   ├── specs/                 # SPEC documents (feature specifications)
│   └── state/                 # Session and workflow state
└── CLAUDE.md                  # Project-level Claude instructions
```

---

## portal/ — Customer SaaS Application

The customer-facing product: dashboard, admin, billing, usage tracking.

```
portal/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI app initialization, middleware
│   │   ├── models.py          # SQLAlchemy models (users, billing, organizations)
│   │   ├── models/
│   │   │   └── meetings.py    # VexaMeeting SQLAlchemy model (SPEC-SCRIBE-002)
│   │   ├── routes/            # API endpoints
│   │   │   ├── auth.py        # OIDC authentication (Zitadel integration)
│   │   │   ├── billing.py     # Mollie direct debit, invoicing
│   │   │   └── usage.py       # Token usage tracking
│   │   ├── api/
│   │   │   └── meetings.py    # FastAPI router /api/bots/*
│   │   └── services/          # Business logic
│   │       ├── provisioning.py # Auto-tenant setup (Zitadel + LibreChat + LiteLLM)
│   │       ├── billing.py     # Billing service (mandates, invoicing)
│   │       └── vexa.py        # VexaClient for meeting bot
│   ├── alembic/               # Database migrations (schema versioning)
│   ├── tests/                 # pytest test suite
│   ├── pyproject.toml         # Python dependencies + ruff/pyright config
│   └── Dockerfile             # Backend container
├── frontend/
│   ├── src/
│   │   ├── routes/            # TanStack Router file-based routing
│   │   │   ├── admin/users/   # Admin user management
│   │   │   ├── auth/          # Login, OIDC callback
│   │   │   ├── dashboard/     # Main customer dashboard
│   │   │   └── app/meetings/  # Meeting bot pages
│   │   ├── components/
│   │   │   └── ui/            # Design system (Button, Input, Label, Select, Card, etc.)
│   │   ├── hooks/             # Custom React hooks (data fetching, state)
│   │   └── paraglide/         # Auto-compiled i18n messages
│   ├── project.inlang/        # Inlang i18n config (NL + EN source)
│   ├── vite.config.ts         # Vite bundler configuration
│   └── Dockerfile             # Frontend container
└── docs/
    └── ui-components.md       # Portal UI component usage rules
```

**Reference Implementation:** `portal/frontend/src/routes/admin/users/invite.tsx` — canonical example of UI component patterns

---

## deploy/ — AI Platform Stack

All Docker Compose configs, Caddy config, LiteLLM config, and service configurations for core-01.

```
deploy/
├── docker-compose.yml         # Main AI stack compose file
├── caddy/                     # Caddy reverse proxy configuration
├── litellm/                   # LiteLLM model proxy configuration
├── knowledge-ingest/          # Document ingestion pipeline (Focus)
├── klai-knowledge-mcp/        # Knowledge MCP server
└── *.yml                      # Per-service compose overrides
```

**Server Layout:**
- `public-01` (Hetzner CX42, €17/mo): Coolify, website, Twenty CRM, Fider, Uptime Kuma
- `core-01` (Hetzner EX44 dedicated, €47/mo): Full AI stack (portal, LibreChat, LiteLLM, Qdrant, etc.)
- `ai-01` (Nebius H100 GPU, Phase 3+): vLLM inference, Whisper GPU

---

## claude-docs/ — Knowledge Base

Living knowledge base of patterns and pitfalls, built up through experience. This is the canonical source synced to `.claude/rules/klai/` references.

```
claude-docs/
├── patterns/
│   ├── devops.md              # Coolify, Docker, CI/CD patterns
│   ├── infrastructure.md      # Hetzner, SOPS, env, DNS, SSH patterns
│   ├── platform.md            # LiteLLM, vLLM, LibreChat, Zitadel, Caddy patterns
│   ├── frontend.md            # i18n, component patterns
│   └── code-quality.md        # ruff, pyright, ESLint, pre-commit
├── pitfalls/
│   ├── process.md             # AI dev workflow rules (universal)
│   ├── git.md                 # Git safety rules
│   ├── devops.md              # Deployment pitfalls
│   ├── infrastructure.md      # Secret management pitfalls
│   └── platform.md            # AI stack pitfalls
└── architecture/
    └── platform.md            # Full AI stack architecture
```

---

## Key Cross-Project Files

| File | Location | Purpose |
|------|----------|---------|
| `CLAUDE.md` | Root | Project-level Claude instructions |
| `portal/docs/ui-components.md` | Portal | UI component usage rules |
| `claude-docs/patterns/` | Root | Patterns for DevOps, infra, platform |
| `claude-docs/pitfalls/` | Root | Pitfalls for all domains |

---

## Development Workflow

```
Feature Development: /sparring → /moai plan → /moai run → /moai sync
Quick Fix:          /moai fix (or /moai loop)
Code Quality:       /moai review → /moai clean → /moai coverage
Knowledge Capture:  /retro "what happened"
```

## Repo Layout

| What | Local path | GitHub remote |
|------|-----------|---------------|
| **This monorepo** | `C:\Users\markv\stack\02 - Voys\Code\klai-mono` | `GetKlai/klai` |
| Claude assets source | `C:\Users\markv\stack\02 - Voys\Code\klai\klai-claude` | `GetKlai/klai-claude` |
| Infrastructure secrets | `C:\Users\markv\stack\02 - Voys\Code\klai\klai-infra` | `GetKlai/klai-infra` (private) |
| Website | `C:\Users\markv\stack\02 - Voys\Code\klai\klai-website` | `GetKlai/klai-website` |
