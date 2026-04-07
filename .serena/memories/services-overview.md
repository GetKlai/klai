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
- **Purpose:** Syncs external sources (currently GitHub, Notion) to the Klai knowledge base (Qdrant)
- **Port:** 8200
- **Auth:** Internal service-to-service via AuthMiddleware
- **Key components:**
  - `adapters/github.py` — GitHub API adapter
  - `adapters/notion.py` — Notion API adapter (uses notion_client v2 + notion-sync-lib)
  - `clients/knowledge_ingest.py` — Pushes docs to knowledge ingest endpoint; includes `delete_connector()` for per-connector cleanup
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

## Notion adapter (klai-connector) — key behaviors
- **notion_client v2:** `databases.query()` is removed. All pages retrieved via `client.search()`. `database_ids` filtering is post-fetch in Python (compare `parent.database_id`).
- **notion_client timeout:** Pass `client=httpx.Client(timeout=httpx.Timeout(30.0))` as a separate constructor param — `ClientOptions` does not accept `timeout`.
- **`page_ids` config:** When set, fetches specific pages via `pages.retrieve()` — skips search entirely. Use for targeted ingestion.
- **`max_pages`:** Real page count limit (not API pagination call limit).
- **`fetch_blocks_recursive`** (notion-sync-lib): fetches ALL nested blocks for one Notion page → produces 1 artifact.
- **Incremental cursor reset:** If data was cleaned and 0 docs are fetched, the cursor `last_synced_at` may still be set. Reset via: `UPDATE connector.sync_runs SET cursor_state = NULL WHERE connector_id = ?`
- **FileType detection on Notion titles:** `Path("3.011 Opzeggingen...").suffix` returns `".011 Opzeggingen..."` (non-empty, has space, long). Guard: `if not suffix or " " in suffix or len(suffix) > 10` before trusting the suffix.

## DELETE /ingest/v1/connector endpoint (knowledge-ingest)
Performs per-connector recursive cleanup: Qdrant points + PG artifacts + FalkorDB episodes.
Called by portal-api connector delete flow via `knowledge_ingest_client.delete_connector()` before the DB record is removed.
FalkorDB cleanup uses `delete_kb_episodes(org_id, episode_ids)` from `knowledge_ingest/graph.py`.

## Source citation pipeline — known gap
`source_ref` (Notion UUID / source URL) is stored in Qdrant payload and PG `artifacts.extra` but is NOT propagated to the chat/research UI. The break points are:
1. `retrieval-api ChunkResult` — no `source_ref`, `source_connector_id`, or `source_url` fields
2. `research-api _to_chunk()` — always sets `metadata = {}`, discards all source context
3. Frontend `Citation` type — no `url` or `source_ref` field
Fix requires changes at all 5 layers to enable clickable source links in citations.
