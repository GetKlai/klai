# Changelog

## [Unreleased] — 2026-04-17 — SPEC-CRAWL-004: Automatic Auth Guard Setup

### Added — SPEC-CRAWL-004: AI-first auth guard in connector wizard

- **Auto-detection during preview:** when a webcrawler preview succeeds with cookies, the system automatically computes a canary fingerprint and uses AI to detect the login indicator element. Admin sees "✓ Auth protection enabled" — no technical config needed.
- **knowledge-ingest/fingerprint.py** (NEW): stdlib-only SimHash reimplementation, compatible with klai-connector's trafilatura version. Zero external deps.
- **knowledge-ingest/selector_ai.py:** added `detect_login_indicator_via_llm()` — identifies logout buttons, user menus, and account dropdowns via LLM DOM analysis.
- **knowledge-ingest/routes/crawl.py:** `CrawlPreviewResponse` extended with `auth_guard` field containing canary URL, fingerprint, and login indicator.
- **klai-connector/routes/fingerprint.py** (NEW): `POST /api/v1/compute-fingerprint` endpoint for manual canary URL changes. Uses `_post_crawl_sync()` shared helper.
- **Portal backend:** `_auto_fill_canary_fingerprint()` on connector create/update — recomputes fingerprint when canary_url set but fingerprint missing. XOR validator relaxed for backend auto-fill flow.
- **Portal frontend:** auth guard confirmation card in preview step with Shield icon + expandable advanced settings for manual override.

### Fixed

- **Semgrep CI:** excluded minified widget JS (`klai-chat.js`) from SAST scan — false positive on Shadow DOM API in pre-built SolidJS bundle.

## [Unreleased] — 2026-04-17 — SPEC-CRAWL-003: Three-Layer Content Quality Guardrails

### Added — SPEC-CRAWL-003: Auth-expiry detection for webcrawler connectors

- **Layer A — Canary fingerprint (pre-sync fail-fast):** Re-crawls a reference page before each sync and compares its SimHash fingerprint to the stored baseline. Similarity < 0.80 aborts the sync immediately with `status=auth_error`, `quality_status=failed`. Prevents contaminated content from reaching Qdrant.
- **Layer B — Per-page login indicator:** CSS selector (`login_indicator_selector`) embedded in Crawl4AI `wait_for` to detect auth-walled pages. Pages that fail the selector are excluded from ingest with a single summary log (no per-page log spam).
- **Layer C — Post-sync boilerplate-ratio metric:** 64-bit SimHash fingerprint per page; greedy centroid clustering (pairwise for ≤200 pages, LSH 8×8 bands for >200). Clusters exceeding 15% of total pages flag `quality_status=degraded`. Minimum 30 pages for statistical validity.
- **`klai-connector/app/services/content_fingerprint.py`** (NEW): Pure-function module with `compute_content_fingerprint()`, `similarity()`, `find_boilerplate_clusters()`, and `ContentFingerprint` NewType.
- **`klai-connector/app/services/events.py`** (NEW): Fire-and-forget product event emission via direct DB write to shared `product_events` table. Resolves Zitadel org_id to `portal_orgs.id` FK.
- **Alembic migration 005**: `quality_status VARCHAR(20)` nullable column on `connector.sync_runs`.
- **Grafana alert**: "Knowledge sync quality degraded" (uid=bfjbxm0h95q0wf) — queries `product_events` for `knowledge.sync_quality_degraded` events.
- **Portal validation**: `WebcrawlerConfig` Pydantic model extended with `canary_url`, `canary_fingerprint`, `login_indicator_selector` + XOR validator.
- **165 tests** across 6 test files; `content_fingerprint.py` at 98% coverage.

### Fixed — Post-deploy bugs found during E2E

- Product event emission used a non-existent HTTP endpoint (`POST /internal/product-events`). Replaced with direct DB write matching portal's own pattern.
- `from app.core.database import session_maker` captured `None` at import time. Fixed to read `database.session_maker` at call time.
- Layer C detail logs had `sample_urls=[]` (lookup key mismatch). Fixed to use cluster URLs directly.
- `wait_for` combined login indicator with `||` syntax (invalid Crawl4AI). Fixed to embed CSS check inside JS arrow function.

