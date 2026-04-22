# SPEC-CRAWLER-004 — Compact

Web-crawl pipeline consolidation in knowledge-ingest; remove duplicate in
klai-connector; shared credentials library.

## Requirements

### REQ-CRAWLER-004-01 — Shared credentials lib

- **01.1**: Provide `klai-libs/connector-credentials/` with
  `ConnectorCredentialStore` using AES-256-GCM (`AESGCMCipher`).
- **01.2**: Importable by klai-portal/backend, klai-connector, knowledge-ingest
  via one declared dep — no version drift.
- **01.3**: When knowledge-ingest needs cookies, it loads the connector row,
  decrypts via shared lib in-process. Plaintext cookies never leave service
  boundary.
- **01.4**: Missing or invalid `ENCRYPTION_KEY` → startup fails with clear
  error.
- **01.5**: Ships with pytest suite for round-trip, cross-org isolation, KEK
  rotation.

### REQ-CRAWLER-004-02 — Crawl pipeline feature parity

- **02.1**: `knowledge-ingest/adapters/crawler.py:_ingest_crawl_result` extracts
  images from `media.images` with `is_valid_image_src` filter (rejects
  `quality=90`, `fit=scale-down`) and `dedupe_image_urls`.
- **02.2**: Images uploaded to content-addressed Garage/S3 at
  `{org_id}/images/{kb_slug}/{sha256}.{ext}`.
- **02.3**: When `login_indicator` selector is set and matches a crawled page,
  sync fails with `error_type="auth_wall_detected"`; no artifacts upserted.
- **02.4**: HTTP 4xx/5xx on image download → log warning, continue other
  images; no retry loop.
- **02.5**: Qdrant payload contains `source_label`, `source_type`,
  `source_domain`, `anchor_texts`, `incoming_link_count`, `links_to`,
  `chunk_type` for every chunk.

### REQ-CRAWLER-004-03 — Bulk-sync endpoint + delegation

- **03.1**: `POST /ingest/v1/crawl/sync` protected by `InternalSecretMiddleware`
  accepts `{connector_id, org_id, kb_slug, base_url, max_pages, path_prefix,
  content_selector, canary_url, canary_fingerprint, login_indicator}`.
- **03.2**: Endpoint loads cookies via `ConnectorCredentialStore.decrypt_credentials()`,
  enqueues Procrastinate `run_crawl` task, returns `{job_id, status: "queued"}`
  within 500 ms.
- **03.3**: `klai-connector/sync_engine.py` for `web_crawler`: bypass adapter,
  POST connector config (no cookies, only `connector_id`) to endpoint, map
  `job_id` to `sync_runs.cursor_state.remote_job_id`.
- **03.4**: klai-connector keeps owning `sync_runs` state + `product_events`;
  polls `GET /ingest/v1/crawl/sync/{job_id}/status` for lifecycle.
- **03.5**: If endpoint unreachable or non-2xx → `sync_run.status="failed"`,
  `error.details.service="knowledge-ingest"`; no auto-retry.

### REQ-CRAWLER-004-04 — Remove duplicate

- **04.1**: No `webcrawler.py` / `content_fingerprint.py` under
  `klai-connector/app/`; no `WebCrawlerAdapter` anywhere in klai-connector.
- **04.2**: `base.py` has no `ImageRef`, no `DocumentRef.images`, no
  `DocumentRef.content_fingerprint`.
- **04.3**: `registry.py` routes `web_crawler` directly to delegation, not via
  `BaseAdapter`.
- **04.4**: Delete `tests/adapters/test_webcrawler*.py`; update shared-helper
  tests to match smaller surface area.
- **04.5**: Dangling imports of removed symbols fail CI via ruff/pyright, not
  at runtime.

### REQ-CRAWLER-004-05 — Validation + docs

- **05.1**: Voys support re-sync via new endpoint → every chunk has
  `source_type="crawl"`, `source_label="help.voys.nl"`, non-empty
  `anchor_texts`, `chunk_type` in allowed set; `index.md` chunks have
  `incoming_link_count > 0`.
- **05.2**: `knowledge.crawled_pages` has 20 rows for support KB;
  `knowledge.page_links` has internal link graph; re-sync triggers
  `crawl_skipped_unchanged` for every URL.
- **05.3**: Redcactus sync with corrupted cookies → `status=failed`,
  `error_type=auth_wall_detected`, no artifacts. Valid cookies → chunks have
  `source_label="wiki.redcactus.cloud"`.
- **05.4**: Log grep during full sync → zero plaintext cookie occurrences.
- **05.5**: `docs/architecture/knowledge-ingest-flow.md` § Part 1.2, § Part 2,
  § Part 4 updated before Fase G closes.

