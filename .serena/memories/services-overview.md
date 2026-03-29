# Klai Services Overview

## klai-connector
- **Purpose:** Syncs external sources (currently GitHub) to the Klai knowledge base (Qdrant)
- **Port:** 8200
- **Auth:** Internal service-to-service via AuthMiddleware
- **Key components:**
  - `adapters/github.py` — GitHub API adapter
  - `clients/knowledge_ingest.py` — Pushes docs to knowledge ingest endpoint
  - `services/sync_engine.py` — Orchestrates sync runs
  - `services/scheduler.py` — APScheduler-based periodic sync
  - `services/crypto.py` — AES-GCM encryption for stored OAuth secrets
  - `routes/connectors.py` — CRUD for connector configs
  - `routes/sync.py` — Trigger manual syncs

## klai-scribe
- **Purpose:** Meeting/audio transcription
- **Components:**
  - `whisper-server/` — Whisper ASR server
  - `scribe-api/` — FastAPI, stores transcriptions, integrates with portal

## klai-focus
- **Purpose:** Research API (AI-powered research/focus features)
- **Location:** `klai-focus/research-api/`
- **Stack:** FastAPI + Python (same pattern as other services)

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