### Changed — Code quality improvements

- Extracted `_post_crawl_sync()` helper — single place for POST /crawl plumbing (cookie hooks, auth, payload construction).
- `LAYER_C_MIN_PAGES = 30` as named module-level constant (was inline magic number).
- `ContentFingerprint = NewType("ContentFingerprint", str)` for type safety across adapter and sync engine.
- Log deduplication: event name in message string, queryable data exclusively in `extra={}`.
- Robust `wait_for` matching via `re.match()` instead of brittle `startswith("js:() =>")`.

### Ops

- Cleaned 1115 login-wall boilerplate chunks from Redcactus KB in Qdrant (1124 clean chunks remaining).

## [Unreleased] — 2026-04-16 — SPEC-KB-IMAGE-001: Adapter-owned image URL resolution (refactor)

### Changed

- **`klai-connector/app/adapters/webcrawler.py`**: `_process_results()` resolveert nu relatieve image URLs naar absoluut t.o.v. de pagina-URL, direct bij het ophalen van resultaten. Geen connector-type dispatch meer in `sync_engine` voor webcrawler URLs.
- **`klai-connector/app/adapters/notion.py`**: Verwijderd `_image_cache` side-channel en `get_cached_images()` methode. `fetch_document()` zet `ref.images` nu direct (conform het BaseAdapter contract).
- **`klai-connector/app/adapters/github.py`**: `source_url` is nu de GitHub blob-view URL (`https://github.com/{owner}/{repo}/blob/{branch}/{path}`) voor gebruikerszichtbare citaties. De raw URL (`raw.githubusercontent.com`) wordt alleen intern gebruikt in `fetch_document()` als basis voor het resolven van markdown image URLs. Nieuwe statische helper `_extract_markdown_images()` vult `ref.images` met absolute URLs voor `.md` en `.rst` bestanden.
- **`klai-connector/app/services/sync_engine.py`**: `_extract_and_upload_images()` hernoemd naar `_upload_images()`. Alle connector-type dispatch verwijderd. `text` parameter verwijderd. `extract_markdown_image_urls()` en `resolve_relative_url` imports verwijderd. Parameter `ref` getypt als `DocumentRef` in plaats van `Any`.
- **`klai-connector/app/adapters/base.py`**: Docstrings documenteren nu expliciet het contract: `ImageRef.url` MUST be absolute HTTP(S); `DocumentRef.images` is URL-based only (DOCX/PDF embeds gaan via de parser pipeline, niet via dit veld). Docstrings toegevoegd voor `source_url` en `last_edited` velden op `DocumentRef`.

### Added

- **18 nieuwe unit tests** verdeeld over 4 testbestanden:
  - `tests/adapters/test_github_images.py` (NIEUW) — 9 tests voor markdown URL resolution (relatief, absoluut, dot-slash, branch handling, data URI skipping, leading-slash urljoin semantics)
  - `tests/adapters/test_webcrawler.py::TestImageUrlResolution` — 4 tests voor `_process_results()` absolute URL conversie
  - `tests/adapters/test_notion.py` — 3 tests voor `ref.images` populatie + afwezigheid van legacy `_image_cache`
  - `tests/test_sync_engine_images.py::TestUploadImagesIsConnectorAgnostic` — 2 tests voor connector-agnostic upload

> Dit is een refactor. Er zijn geen nieuwe gebruikerszichtbare features. Extern gedrag is ongewijzigd.

## [Unreleased] — 2026-04-06 — SPEC-KB-026: Taxonomy Integration Hardening

### Fixed — SPEC-KB-026: Taxonomy Integration Hardening (6 bugs)

