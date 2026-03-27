# Product: Klai

## Project Overview

**Klai** is a privacy-first, EU-only AI infrastructure platform for European enterprises. It provides secure, compliant access to Large Language Models (LLMs) with full data residency in the EU, transparent open-source components, and zero vendor lock-in.

**Core Problem Solved:** Enterprises need AI capabilities but face compliance barriers -- GDPR, data residency requirements, Cloud Act vulnerability, and opacity around where data actually goes. Klai answers "Where does my data go?" with full auditability, EU-only infrastructure, and open-source components.

**Core Value Proposition:**
- Data stays exclusively in the EU (Hetzner servers)
- Only open-source or open-weight models from EU providers (Mistral, Qwen)
- Full transparency: published finances, roadmap, company documentation
- Self-hostable for maximum control
- Per-tenant isolation and privacy-first architecture

---

## Target Audience

| Role | Pain Point | Klai's Answer |
|------|-----------|---------------|
| **Compliance Officers** | "Where does my data go?" | EU-only, GDPR-compliant, DPA available |
| **IT Directors** | "Is it open source and auditable?" | All components open-source, published architecture |
| **Enterprise Management** | "Can we negotiate enterprise terms?" | Enterprise SAML/SCIM coming (Phase 4) |
| **Privacy-Conscious SMBs** | EU data, transparent pricing, no lock-in | Self-service, monthly billing, clear pricing |
| **Regulated industries** | Finance, healthcare, government GDPR requirements | Full audit logs, EU data residency guarantee |

**Primary Markets:** Dutch and German enterprises, EU-regulated industries, privacy-focused organizations

---

## Core Products

### Chat
AI chat interface powered by LibreChat. Each customer gets an isolated subdomain (e.g., `acme.getklai.com`) with their own data store. Model routing via LiteLLM: simple queries routed to fast models, complex queries to large models. Automatic fallback from Mistral API to Ollama CPU on failure. Web search via SearxNG + Firecrawl for full-page content extraction. Reranking via self-hosted Infinity (BGE-reranker-v2-m3).

### Knowledge
Organization-scoped knowledge bases with document management. Hybrid retrieval pipeline combining vector search (Qdrant) and knowledge graph (FalkorDB/Graphiti) with Reciprocal Rank Fusion. Document ingestion via knowledge-ingest service with dense + sparse embeddings (BGE-M3 via TEI + custom sparse server). External source connectors (klai-connector): GitHub repos, websites via Crawl4AI. Knowledge-augmented chat responses via LiteLLM retrieval hook. MCP server (klai-knowledge-mcp) for LibreChat tool integration. **Knowledge Gaps dashboard** tracks unanswered questions (zero chunks or low-confidence retrieval) so admins can identify and fill content gaps in their knowledge bases.

### Docs
Per-tenant documentation sites backed by Gitea git storage. Next.js docs-app with Zitadel SSO authentication. Markdown-based content with automatic knowledge base ingestion for RAG.

### Focus
Deep research workflows with document Q&A. Document processing via Docling. Web search integration via SearxNG. Streaming research output with source citations.

### Scribe
Voice-to-text transcription via self-hosted Whisper Server (large-v3-turbo model, faster-whisper on CPU). Audio/video file upload with transcription history per organization.

### Meeting Transcription
Bot-assisted meeting transcription via Vexa integration. A Vexa bot joins Google Meet as a browser participant, records the combined audio stream, and submits it for post-meeting batch transcription via Whisper Server. Key capabilities:
- Vexa bot joins Google Meet as a browser participant
- Post-meeting batch transcription via Whisper Server (no real-time overhead)
- Speaker attribution via Vexa's DOM-based speaking-indicator detection
- EU-only audio processing -- audio never leaves Klai infrastructure
- Calendar invite parsing via IMAP listener (meet@getklai.com)
- Consent notice displayed and recorded before any bot is dispatched

---

## Core Features

**Customer-Facing**
- Private chat with Klai LLM models (isolated per tenant)
- Knowledge bases: upload documents, connect GitHub/websites, ask questions with cited sources; gap dashboard shows admins which questions the KB can't answer
- Docs: per-tenant documentation sites with markdown editing
- Focus: deep research with web search and document analysis
- Scribe: voice-to-text transcription
- Meeting bots: automated meeting join, recording, and transcription
- Usage dashboard (per-user token tracking)
- Self-service billing
- Multi-language UI (Dutch + English via Paraglide/Inlang)
- Admin panel: invite users, manage groups, organization settings
- OIDC Single Sign-On via Zitadel

**Platform**
- Automatic tenant provisioning on signup (Zitadel org + LibreChat container + LiteLLM virtual key)
- Multi-tenancy: one subdomain per customer, separate MongoDB database per tenant
- Per-tenant librechat.yaml with rolling updates without downtime
- Data isolation: per-user storage in LibreChat, per-tenant databases
- EU-only data residency (Hetzner)
- Observability: VictoriaMetrics + Grafana dashboards, GlitchTip error tracking, VictoriaLogs
- Hybrid RAG: vector search (Qdrant) + knowledge graph (FalkorDB/Graphiti) with RRF merging

---

## Use Cases

1. **Enterprise Chat** -- Employees access private AI chat without sending data to US cloud providers
2. **Knowledge Management** -- Upload documents, connect external sources (GitHub, websites), and get AI-powered answers with citations
3. **Team Documentation** -- Per-tenant docs sites with version-controlled markdown content
4. **Deep Research (Focus)** -- Research topics with web search and document analysis, get structured output with sources
5. **Meeting Transcription** -- Automated meeting bot joins calls, records, and transcribes with speaker attribution
6. **Voice Transcription (Scribe)** -- Upload audio/video files for transcription
7. **Compliance Reporting** -- Audit logs, DPA, EU data residency documentation for regulatory requirements
8. **AI API Access** -- Developers use Klai's OpenAI-compatible API (via LiteLLM) for building internal tools

---

## Roadmap Phases

| Phase | Status | Key Deliverable |
|-------|--------|----------------|
| Phase 1 | Complete | Portal live, first paying customer, provisioning active |
| Phase 2 | In Progress | Focus (RAG) live, Scribe live, Knowledge bases, Docs, Connectors |
| Phase 3 | Starting | GPU server (ai-01) for vLLM self-hosting, Whisper GPU |
| Phase 4 | Planned | Enterprise SAML SSO, SCIM provisioning |
| Phase 5 | Planned | LiteLLM Enterprise, advanced audit logs |

---

## Subprojects

| Subproject | Role |
|-----------|------|
| **klai-website** | Public marketing site (getklai.com) -- Astro + Keystatic CMS |
| **klai-portal** | Customer SaaS application -- FastAPI + React |
| **klai-infra** | Infrastructure configuration and deployment -- Docker, SOPS, Caddy |
| **klai-docs** | Internal documentation portal -- Next.js |
| **klai-claude** | Canonical Claude Code assets -- agents, rules, patterns, knowledge base |

---

## Key Differentiators vs Competition

| Feature | Klai | ChatGPT | Azure AI |
|---------|------|---------|---------|
| EU data residency | Guaranteed | US servers | Opt-in, complex |
| Open source | All components | Proprietary | Partial |
| Self-hostable | Optional | No | Complex enterprise |
| Cloud Act vulnerability | None (EU company) | US company | US company |
| Transparent pricing | Published | Variable | Complex |
| Per-tenant isolation | Complete | Shared | Enterprise only |
