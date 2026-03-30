# Klai Services Overview

## Portal (klai-portal/)
- **Backend:** `klai-portal/backend/` — FastAPI, SQLAlchemy async, Alembic, PostgreSQL. Port 8010 on core-01.
- **Frontend:** `klai-portal/frontend/` — React 19 + Vite + TanStack Router + Mantine 8 + Paraglide i18n

## klai-scribe (`klai-scribe/`)
- **Purpose:** Meeting/audio transcription
- **Components:**
  - `whisper-server/` — Whisper ASR server
  - `scribe-api/` — FastAPI, stores transcriptions, integrates with portal

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

## External Platform Services
| Service | Role | URL |
|---------|------|-----|
| Zitadel | SSO/Auth | auth.getklai.com |
| LiteLLM | LLM proxy/routing | internal |
| LibreChat | AI chat UI (per tenant) | {slug}.getklai.com |
| Qdrant | Vector DB for knowledge | internal |
| Vexa | Meeting bot manager | internal port 8056 |
| Moneybird | Dutch billing/invoicing | moneybird.com API |
| Caddy | Reverse proxy + TLS | core-01 |
| Redis | Cache/queues | internal |
| Meilisearch | Search (shared across tenants) | internal |
| VictoriaMetrics | Metrics/monitoring | internal |