## Acceptance scenarios (key)

See `acceptance.md` for full Given/When/Then. Summary:

- AC-01.1 round-trip encryption of cookies
- AC-01.2 cross-org DEK isolation (auth tag mismatch)
- AC-02.1 Cloudflare srcset debris filtered from image list
- AC-02.3 login_indicator triggers hard fail with `auth_wall_detected`
- AC-02.4 complete Qdrant payload for all web-crawler chunks
- AC-03.1 endpoint returns 202 in < 500 ms, queues Procrastinate task
- AC-03.4 delegation: klai-connector sends ONE POST, stores job_id, polls /status
- AC-04.1 grep `WebCrawlerAdapter` in klai-connector returns zero
- AC-05.1 Voys support smoketest — all dimensions green
- AC-05.2 dual-hash dedup engages on no-op re-sync
- AC-05.4 Redcactus login_indicator guard works with corrupted cookies
- AC-05.5 no plaintext cookies in any log line during sync

Quality gates:
- ≥ 85% unit coverage on new modules
- 0 ruff/pyright errors on touched files
- Full regression suite green on klai-portal, klai-connector, knowledge-ingest

## Files to modify

### klai-libs (new)
`klai-libs/connector-credentials/` (pyproject.toml, `connector_credentials/
{__init__.py, store.py, cipher.py}`, `tests/test_store.py`).

### klai-portal/backend (refactor)
`app/services/connector_credentials.py` (thin re-export), `app/core/security.py`
(move to shared lib), `pyproject.toml`.

### klai-knowledge-ingest (additions, Fases A-C)
- `knowledge_ingest/adapters/crawler.py` (image extraction + login_indicator)
- `knowledge_ingest/image_utils.py` (new, ports from klai-connector)
- `knowledge_ingest/s3_storage.py` (new, ports from klai-connector)
- `knowledge_ingest/sync_images.py` (new helper)
- `knowledge_ingest/routes/crawl.py` (new `POST /ingest/v1/crawl/sync` + status)
- `knowledge_ingest/crawl_tasks.py` (accept cookies, login_indicator)
- `knowledge_ingest/models.py` (CrawlSyncRequest)
- `pyproject.toml` (shared lib, `filetype`)
- `tests/test_crawler_images.py`, `tests/test_crawler_login_indicator.py`,
  `tests/test_crawl_sync_endpoint.py`

### klai-connector (delegation + deletion)
- `app/services/sync_engine.py` (pre-dispatch fork for web_crawler)
- `app/clients/knowledge_ingest.py` (add `crawl_sync` + `crawl_status` methods)
- `pyproject.toml` (shared lib dep)
- DELETE Fase F: `app/adapters/webcrawler.py`, `app/services/content_fingerprint.py`,
  parts of `app/services/image_utils.py`, `ImageRef` + `DocumentRef.images` +
  `DocumentRef.content_fingerprint` from `app/adapters/base.py`, tests
  `tests/adapters/test_webcrawler*.py`

### docs (Fase G)
`docs/architecture/knowledge-ingest-flow.md` (§ 1.2, § 2, § 4).

### Pre-existing test fixes (Fase G)
`klai-knowledge-ingest/tests/test_crawl_link_fields.py`, `tests/test_knowledge_fields.py`.

## Exclusions (What NOT to Build)

- NO changes to portal UI connector-wizard — UI flow stays identical.
- NO changes to GitHub/Notion/Drive/MS-Docs adapters — remain Klasse 1.
- NO changes to scribe push-flow.
- NO data migration of `portal_orgs.connector_dek_enc` or
  `portal_connectors.encrypted_credentials` rows — only code moves.
- NO new Qdrant collections; `klai_knowledge` stays.
- NO Procrastinate queue config changes — reuse existing queues.
- NO new connector_type enum values — `web_crawler` stays.
- NO deprecation of `POST /ingest/v1/crawl` (single-URL, used by preview wizard).

## Constraints

- Each fase independently committable and CI-green.
- No breaking changes for existing web_crawler connectors during Fases A–E.
- All commits revertable without production impact.
- Credentials never in plaintext over service boundaries.
- Graphiti/FalkorDB + Qdrant schema unchanged.

## References

- SPEC-CRAWL-002/003/004 (content quality layers, cookie auth, auth guard)
- SPEC-CRAWLER-002/003 (crawl registry, link graph retrieval)
- SPEC-KB-IMAGE-001, SPEC-KB-020, SPEC-KB-021
- Commits `28dda391`, `b1abd3e9`
- `docs/architecture/knowledge-ingest-flow.md` § Part 1.2, Part 2