- **R1+R2 (Critical) — `clustering_tasks.py`**: Fixed `submit_taxonomy_proposal` signature mismatch that caused a `TypeError` on every clustering run — no proposals were ever submitted. Added `cluster_centroid` field to `TaxonomyProposal` dataclass and payload so auto-categorise fires after proposal approval.
- **R3 (Major) — `proposal_generator.py`**: `maybe_generate_proposal()` now calls `generate_node_description()` — node descriptions were always empty.
- **R4 (Major) — portal gap classification**: New `POST /ingest/v1/taxonomy/classify` endpoint in `klai-knowledge-ingest`. Portal's gap classification wired to it in `app/api/internal.py` (was a skeleton that only logged "not yet connected").
- **R5 (Major) — auto-categorise job**: Replaced `asyncio.create_task()` fire-and-forget in `app/api/taxonomy.py` with a Procrastinate background job via `POST /ingest/v1/taxonomy/auto-categorise-job` (stepwise retry: 30 s → 5 m → 30 m).
- **R6 (Medium) — centroid staleness**: `load_centroids()` now rejects files older than 48 h (`taxonomy_centroid_max_age_hours` config). Timezone-safe; treats unparseable timestamps as stale.

### Added — SPEC-KB-026: New endpoints and tests

- `POST /ingest/v1/taxonomy/classify` — classify a gap against the active taxonomy
- `POST /ingest/v1/taxonomy/auto-categorise-job` — enqueue Procrastinate auto-categorise task
- `_StepwiseRetry` Procrastinate task with 30 s / 5 m / 30 m backoff
- `classify_gap_taxonomy()` and `enqueue_auto_categorise()` on `KnowledgeIngestClient`
- 7 new test files covering all fixed bugs

## [Unreleased] — 2026-04-06

### Added — SPEC-KB-023: Taxonomy Discovery — Blind Labeling at Ingest

- **`content_labeler.py`** (new module): `generate_content_label(title, content_preview)` generates 3–5 lowercase keywords describing a document BEFORE any taxonomy context is shown. Uses `klai-fast`, 15 s timeout, returns `[]` on failure (non-fatal).
- **Bias prevention**: label generation runs before `classify_document` so the LLM cannot be anchored by existing taxonomy node names. Enables unbiased category discovery for SPEC-KB-024 clustering.
- **Rate limiting**: shares the existing `_TokenBucketLimiter` / `_RateLimitedTransport` singleton from `taxonomy_classifier.py` (1 req/s). Sequential execution (label first, then classify) means no additional rate-limit config needed.
- **Qdrant storage**: `content_label` stored as keyword array payload on ALL chunks of a document, via both `upsert_chunks` and `upsert_enriched_chunks`. Survives the enrichment pipeline via `extra_payload` passthrough.
- **Qdrant index**: keyword payload index on `content_label` added to `ensure_collection()` alongside existing indexes. Enables scroll filters in SPEC-KB-024 clustering.
- **LLM budget**: 2 calls per document total — `content_label` (blind) + `taxonomy_node_ids`/`tags` classification (anchored). No additional LLM calls introduced.
- **Config**: `content_label_timeout: float = 15.0` in `config.py`.
- **11 unit tests** in `tests/test_content_labeler.py` covering: happy path, timeout/error → `[]`, lowercase, dedup, clamp to 5, 500-char truncation, empty LLM response, non-string filter, `klai-fast` model check, system prompt guard (no taxonomy terms).
- **4 unit tests** in `tests/test_taxonomy_qdrant.py` (`TestUpsertChunksContentLabel`) covering: stored when provided, empty list stored (not omitted), None means absent, all chunks get same label.

## [Unreleased] — 2026-04-05

### Added — SPEC-KB-019: Notion Connector

- **NotionAdapter** (`klai-connector/app/adapters/notion.py`): `BaseAdapter` implementation using `notion_client.AsyncClient`. Supports `list_documents`, `fetch_document`, `get_cursor_state`, and `post_sync`.
- **Incremental sync**: `last_synced_at` cursor state filters pages by `last_edited_time` for efficient delta syncs.
- **Rate limiting**: `asyncio.Semaphore(3)` for 3 req/s Notion API limit with exponential backoff on 429 responses.
- **Config**: `access_token` (required), `database_ids` (optional, newline-separated list for UI), `max_pages` (default 500).
- **AdapterRegistry**: Notion registered as `"notion"` in `klai-connector/app/main.py`.
- **Frontend form** (`$kbSlug_.add-connector.tsx`): 2-step form — credentials (token + database IDs) + settings (assertion modes + max pages). Notion enabled in connector grid.
- **i18n**: 6 new `admin_connectors_notion_*` keys in EN and NL.
- **Credential encryption**: `SENSITIVE_FIELDS["notion"] = ["access_token"]` in `connector_credentials.py` — Notion tokens encrypted at rest via SPEC-KB-020 DEK/KEK hierarchy.
- **9 unit tests** in `klai-connector/tests/adapters/test_notion.py` covering adapter methods, config validation, rate limiting, and access_token security.
- **Note**: `database_ids` is stored and parsed but does not filter Notion API results (notion_client v2 removed `databases.query()`). All workspace-accessible pages sync. Filtering will be added in a future SPEC.

