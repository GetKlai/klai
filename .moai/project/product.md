# Product: Klai

## Project Overview

**Klai** is a privacy-first, EU-only AI infrastructure platform for European enterprises. It provides secure, compliant access to Large Language Models (LLMs) with full data residency in the EU, transparent open-source components, and zero vendor lock-in.

**Core Problem Solved:** Enterprises need AI capabilities but face compliance barriers — GDPR, data residency requirements, Cloud Act vulnerability, and opacity around where data actually goes. Klai answers "Where does my data go?" with full auditability, EU-only infrastructure, and open-source components.

**Core Value Proposition:**
- Data stays exclusively in the EU (Hetzner servers in Germany)
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
AI chat interface powered by LibreChat. Each customer gets an isolated subdomain (e.g., `acme.getklai.com`) with their own data store. Model routing via LiteLLM Complexity Router: simple queries → Qwen3-8B, complex → Qwen3-32B (< 1ms routing overhead). Automatic fallback from Mistral API to Ollama CPU on failure.

### Focus
RAG-enabled document Q&A. Customers upload company documents; Focus uses them to answer questions with source citations. Built on Qdrant vector database + TEI dense embeddings + pgvector. (Phase 2 — live)

### Scribe
Voice-to-text transcription via Whisper Server. CPU-based for current scale, GPU-accelerated coming in Phase 3. Integrated as Scribe API within the platform.

### Meeting Transcription
Bot-assisted meeting transcription via Vexa integration (SPEC-SCRIBE-002). A Vexa bot joins Google Meet, Zoom, or Microsoft Teams as a browser participant, records the combined audio stream, and submits it for post-meeting batch transcription via the existing Whisper Server. Key capabilities:
- Vexa bot joins Google Meet, Zoom, and Microsoft Teams as a browser participant
- Post-meeting batch transcription via Whisper Server (no real-time overhead)
- Speaker attribution via Vexa's DOM-based speaking-indicator detection
- EU-only audio processing on Hetzner core-01 — audio never leaves Klai infrastructure
- Transcript available in portal under `/app/meetings` with copy and download
- Consent notice displayed and recorded before any bot is dispatched

---

## Core Features

**Customer-Facing**
- Private chat with Klai LLM models (isolated per tenant)
- Focus: RAG document Q&A with source citations
- Scribe: Voice-to-text transcription
- Usage dashboard (per-user token tracking)
- Self-service billing (Moneybird — NL-based, EU-only)
- Invoicing and VAT management
- Multi-language UI (Dutch + English)
- Admin panel: invite users, manage roles, organization settings
- OIDC Single Sign-On via Zitadel

**Platform**
- Automatic tenant provisioning on signup (Zitadel org + LibreChat container + LiteLLM virtual key)
- Multi-tenancy: one subdomain per customer, separate MongoDB database per tenant
- LiteLLM Complexity Router (7-dimension analysis, < 1ms overhead)
- Per-tenant librechat.yaml with rolling updates without downtime
- Data isolation: per-user storage in LibreChat, per-tenant databases
- EU-only data residency (Hetzner Germany)
- Observability: VictoriaMetrics + Grafana dashboards, GlitchTip error tracking, VictoriaLogs

---

## Use Cases

1. **Enterprise Chat** — Employees access private AI chat without sending data to US cloud providers
2. **Document Q&A (Focus)** — Upload internal policy documents, contracts, or handbooks; ask questions with cited sources
3. **Meeting Transcription (Scribe)** — Record and transcribe meetings or voice notes in Dutch and English
4. **Compliance Reporting** — Audit logs, DPA, EU data residency documentation for regulatory requirements
5. **AI API Access** — Developers use Klai's OpenAI-compatible API for building internal tools

---

## Roadmap Phases

| Phase | Status | Key Deliverable |
|-------|--------|----------------|
| Phase 1 | ✅ Complete | Portal live, first paying customer, provisioning active |
| Phase 2 | 🚧 In Progress | Focus (RAG) live, Scribe live, end-to-end verified |
| Phase 3 | 🚧 Starting | GPU server (ai-01) for vLLM self-hosting, Whisper GPU |
| Phase 4 | 📋 Planned | Enterprise SAML SSO, SCIM provisioning |
| Phase 5 | 📋 Planned | LiteLLM Enterprise, advanced audit logs |

---

## Subprojects

| Subproject | Role |
|-----------|------|
| **klai-website** | Public marketing site (getklai.com) — Astro + Keystatic CMS |
| **klai-portal** | Customer SaaS application — FastAPI + React |
| **klai-infra** | Infrastructure configuration & deployment — Docker, SOPS, Caddy |
| **klai-docs** | Internal documentation portal — Next.js |
| **klai-claude** | Canonical Claude Code assets — agents, rules, patterns, knowledge base |

---

## Key Differentiators vs Competition

| Feature | Klai | ChatGPT | Azure AI |
|---------|------|---------|---------|
| EU data residency | ✅ Guaranteed | ❌ US servers | ⚠️ Opt-in, complex |
| Open source | ✅ All components | ❌ Proprietary | ⚠️ Partial |
| Self-hostable | ✅ Optional | ❌ No | ⚠️ Complex enterprise |
| Cloud Act vulnerability | ✅ None (EU company) | ❌ US company | ❌ US company |
| Transparent pricing | ✅ Published | ⚠️ Variable | ❌ Complex |
| Per-tenant isolation | ✅ Complete | ❌ Shared | ⚠️ Enterprise only |
