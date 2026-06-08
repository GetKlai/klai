# Klai Services Overview

## Portal (klai-portal/)
- **Backend:** `klai-portal/backend/` — FastAPI, SQLAlchemy async, Alembic, PostgreSQL.
- **Frontend:** `klai-portal/frontend/` — React 19 + Vite + TanStack Router + Mantine 8 + Paraglide i18n

## klai-scribe (`klai-scribe/`)
- **Purpose:** Meeting/audio transcription
- **Components:**
  - `whisper-server/` — Whisper ASR server image/runtime
  - `scribe-api/` — FastAPI, stores transcriptions, integrates with portal

## klai-website (`klai-website/`)
- **Purpose:** Marketing website
- **Stack:** Astro 5, TypeScript strict, Tailwind CSS v4, Keystatic CMS
- **Deploy:** environment-specific

## klai-connector (in deploy/ or separate service)
- **Purpose:** Syncs external sources (currently GitHub) to the Klai knowledge base (Qdrant)
- **Port:** 8200
- **Auth:** Internal service-to-service via AuthMiddleware
- **Key components:**
  - `adapters/github.py` — GitHub API adapter
  - `clients/knowledge_ingest.py` — Pushes docs to knowledge ingest endpoint
  - `services/sync_engine.py` — Orchestrates sync runs
  - `services/scheduler.py` — APScheduler-based periodic sync
  - `services/crypto.py` — AES-GCM encryption for stored OAuth secrets

## klai-focus / research-api
- **Purpose:** Research API (AI-powered research/focus features)
- **Stack:** FastAPI + Python
- Calls retrieval-api for all retrieval (no direct Qdrant access)

## Inference Services
Inference endpoints are environment-specific. The public product contracts are:

| Service | Model family | Purpose |
|---------|--------------|---------|
| embedding runtime | BGE-M3-compatible | Dense embeddings for knowledge-ingest and retrieval-api |
| reranker runtime | cross-encoder reranker | Reranking for retrieval-api |
| sparse embedding runtime | BGE-M3-compatible sparse encoder | Sparse embeddings for knowledge-ingest |
| whisper-server | Whisper-compatible ASR | STT for scribe-api |

## External Platform Services
| Service | Role | URL |
|---------|------|-----|
| Zitadel | SSO/Auth | public auth endpoint |
| LiteLLM | LLM proxy/routing | internal service |
| LibreChat | AI chat UI (per tenant) | {slug}.getklai.com |
| Qdrant | Vector DB for knowledge | internal |
| Vexa | Meeting bot manager | internal service |
| Moneybird | Dutch billing/invoicing | moneybird.com API |
| Caddy | Reverse proxy + TLS | self-hosted edge service |
| Redis | Cache/queues | internal |
| Meilisearch | Search (shared across tenants) | internal |
| VictoriaMetrics | Metrics/monitoring | internal |