### Added — SPEC-KB-020: Secure Connector Credential Storage

- **AES-256-GCM cipher** (`app/core/security.py`): `AESGCMCipher` class with nonce||ciphertext envelope, random nonce per encryption, authenticated decryption.
- **KEK-DEK hierarchy** (`app/services/connector_credentials.py`): `ConnectorCredentialStore` with per-tenant DEK encrypted by KEK derived from `ENCRYPTION_KEY` env var. `get_or_create_dek` uses `SELECT ... FOR UPDATE` to prevent race conditions.
- **SENSITIVE_FIELDS mapping**: github (`access_token`, `installation_token`, `app_private_key`), notion (`access_token`), google_drive/ms_docs (`oauth_token`, `refresh_token`, `access_token`), web_crawler (`auth_headers`).
- **Schema migration**: `encrypted_credentials BYTEA` on `portal_connectors`, `connector_dek_enc BYTEA` on `portal_orgs` (migration `172c9ab5f151`).
- **API integration**: encrypt on connector create/update, mask sensitive fields in API responses (`app/api/connectors.py`). Internal endpoint (`/internal/connectors/{id}`) decrypts and merges before returning to connector service.
- **Startup guard** (`app/main.py`): hard-fails at startup if `ENCRYPTION_KEY` is missing or not a valid 64-char hex string (REQ-CRYPTO-003).
- **Structlog masking** (`app/logging_setup.py`): `mask_secret_str` processor prevents `SecretStr` values from leaking into log output.
- **Data migration script** (`scripts/migrate_connector_credentials.py`): backfills encrypted credentials for existing connectors.
- **Deploy**: `ENCRYPTION_KEY: ${PORTAL_API_ENCRYPTION_KEY}` added to `deploy/docker-compose.yml` portal-api environment.
- **41 tests** across `test_security.py` (12), `test_log_masking.py` (7), `test_connector_credentials.py` (16), `test_connector_encryption_api.py` (6).
- **New env var required**: `PORTAL_API_ENCRYPTION_KEY` (64-char hex, generate with `openssl rand -hex 32`).

## [Unreleased] — 2026-04-01

### Added — SPEC-AUTH-002: Product Entitlements

- **Plan-to-products mapping** (`app/core/plans.py`): `free` (none), `core` (chat), `professional` (chat, scribe), `complete` (chat, scribe, knowledge). Application-level constant, not stored in DB.
- **Product assignments**: direct per-user assignments via `portal_user_products` table + group-based inheritance via `portal_group_products`. Effective products = union of both.
- **`require_product()` dependency**: FastAPI dependency factory that returns 403 if the user lacks the required product. Applied to `/meetings` (scribe) and `/knowledge` (knowledge) routes.
- **Seat enforcement**: invite endpoint returns 409 Conflict when `active_users >= org.seats`, with `FOR UPDATE` lock to prevent race conditions.
- **Auto-assignment on invite**: new users automatically receive all products included in the org's current plan.
- **Plan change handling**: upgrade makes new products assignable (no auto-enable); downgrade revokes over-ceiling assignments for both user and group products.
- **JWT enrichment endpoint**: `GET /api/internal/users/{id}/products` for Zitadel Action to enrich access tokens with `klai:products` claim. Fail-closed (empty list on error).
- **Admin product API**: `GET/POST/DELETE /api/admin/users/{id}/products`, `GET /api/admin/users/{id}/effective-products`, `GET /api/admin/products`, `GET /api/admin/products/summary`.
- **Migration**: `portal_user_products` table with UNIQUE constraint on `(zitadel_user_id, product)`, index on `(org_id, product)`, and backfill from existing org plans.
- **28 unit tests** covering all 9 SPEC requirements (TS-001 through TS-018).

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
