# Changelog

## [Unreleased] — 2026-04-01

### Added — SPEC-CRAWLER-003: Link-Graph Retrieval Enrichment

- **Link graph helpers** (`link_graph.py`): four async query functions against `knowledge.page_links` — `get_outbound_urls`, `get_anchor_texts`, `get_incoming_count`, `compute_incoming_counts` — all org- and kb-scoped.
- **Qdrant indexes**: `source_url` (keyword) and `incoming_link_count` (integer) payload indexes added to `klai_knowledge` collection via `ensure_collection()`.
- **Batch link count update** (`qdrant_store.update_link_counts()`): refreshes `incoming_link_count` for all chunks of a URL via `set_payload()`, with semaphore (20 concurrent) and per-call timeout (5 s).
- **Crawl route enrichment**: single-URL ingest populates `source_url`, `links_to` (cap 20), `anchor_texts`, and `incoming_link_count` in `extra_payload` before enrichment task dispatch.
- **Bulk crawler**: batch `update_link_counts()` call after crawl loop to refresh incoming counts for all pages in the KB.
- **Anchor text augmentation** (`enrichment_tasks.py`): appends deduplicated "Also known as: anchor1 | anchor2" block to `enriched_text` (dense + sparse vectors) when anchor texts are available.
- **Retrieval — 1-hop forward expansion** (`retrieve.py`): after RRF merge, outbound URLs from top `link_expand_seed_k` chunks are used to fetch additional candidate chunks via `fetch_chunks_by_urls()`; skipped for notebook scope.
- **Retrieval — authority boost**: `score += link_authority_boost * log(1 + incoming_link_count)` applied to all candidate chunks when `link_authority_boost > 0`.
- **`fetch_chunks_by_urls()`** (`search.py`): payload-filter-based chunk lookup by `source_url` using `client.scroll()` with a 3 s timeout; returns `score=0.0` for reranker scoring.
- **Config** (`retrieval_api/config.py`): five new settings — `link_expand_enabled` (default `True`), `link_expand_seed_k` (10), `link_expand_max_urls` (30), `link_expand_candidates` (20), `link_authority_boost` (0.05).
- **Metrics**: new `step_latency_seconds` label `link_expand` in retrieval-api Prometheus metrics.

## [Unreleased] — 2026-03-27

### Added — SPEC-KB-014: Knowledge Gap Detection & UI

- **LiteLLM hook**: Gap detection in `klai_knowledge.py` — classifies retrieval results as `hard_gap` (zero chunks), `soft_gap` (all chunk scores below threshold), or `success`. Fire-and-forget async reporting via `asyncio.create_task()`.
- **Internal API**: `POST /internal/v1/gap-events` endpoint for service-to-service gap event ingestion (authenticated via `PORTAL_INTERNAL_SECRET`).
- **Database**: New `portal_retrieval_gaps` table with FK to `portal_orgs`, composite indexes on `(org_id, occurred_at)` and `(org_id, query_text)`. 90-day retention policy.
- **Gap API**: `GET /api/app/gaps` (list with filters: days, gap_type, limit) and `GET /api/app/gaps/summary` (aggregated counts: total_7d, hard_7d, soft_7d) — admin-only.
- **KB stats**: Extended `GET /api/app/knowledge-bases/{slug}/stats` to include `org_gap_count_7d`.
- **Gap dashboard** (`/app/gaps`): Table of unanswered questions grouped by query text, with type badge (hard/soft), nearest KB, frequency, and action buttons (navigate to KB or knowledge index). Product-gated to `knowledge` entitlement.
- **Knowledge index card**: Admins see a "Knowledge Gaps" card on `/app/knowledge` with the 7-day gap count and a link to the dashboard.
- **KB detail tile**: "Gaps (7d)" metric tile added to the KB overview tab alongside existing stats.
- **i18n**: 18 new `gaps_*` keys in EN and NL.

### Configuration — SPEC-KB-014

New optional environment variables for the LiteLLM container (safe defaults built in):
- `KLAI_GAP_SOFT_THRESHOLD` (default: `0.4`) — reranker score below which all chunks are classified as low-confidence
- `KLAI_GAP_DENSE_THRESHOLD` (default: `0.35`) — dense score fallback threshold

**Migration required:** Run `alembic upgrade head` on portal-api before restart to create the `portal_retrieval_gaps` table.
