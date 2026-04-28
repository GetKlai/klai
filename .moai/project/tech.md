# Tech Stack: Klai

## Overview

Klai is a multi-service TypeScript/Python monorepo. The frontend stack is TypeScript throughout (React, Next.js). The backend is Python (FastAPI) across all services. Infrastructure is Docker Compose on Hetzner Linux servers with Caddy reverse proxy and SOPS-encrypted secrets.

---

## Portal Backend (klai-portal/backend/)

**Framework:** FastAPI on Python 3.12
**Package manager:** uv

| Layer | Technology | Version |
|-------|-----------|---------|
| Framework | FastAPI | >=0.115 |
| Runtime | Python | >=3.12 |
| Server | Uvicorn | >=0.32 |
| ORM | SQLAlchemy (async) | >=2.0 |
| Migrations | Alembic | >=1.14 |
| Async DB Driver | asyncpg | >=0.30 |
| Validation | Pydantic | >=2.9 |
| Settings | pydantic-settings | >=2.6 |
| HTTP Client | httpx | >=0.27 |
| Docker API | docker (Python SDK) | >=7.0 |
| Crypto | cryptography | >=43.0 |
| Calendar | icalendar | >=6.1, <7.0 |
| Mail authentication (DKIM/SPF/ARC) | authheaders + dkimpy + authres | >=0.16 |
| Public Suffix List (RFC 7489 alignment) | publicsuffix2 | >=2.2, <3.0 |
| MongoDB Driver | motor | >=3.6 |
| Linting | ruff | >=0.8 |
| Type Checking | pyright | >=1.1 |
| Testing | pytest + pytest-asyncio | >=8 / >=0.24 |

---

## Portal Frontend (klai-portal/frontend/)

**Framework:** React 19 SPA with Vite
**Package manager:** npm

| Layer | Technology | Version |
|-------|-----------|---------|
| UI Framework | React | ^19.2.0 |
| Bundler | Vite | ^7.3.1 |
| Language | TypeScript | ~5.9.3 |
| Routing | TanStack Router (file-based) | ^1.114.0 |
| Data Fetching | TanStack Query | ^5.66.0 |
| Tables | TanStack Table | ^8.21.3 |
| Styling | Tailwind CSS | ^4.0.0 |
| UI Primitives | Radix UI | latest (accordion, alert-dialog, dialog, dropdown-menu, popover, scroll-area, separator, tabs, switch, slot) |
| Rich Text Editor | BlockNote | ^0.47.1 |
| Additional UI | Mantine | ^8.0.0 |
| i18n | Paraglide JS (Inlang) | ^2.13.2 |
| Auth | oidc-client-ts + react-oidc-context | ^3.1.0 / ^3.2.0 |
| Error Tracking | Sentry React | ^10.43.0 |
| Icons | Lucide React | ^0.474.0 |
| Command Palette | cmdk | ^1.1.1 |
| Onboarding | driver.js | ^1.3.1 |
| Toast Notifications | Sonner | ^2.0.7 |
| Theme | next-themes | ^0.4.6 |
| Markdown | react-markdown | ^10.1.0 |
| QR Codes | react-qr-code | ^2.0.18 |
| Drag and Drop | @dnd-kit/core | ^6.3.1 |
| Emoji Picker | @emoji-mart/react | ^1.1.1 |
| Linting | ESLint 9 + typescript-eslint | ^9.39.1 / ^8.48.0 |

---

## Docs App (klai-docs/)

**Framework:** Next.js 15 with React 19

| Layer | Technology | Version |
|-------|-----------|---------|
| Framework | Next.js | ^15.0.0 |
| Runtime | React | ^19.0.0 |
| Language | TypeScript | ^5.0.0 |
| Database | PostgreSQL (via pg) | ^8.13.1 |
| Auth | JOSE JWT | ^5.9.6 |
| Markdown | react-markdown + rehype (highlight, raw, slug) + remark-gfm | ^9.0.1 |
| Styling | Tailwind CSS | ^4.0.0 |
| Schema | Zod | ^3.23.8 |
| YAML | js-yaml | ^4.1.0 |
| Linting | ESLint + eslint-config-next | ^9.0.0 / ^15.0.0 |

---

## Scribe API (klai-scribe/scribe-api/)

**Framework:** FastAPI on Python 3.12

| Layer | Technology | Version |
|-------|-----------|---------|
| Framework | FastAPI | >=0.115 |
| ORM | SQLAlchemy (async) | >=2.0 |
| Migrations | Alembic | >=1.14 |
| Auth | python-jose (JWT) | >=3.3 |
| Audio Processing | pydub | >=0.25 |
| File Detection | python-magic | >=0.4 |

---

## Whisper Server (klai-scribe/whisper-server/)

**Framework:** FastAPI on Python 3.12

