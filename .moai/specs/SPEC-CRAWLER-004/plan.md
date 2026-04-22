# Implementation Plan — SPEC-CRAWLER-004

## Overview

Eight-phase refactor om twee web-crawl pipelines te consolideren tot één, gelocaliseerd
in `knowledge-ingest`. Klai-connector blijft een adapter-framework voor Klasse 1
managed-source integrations (github/notion/google_drive/ms_docs). Shared credential
library zorgt voor tenant-scoped cookie-decryptie binnen knowledge-ingest zelf, zodat
geen plaintext cookies over het netwerk gaan.

Elke fase is een losse commit, onafhankelijk groen in CI, revertable zonder productie-
impact. De bestaande Pipeline A (`/ingest/v1/crawl`) en Pipeline B
(klai-connector's webcrawler adapter) blijven beide werken tot en met Fase D; op Fase
D schakelt de sync_engine om en op Fase F verdwijnt de duplicatie.

---

## Reference Implementation Anchors

| Concept | Reference |
|---------|-----------|
| `AESGCMCipher` AES-256-GCM KEK/DEK pattern | `klai-portal/backend/app/core/security.py` + `app/services/connector_credentials.py` (SPEC-KB-020) |
| Procrastinate task dispatch | `klai-knowledge-ingest/knowledge_ingest/crawl_tasks.py:register_crawl_tasks` |
| Image extraction + S3 upload | `klai-connector/app/services/sync_images.py:download_and_upload_images` + `app/services/s3_storage.py:ImageStore` (SPEC-KB-IMAGE-001) |
| URL-validatie helper | `klai-connector/app/services/image_utils.py:is_valid_image_src,dedupe_image_urls` (commit `28dda391`) |
| Crawl registry + page_links | `klai-knowledge-ingest/knowledge_ingest/adapters/crawler.py:_ingest_crawl_result:160-215` |
| Internal secret middleware | `klai-knowledge-ingest/knowledge_ingest/middleware/internal_secret.py` (via `InternalSecretMiddleware`) |
| Cross-service trace correlation | `klai-portal/backend/app/trace.py:get_trace_headers` |

---

## Technology Stack

- Python 3.13, FastAPI, SQLAlchemy 2.0 async, Procrastinate, asyncpg
- `cryptography` (AESGCM), `httpx`, `structlog`, `pydantic` v2
- Garage S3 via `minio` client (content-addressed storage)
- `filetype` for MIME detection (already in klai-connector, port along with s3_storage)
- Shared lib distribution pattern: editable install via path dependency in each
  service's `pyproject.toml` (zie constraint R-2)

---

## Phase Breakdown

### Fase 0 — Shared connector credentials library

**Goal:** `ConnectorCredentialStore` leeft in één plek, importeerbaar door portal-api,
klai-connector, knowledge-ingest.

**Tasks:**

1. Create `klai-libs/connector-credentials/` with `pyproject.toml` (package name
   `klai-connector-credentials`, version `0.1.0`).
2. Port `AESGCMCipher` from `klai-portal/backend/app/core/security.py` to
   `klai-libs/connector-credentials/connector_credentials/cipher.py`.
3. Port `ConnectorCredentialStore` class from
   `klai-portal/backend/app/services/connector_credentials.py` to
   `klai-libs/connector-credentials/connector_credentials/store.py`, keeping the same
   async DB-session interface.
4. Port `SENSITIVE_FIELDS` constant and extend with web_crawler: `{"web_crawler":
   ["cookies"]}`.
5. Add pytest suite `klai-libs/connector-credentials/tests/test_store.py` with
   encrypt/decrypt round-trip, cross-org DEK isolation, and KEK rotation tests (port
   from `klai-portal/backend/tests/test_connector_credentials.py`).
6. Wire into `klai-portal/backend/pyproject.toml` as path dep; replace
   `app/services/connector_credentials.py` with thin re-export (`from
   connector_credentials.store import ConnectorCredentialStore`). Verify all
   existing portal tests pass.
7. Wire into `klai-connector/pyproject.toml` as path dep. Do NOT import yet.
8. Wire into `klai-knowledge-ingest/pyproject.toml` as path dep. Do NOT import yet.
9. Update `deploy/docker-compose.yml` build context for all three services so the
   shared lib is copied into each image during build.
10. CI check: `ruff check klai-libs/ klai-portal/backend/ klai-connector/
    klai-knowledge-ingest/` — all green.

**Estimated size:** ~400 LOC moved + ~200 LOC new tests + compose/pyproject plumbing.

**Risks:**
- Path-dependency can break Docker build context if Dockerfile COPY scope is wrong.
  Mitigation: test builds locally before push.
- KEK rotation logic in `rotate_kek` bypasses RLS — ensure caller still runs under
  `portal_api` role with `bypassrls=true` for that specific operation.

### Fase A — Image extraction with URL validation in knowledge-ingest

**Goal:** `knowledge-ingest/adapters/crawler.py` extracts and uploads images with the
same correctness as the klai-connector fix landed in commit `28dda391`.

**Tasks:**

1. Create `klai-knowledge-ingest/knowledge_ingest/image_utils.py` with
   `is_valid_image_src`, `dedupe_image_urls`, `resolve_relative_url` (verbatim port
   from klai-connector commit `28dda391`).
2. Create `klai-knowledge-ingest/knowledge_ingest/s3_storage.py` with `ImageStore`
   (content-addressed SHA-256 storage, `filetype`-based MIME detection), porting
   from klai-connector `app/services/s3_storage.py`.
3. Create helper `knowledge_ingest/sync_images.py:download_and_upload_images_for_crawl`
   that wraps the S3 upload flow for the crawl context (accepts the crawl4ai
   `result.media.images` list and a base URL).
4. In `adapters/crawler.py:_ingest_crawl_result`, after the existing link-graph
   block, call the new helper; append the resulting public URLs to `extra[
   "image_urls"]` so enrichment passthrough (SPEC-KB-021 passthrough rule) keeps
   them in Qdrant payload.
5. Add pytest: `tests/test_crawler_images.py` — happy path with valid URLs;
   Cloudflare comma-split srcset fragments are filtered; dedup across srcset
   entries.
6. Add S3 env vars to `knowledge-ingest` service in `docker-compose.yml`
   (`GARAGE_S3_ENDPOINT`, `KB_IMAGES_BUCKET`, bucket credentials via SOPS).

**Estimated size:** ~500 LOC new code + ~300 LOC tests.

**Risks:**
- S3 credentials must be tenant-isolated the same way klai-connector does it.
  Mitigation: port the code bit-for-bit first, optimise later.
- `media.images` field presence varies with crawl4ai version; keep the empty-list
  fallback.

### Fase B — Login_indicator selector (Layer B uitbouw)

**Goal:** Expired cookies fail loudly instead of silently ingesting login pages.

**Tasks:**

1. Extend `run_crawl_job` signature in `crawl_tasks.py` to accept
   `login_indicator_selector: str | None`.
2. In `_ingest_crawl_result`, after page fetch but before hashing: if
   `login_indicator_selector` is set and the selector matches anywhere in
   `result.html`, raise a new `AuthWallDetected` exception.
3. Propagate the exception to `run_crawl_job` which marks the crawl_job row
   `status='failed'` with `error='auth_wall_detected: {selector}'` and halts BFS
   discovery for the remaining URLs.
4. Add pytest: `tests/test_crawler_login_indicator.py` with mocked crawl4ai result
   containing a login indicator + a valid page, asserting the sync fails at the
   login page and no artifacts are upserted for either.
5. Reuse existing canary_url / canary_fingerprint checks; this fase only adds the
   selector check, does not refactor existing Layer B behaviour.

**Estimated size:** ~100 LOC + ~150 LOC tests.

### Fase C — New bulk-sync endpoint

**Goal:** `knowledge-ingest` can receive a full web-crawl config from klai-connector
and orchestrate the Procrastinate task internally.

**Tasks:**

1. Add Pydantic request model `CrawlSyncRequest` in
   `knowledge_ingest/models.py` with: `connector_id: UUID`, `org_id: str`,
   `kb_slug: str`, `base_url: str`, `max_pages: int`, `path_prefix: str | None`,
   `content_selector: str | None`, `canary_url: str | None`,
   `canary_fingerprint: str | None`, `login_indicator: str | None`,
   `max_depth: int = 3`.
2. Add route handler in `routes/crawl.py`:
   `POST /ingest/v1/crawl/sync`. Inside: open DB session → instantiate
   `ConnectorCredentialStore(settings.encryption_key)` →
   `.decrypt_credentials(connector_id, org_id, session)` → extract cookies →
   enqueue `crawl_tasks.run_crawl` with the resolved config → return `{job_id,
   status: "queued"}`. Protected by `InternalSecretMiddleware`.
3. Extend `crawl_tasks.run_crawl` signature to accept the new fields (`cookies`,
   `content_selector`, `canary_url`, `canary_fingerprint`, `login_indicator`) and
   pass them to `run_crawl_job`.
4. Add `POST /ingest/v1/crawl/sync/{job_id}/status` endpoint (also internal) for
   klai-connector to poll progress. Reads from `knowledge.crawl_jobs` table.
5. Add pytest: `tests/test_crawl_sync_endpoint.py` with InternalSecret auth, happy
   path (mocked credential decrypt + mocked procrastinate enqueue), missing
   connector_id (404), wrong secret (401), malformed body (422).
6. Update OpenAPI doc comment strings.

**Estimated size:** ~250 LOC route + ~300 LOC tests.

**Risks:**
- Procrastinate `defer_async` result shape differs from the current
  `ingest_tasks.defer_async` — test the return-value mapping carefully.

### Fase D — Delegation in klai-connector sync_engine

**Goal:** `sync_engine` routes `web_crawler` connector_type to the new endpoint and
stops using `WebCrawlerAdapter` for that code path (adapter still exists, but is
unused after this commit).

**Tasks:**

1. Add `CrawlSyncClient` in
   `klai-connector/app/clients/knowledge_ingest.py`: `async def crawl_sync(self,
   *, connector_id, org_id, kb_slug, config_dict) -> dict`.
2. In `klai-connector/app/services/sync_engine.py`, wrap the main sync loop: if
   `portal_config.connector_type == "web_crawler"`, call `crawl_sync()` with the
   config dict (no cookies — only `connector_id`), map returned `job_id` into
   `sync_run.cursor_state.remote_job_id`, then poll
   `GET /ingest/v1/crawl/sync/{job_id}/status` until completion or timeout (poll
   interval 5s, timeout 30min).
3. On success: close `sync_run` with documents_ok from remote; on timeout or 4xx:
   close with `status=failed, error.details.service='knowledge-ingest'`.
4. Preserve all `product_events` emissions (sync_started / sync_completed /
   sync_failed) so downstream analytics unchanged.
5. Add pytest: `tests/test_sync_engine_web_delegation.py` — mocked CrawlSyncClient
   returning queued → running → completed states; failure cases.
6. Live validation: on a staging deploy, create a `web_crawler` connector in a
   throwaway tenant, verify end-to-end flow (tests/test_sync_engine_webcrawler*
   from the old adapter will still pass — we haven't deleted them yet).

**Estimated size:** ~200 LOC + ~300 LOC tests.

**Deploy order:** Fase C must be live on core-01 before Fase D commit lands.

### Fase E — Smoketest on Voys/support KB + Redcactus

**Goal:** All REQ-05.1-3 acceptance criteria green on real tenants.

**Tasks:**

1. Reset Voys `support` KB (delete artifacts, purge Qdrant points, reset
   `last_sync_at`) — same procedure as during the initial E2E test.
2. Trigger sync via portal UI "Sync now" (hits delegation path from Fase D).
3. Validate Qdrant payload on 10 random chunks: `source_type=crawl`,
   `source_label=help.voys.nl`, `source_domain=help.voys.nl`, `anchor_texts`
   non-empty where appropriate, `chunk_type` in allowed set,
   `incoming_link_count > 0` on `index.md` chunks.
4. Validate `knowledge.crawled_pages` has 20 rows for the support KB; re-run sync
   and confirm `crawl_skipped_unchanged` log per URL.
5. Validate `knowledge.page_links` is populated with > 50 rows.
6. Add Redcactus connector (via shared credentials lib — re-encrypt cookies with
   Voys DEK; cookies already captured in the getklai tenant during 2026-04-22 test).
7. Validate Layer B: intentionally corrupt cookies via DB update → re-trigger
   sync → confirm `sync_run.status == "failed"`, `error_type ==
   "auth_wall_detected"`, no new artifacts.
8. Validate REQ-05.4: grep `docker logs` for both services during the full test;
   assert no plaintext cookie values appear.

**Estimated size:** ~50 LOC validation script in `scripts/e2e-spec-crawler-004.sh`
(optional) + ~1 hour manual validation.

**Rollback point:** if Fase E fails any acceptance check, revert Fase D to fall
back to Pipeline B and investigate without blocking other syncs.

### Fase F — Remove duplicate implementation

**Goal:** No `WebCrawlerAdapter` or crawl-specific code left in klai-connector.

**Tasks:**

1. Delete `klai-connector/app/adapters/webcrawler.py`.
2. Delete `klai-connector/app/services/content_fingerprint.py` (SimHash usage is
   now in knowledge-ingest's `fingerprint.py`).
3. Edit `klai-connector/app/services/image_utils.py`: remove `is_valid_image_src`,
   `dedupe_image_urls`; keep `extract_markdown_image_urls` and `resolve_relative_url`
   (still used by GitHub markdown flow).
4. Edit `klai-connector/app/adapters/base.py`: remove `ImageRef`,
   `DocumentRef.content_fingerprint`, `DocumentRef.images`.
5. Edit `klai-connector/app/adapters/registry.py`: remove `WebCrawlerAdapter`
   registration; dispatch for `web_crawler` now happens before the registry (in
   `sync_engine`'s pre-dispatch fork).
6. Delete `klai-connector/tests/adapters/test_webcrawler.py`,
   `tests/adapters/test_webcrawler_canary.py`,
   `tests/adapters/test_webcrawler_auth.py` (if exists).
7. Clean up GitHub/Drive adapters: verify no remaining imports from removed
   symbols; update `tests/test_image_utils.py` to drop the is_valid_image_src /
   dedupe_image_urls tests (now in knowledge-ingest).
8. Run `ruff check klai-connector/` + `pyright` → zero errors for dangling imports.
9. Run full klai-connector test suite → GitHub/Notion/Drive tests all pass.
10. Deploy klai-connector. Verify `docker logs klai-core-klai-connector-1 | grep -i
    webcrawler` returns zero.

**Estimated size:** ~1200 LOC deleted + ~100 LOC touched.

**Risks:**
- A lingering import of a removed symbol in untested code would land as runtime
  error in prod. Mitigation: pyright strict pass before push, plus CI pyright
  gate.

### Fase G — Documentation + cleanup

**Goal:** Docs match reality; pre-existing test failures meegenomen.

**Tasks:**

1. Update `docs/architecture/knowledge-ingest-flow.md`:
   - § Part 1.2 "External sources via klai-connector" — remove web_crawler as
     adapter; document the delegation flow.
   - § Part 2 Phase 1 Step 1 — clarify that image extraction for crawled content
     happens in knowledge-ingest now.
   - § Part 4 "Tenant provisioning" — note the shared credentials library.
2. Fix pre-existing test-failures discovered during Bug #4 session:
   - `klai-knowledge-ingest/tests/test_crawl_link_fields.py` (3 failures,
     `httpx` monkey-patch attribute lookup).
   - `klai-knowledge-ingest/tests/test_knowledge_fields.py` (2 failures,
     `fact`/`factual` assertion_mode rename).
3. Add retrospective to `.claude/rules/klai/` as a pitfall entry documenting how
   the duplicate pipeline arose (pattern: "a new adapter framework can
   accidentally absorb responsibilities that don't fit its abstraction").
4. Update SPEC status: set `spec.md` frontmatter `status: completed`.

**Estimated size:** ~300 LOC doc updates + 5 test fixes.

---

## MX Tag Plan (Phase 3.5)

High fan_in targets requiring `@MX:ANCHOR`:

- `knowledge_ingest/adapters/crawler.py:_ingest_crawl_result` — now also image
  pipeline entry point
- `knowledge_ingest/routes/crawl.py:crawl_sync` — new internal API boundary
- `klai-connector/app/services/sync_engine.py:run_sync` — delegation fork is a
  behavioural anchor

Danger-zone targets requiring `@MX:WARN`:

- `connector_credentials.store:get_or_create_dek` — `SELECT FOR UPDATE` pattern
  already warned in SPEC-KB-020, reaffirm in shared lib
- `routes/crawl.py:crawl_sync` — `X-Internal-Secret` is the only boundary
  preventing arbitrary crawl calls

TODO markers (removed during implementation):

- `@MX:TODO` in Fase A stub during TDD RED phase on `_ingest_crawl_result` image
  section

---

## Risk Analysis and Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Shared lib path-dep breaks Docker build | M | H | Test all three Dockerfiles locally in Fase 0 before commit |
| Procrastinate task signature bump breaks in-flight jobs | L | H | Add new fields as `Optional` with safe defaults; drain queue before deploy |
| Delegation path has higher latency than direct adapter | M | L | Poll-based tracking adds at most 5s detection delay; acceptable |
| Live cookies get logged by accident during Fase E validation | L | H | Structlog redact filter + REQ-05.4 grep-gate in CI |
| Revert of Fase D leaves DB in split-brain state | L | M | Keep Pipeline B wiring intact until Fase F; `sync_runs` schema unchanged |
| Pyright misses a dangling import in untested code path | L | H | Enable `pyright --strict` on klai-connector for Fase F PR |

---

## Estimated Effort

- Fase 0: 2-3 commits (credential lib + three service wirings)
- Fase A: 1 commit
- Fase B: 1 commit
- Fase C: 1 commit
- Fase D: 1 commit (after Fase C deployed)
- Fase E: validation round, potentially 1 bugfix commit
- Fase F: 1 commit (large deletion)
- Fase G: 1 commit

Total: ~8-10 commits, all revertable independently.

---

## Open Questions (to resolve before Fase D)

1. Poll vs subscribe? Fase D default is polling `/status` every 5s. If
   Procrastinate already emits notify-channel events on task completion, we could
   listen. Decision deferred to Fase C implementation — if event path is trivial,
   use it; else polling is sufficient.
2. Multi-KB simultaneous syncs on the same connector? Not possible currently
   (`sync_runs` unique constraint). Keep the same invariant for delegation.
3. Should `POST /ingest/v1/crawl` (single-URL endpoint) stay around? Yes — the
   preview wizard still uses it. Mark as internal-only; not deprecated.
