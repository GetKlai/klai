# Klai Services Overview

## Portal (klai-portal/)
- **Backend:** `klai-portal/backend/` — FastAPI, SQLAlchemy async, Alembic, PostgreSQL. Port 8010 on core-01.
- **Frontend:** `klai-portal/frontend/` — React 19 + Vite + TanStack Router + Mantine 8 + Paraglide i18n

## klai-scribe (`klai-scribe/`)
- **Purpose:** Meeting/audio transcription
- **Components:**
  - `whisper-server/` — Whisper ASR server (deployed on **gpu-01**, reached from core-01 via SSH tunnel at `172.18.0.1:8000`)
  - `scribe-api/` — FastAPI, stores transcriptions, integrates with portal (deployed on core-01)

## klai-website (`klai-website/`)
- **Purpose:** Marketing website
- **Stack:** Astro 5, TypeScript strict, Tailwind CSS v4, Keystatic CMS
- **Deploy:** Coolify on public-01

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

## GPU Inference Services (gpu-01 via SSH tunnel)
All accessed from core-01 containers via 172.18.0.1 (Docker host gateway):
| Service | Port | Model | Purpose |
|---------|------|-------|---------|
| TEI | 7997 | BAAI/bge-m3 | Dense embeddings (1024-dim) for knowledge-ingest + retrieval-api |
| Infinity | 7998 | BAAI/bge-reranker-v2-m3 | Cross-encoder reranking for retrieval-api |
| bge-m3-sparse | 8001 | BAAI/bge-m3 | Sparse SPLADE embeddings for knowledge-ingest |
| whisper-server | 8000 | Whisper large-v3 | STT for scribe-api |

## External Platform Services
| Service | Role | URL |
|---------|------|-----|
| Zitadel | SSO/Auth | auth.getklai.com |
| LiteLLM | LLM proxy/routing | internal (http://litellm:4000) |
| LibreChat | AI chat UI (per tenant) | {slug}.getklai.com |
| Qdrant | Vector DB for knowledge | internal |
| Vexa | Meeting bot manager | internal port 8056 |
| Moneybird | Dutch billing/invoicing | moneybird.com API |
| Caddy | Reverse proxy + TLS | core-01 |
| Redis | Cache/queues | internal |
| Meilisearch | Search (shared across tenants) | internal |
| VictoriaMetrics | Metrics/monitoring | internal |