| Layer | Technology | Version |
|-------|-----------|---------|
| Framework | FastAPI | >=0.115 |
| Whisper | faster-whisper | >=1.1 |
| Model | large-v3-turbo | CPU, int8 |
| Audio | soundfile + numpy | >=0.12 / >=1.26 |

---

## Research API (klai-focus/research-api/)

**Framework:** FastAPI on Python 3.12

| Layer | Technology | Version |
|-------|-----------|---------|
| Framework | FastAPI | >=0.115 |
| ORM | SQLAlchemy (async) | >=2.0 |
| Vector DB Client | qdrant-client | >=1.12 |
| Auth | python-jose (JWT) | >=3.3 |
| Tokenizer | tiktoken | >=0.8 |
| YouTube | youtube-transcript-api | >=0.6 |
| SSE | sse-starlette | >=2.1 |

---

## Retrieval API (klai-retrieval-api/)

**Framework:** FastAPI on Python 3.12

| Layer | Technology | Version |
|-------|-----------|---------|
| Framework | FastAPI | >=0.115 |
| Vector DB Client | qdrant-client | >=1.12 |
| Knowledge Graph | graphiti-core[falkordb] | >=0.28, <0.30 |
| SSE | sse-starlette | >=2.1 |

**Evidence scoring** (SPEC-EVIDENCE-001): chunks krijgen een `final_score = reranker_score × content_type_weight × assertion_weight × temporal_decay`. Shadow mode logt diffs naar VictoriaLogs; activeer met `EVIDENCE_SHADOW_MODE=false` na RAGAS-validatie.

**Evaluatie** (`evaluation/`): RAGAS-script met 150 queries (50 curated + 100 synthetisch), Wilcoxon signed-rank test, per-dimensie isolatie. Gebruik `klai-large` als judge. Run handmatig na baseline-meting.

---

## Klai Connector (klai-connector/)

**Framework:** FastAPI on Python 3.12

| Layer | Technology | Version |
|-------|-----------|---------|
| Framework | FastAPI | >=0.115 |
| ORM | SQLAlchemy (async) | >=2.0 |
| Document Processing | Unstructured | >=0.16.0 |
| HTTP Client | httpx[http2] | >=0.28.0 |
| Scheduler | APScheduler | >=3.10.0 |
| GitHub API | gidgethub | >=5.3.0 |
| JWT | PyJWT[crypto] | >=2.9.0 |
| S3 Client | minio | >=7.2.0 |
| Image Validation | filetype | >=1.2.0 |

---

## Shared Libraries (klai-libs/)

Path-installed editable libraries consumed by multiple services. Changes
ripple to every consumer on `uv sync`; drift between services is
structurally prevented because there is only one implementation.

### klai-libs/image-storage

**Purpose:** Shared image pipeline + canonical SSRF guard
(SPEC-KB-IMAGE-002, SPEC-SEC-SSRF-001)

**Consumers:** `klai-knowledge-ingest`, `klai-connector`, `klai-portal/backend`

| Module | Purpose |
|---|---|
| `storage` | Content-addressed S3 upload (Garage) |
| `pipeline` | Adapter + crawl image download orchestrators |
| `url_guard` | Canonical SSRF guard: `validate_url_pinned`, `validate_confluence_base_url`, `PinnedResolverTransport`, `ValidatedURL` |

| Layer | Technology | Version |
|-------|-----------|---------|
| HTTP Client | httpx | >=0.28 |
| S3 Client | minio | >=7.2 |
| Image Validation | filetype | >=1.2 |
| Structured Logging | structlog | >=25.0 |

### klai-libs/connector-credentials

**Purpose:** Tenant-scoped encrypted credential storage for connector
configs. Consumed by `klai-portal/backend` and `klai-connector`.

### klai-libs/identity-assert

**Purpose:** Shared service-to-service identity verification helper
(SPEC-SEC-IDENTITY-ASSERT-001). Calls portal-api's
`POST /internal/identity/verify` endpoint with consumer-side caching
(60 s TTL, fail-closed). Single implementation across every Python
consumer that carries a tenant or user identity claim — services do
not re-implement the contract.

**Consumers:** future Phase B/C/D adopters (`klai-knowledge-mcp`,
`klai-scribe`, `klai-retrieval-api`). Phase A landed the library +
endpoint; consumers migrate one PR each.

| Module | Purpose |
|---|---|
| `client` | `IdentityAsserter` — async httpx client + LRU cache |
| `models` | `VerifyResult` frozen dataclass + `KNOWN_CALLER_SERVICES` allowlist |
| `cache` | In-process TTL cache (privacy boundary: per-process only) |
| `telemetry` | structlog `identity_assert_call` event with hashed user_id |

| Layer | Technology | Version |
|-------|-----------|---------|
| HTTP Client | httpx | >=0.28 |
| Structured Logging | structlog | >=25.0 |

---

## Garage S3 (deploy/garage/)

**Purpose:** Image storage for the knowledge pipeline (SPEC-KB-IMAGE-001)

| Layer | Technology | Version |
|-------|-----------|---------|
| Object Storage | Garage | v2.2.0 (`dxflrs/garage:v2.2.0`) |
| S3 API | Port 3900 (authenticated uploads) | — |
| Web Endpoint | Port 3902 (anonymous reads via Caddy) | — |
| Caddy Route | `/kb-images/*` → `garage:3902` | — |

---

## Klai Mailer (klai-mailer/)

**Framework:** FastAPI on Python 3.12

| Layer | Technology | Version |
|-------|-----------|---------|
| Framework | FastAPI | >=0.115 |
| Email | aiosmtplib | >=3.0 |
| Templates | Jinja2 | >=3.1 |

---

## Klai Knowledge MCP (klai-knowledge-mcp/)

**Framework:** MCP SDK on Python 3.12

| Layer | Technology | Version |
|-------|-----------|---------|
| MCP Framework | mcp[cli] | >=1.9.0 |
| HTTP Client | httpx | >=0.27.0 |

---

## Knowledge Ingest (klai-knowledge-ingest/)

**Framework:** FastAPI on Python 3.12

| Layer | Technology | Version |
|-------|-----------|---------|
| Framework | FastAPI | ==0.115.6 |
| ORM | SQLAlchemy (async) | ==2.0.36 |
| Vector DB Client | qdrant-client | ==1.12.1 |
| Job Queue | procrastinate[asyncpg] | ==2.15.0 |
| HTML Parsing | html2text | ==2024.2.26 |
| Caching | cachetools | ==5.5.2 |
| Clustering | scikit-learn | >=1.3.0 |

---

## Infrastructure (deploy/docker-compose.yml)

### Databases

| Service | Image | Purpose |
|---------|-------|---------|
| PostgreSQL | pgvector/pgvector:pg17 | Primary relational DB (portal, Zitadel, LiteLLM, GlitchTip, Gitea, Vexa, research, scribe) |
| MongoDB | mongo:latest | Per-tenant LibreChat chat history |
| Redis | redis:alpine | Session cache, rate limiting, LiteLLM cache |
| Qdrant | qdrant/qdrant:latest | Vector store for knowledge retrieval |
| FalkorDB | falkordb/falkordb:latest | Knowledge graph (Graphiti entity-relation store) |
| Meilisearch | getmeili/meilisearch:latest | Full-text search for LibreChat |

### AI and ML Services

| Service | Image | Purpose |
|---------|-------|---------|
| LiteLLM | ghcr.io/berriai/litellm:main-stable | Model proxy, routing, virtual keys, RAG hook |
| Ollama | ollama/ollama:latest | CPU fallback inference (6 CPUs, 12GB RAM limit) |
| TEI | ghcr.io/huggingface/text-embeddings-inference:cpu-latest | Dense embeddings (BAAI/bge-m3) |
| BGE-M3 Sparse | Custom build (deploy/bge-m3-sparse) | Sparse embeddings (BAAI/bge-m3, 4 CPUs, 8GB RAM) |
| Infinity Reranker | michaelf34/infinity:latest | Reranking (BAAI/bge-reranker-v2-m3, 4 CPUs, 3GB RAM) |
| Whisper Server | ghcr.io/getklai/whisper-server:latest | Speech-to-text (large-v3-turbo, CPU int8) |
| Docling | ghcr.io/docling-project/docling-serve:latest | Document parsing and conversion |

### Application Services

| Service | Image | Purpose |
|---------|-------|---------|
| Portal API | ghcr.io/getklai/portal-api:latest | Tenant provisioning, auth, billing, meetings |
| LibreChat (klai) | ghcr.io/danny-avila/librechat:latest | AI chat (klai tenant) |
| LibreChat (getklai) | ghcr.io/danny-avila/librechat:latest | AI chat (getklai tenant) |
| Docs App | ghcr.io/getklai/klai-docs:latest | Per-tenant documentation sites |
| Research API | ghcr.io/getklai/research-api:latest | Deep research service |
| Retrieval API | ghcr.io/getklai/retrieval-api:latest | Hybrid vector + graph retrieval |
| Scribe API | ghcr.io/getklai/scribe-api:latest | Transcription management |
| Knowledge Ingest | ghcr.io/getklai/knowledge-ingest:latest | RAG ingestion pipeline |
| Klai Connector | ghcr.io/getklai/klai-connector:latest | External source sync |
| Klai Knowledge MCP | ghcr.io/getklai/klai-knowledge-mcp:latest | MCP server for LibreChat |
| Klai Mailer | ghcr.io/getklai/klai-mailer:latest | Email notifications for Zitadel |
| Vexa Bot Manager | ghcr.io/getklai/vexa-lite:latest | Meeting bot orchestration |

### Supporting Services

| Service | Image | Purpose |
|---------|-------|---------|
| Caddy | ghcr.io/getklai/caddy-hetzner:latest | Reverse proxy, wildcard TLS, tenant routing |
| Zitadel | ghcr.io/zitadel/zitadel:latest | Identity provider (OIDC, multi-tenant) |
| Gitea | gitea/gitea:latest | Git storage for docs content |
| SearxNG | searxng/searxng:latest | Self-hosted web search |
| Firecrawl | ghcr.io/mendableai/firecrawl:latest | Web page scraping for LibreChat |
| Crawl4AI | unclecode/crawl4ai:latest | Web crawling for klai-connector |
| Docker Socket Proxy | tecnativa/docker-socket-proxy:latest | Limited Docker API access for portal-api |

### Monitoring Stack

| Service | Image | Purpose |
|---------|-------|---------|
| VictoriaMetrics | victoriametrics/victoria-metrics:latest | Metrics storage (30d retention, 2 CPUs, 4GB RAM) |
| VictoriaLogs | victoriametrics/victoria-logs:latest | Log storage (30d retention, 2 CPUs, 2GB RAM) |
| cAdvisor | gcr.io/cadvisor/cadvisor:latest | Container metrics |
| Grafana Alloy | grafana/alloy:latest | Metrics and logs collector |
| Grafana | grafana/grafana:latest | Dashboards (with Zitadel SSO) |
| GlitchTip | glitchtip/glitchtip:latest | Error tracking (Sentry-compatible) |

### Network Architecture

The compose stack uses isolated internal networks for security:

| Network | Purpose | Internal |
|---------|---------|----------|
| klai-net | Main service mesh | No |
| socket-proxy | portal-api to Docker socket | Yes |
| inference | LiteLLM to Ollama | Yes |
| monitoring | VictoriaMetrics/Logs/cAdvisor | Yes |
| net-mongodb | MongoDB to LibreChat | Yes |
| net-postgres | PostgreSQL to consumers | Yes |
| net-redis | Redis to consumers | Yes |
| net-meilisearch | Meilisearch to LibreChat | Yes |
| vexa-bots | Bot containers (needs internet) | No |

---

## Auth

**Provider:** Zitadel (self-hosted)
**Protocol:** OIDC / OAuth 2.0
**Multi-tenancy:** One Zitadel organization per customer
**SSO:** All services authenticate via Zitadel (portal, LibreChat, Grafana, docs-app)
**Token validation:** JWT introspection via Zitadel endpoint (scribe-api, research-api, klai-connector)

---

## Secrets Management

**Tool:** SOPS + age encryption
**Source of truth:** `klai-infra/core-01/.env.sops`
**Rule:** NEVER edit `/opt/klai/.env` via shell -- all secret changes through SOPS

---

## Code Quality

| Tool | Scope | Config |
|------|-------|--------|
| ruff | Python linting + formatting (all services) | pyproject.toml (line-length 100-120, py312) |
| pyright | Python type checking (strict in klai-connector) | pyproject.toml |
| ESLint 9 | TypeScript linting | eslint.config.mjs |
| Tailwind CSS v4 | Utility-first styling | Vite plugin |

---

## Deployment

**Portal frontend:** GitHub Action builds, rsyncs static dist to core-01:/opt/klai/portal-dist/ (served by Caddy)
**Backend services:** Docker images built and pushed to ghcr.io/getklai/*, pulled on core-01
**Deploy configs:** `deploy/docker-compose.yml` synced to core-01 via GitHub Action on push to main

---

## Architecture Decisions

| Decision | Choice | Rationale |
|---------|--------|-----------|
| LLM Provider | Mistral API + Ollama fallback | EU provider, self-hostable path |
| Auth | Zitadel | B2B multi-tenancy, OIDC, future SAML |
| Chat UI | LibreChat | Open-source, per-tenant isolation |
| Vector DB | Qdrant | Purpose-built vector search, EU company |
| Knowledge Graph | FalkorDB + Graphiti | Entity-relation retrieval alongside vector |
| Embeddings | BGE-M3 (dense + sparse) | Multilingual, self-hosted via TEI |
| SQL DB | PostgreSQL 17 + pgvector | Shared by all services, vector extension |
| Document DB | MongoDB | LibreChat native storage |
| Secrets | SOPS + age | Encrypted in git, no separate secret manager |
| Reverse Proxy | Caddy | Automatic TLS, per-tenant routing |
| Monorepo | Single git repo | Shared Claude assets, coordinated deploys |
| Package Manager | uv (Python), npm (JS) | Fast, reliable dependency resolution |
