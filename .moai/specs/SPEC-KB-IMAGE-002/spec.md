---
id: SPEC-KB-IMAGE-002
version: "1.1"
status: completed
created: 2026-04-22
updated: 2026-04-22
author: Mark Vletter
priority: high
issue_number: 111
---

## HISTORY

| Version | Date | Author | Change |
|---------|------|--------|--------|
| 1.0 | 2026-04-22 | Mark Vletter | Initial draft — extract duplicated image storage + helpers into `klai-libs/image-storage/` shared package |
| 1.1 | 2026-04-22 | Mark Vletter | Completed — Fase 1-4 + docs landed on main. Shared lib live on both consumer services; 10 local files deleted; CI path filters fan out to both builds on a lib-only change. Zero behavioural change confirmed via regression baseline on both services. |

---

# SPEC-KB-IMAGE-002: Shared `klai-libs/image-storage` package to eliminate image-pipeline code duplication across klai-connector and knowledge-ingest

## Context

After SPEC-CRAWLER-004 Fase A landed the image pipeline in knowledge-ingest (to support the consolidated web-crawl flow), the repository now carries **two near-identical copies** of the same image-storage code:

- `klai-connector/app/services/s3_storage.py` vs `klai-knowledge-ingest/knowledge_ingest/s3_storage.py`
- `klai-connector/app/services/image_utils.py` vs `klai-knowledge-ingest/knowledge_ingest/image_utils.py`
- `klai-connector/app/services/sync_images.py` vs `klai-knowledge-ingest/knowledge_ingest/sync_images.py`

Both services write to the same Garage bucket, use the same content-addressed key format (`{org_id}/images/{kb_slug}/{sha256}.{ext}`), and serve images via the same public URL prefix (`/kb-images/`). The `research.md` audit confirms the logic is ≈98 % identical — the only difference is the input shape (crawl4ai `media.images` dicts vs adapter `ref.images` tuples vs Unstructured base64 `parsed_images`).

This duplication:
- Doubles the test surface (`tests/test_s3_storage.py`, `tests/test_image_utils.py` in both repos; overlapping orchestration tests)
- Doubles the maintenance cost of future Garage/S3 contract changes
- Lets the two copies drift (e.g. one may add a field the other never picks up)
- Violates the single-source-of-truth principle we already established for credentials via `klai-libs/connector-credentials` in SPEC-CRAWLER-004 Fase 0

### Why not a bigger refactor

During SPEC-CRAWLER-004 Fase F impact analysis we considered moving the klai-connector URL-image path entirely to knowledge-ingest. That is a larger behavioural change with real adapter-rewrite risk and is deferred to a possible follow-up SPEC-KB-IMAGE-003. This SPEC is the **strictly-smaller, zero-behaviour-change prerequisite** that unlocks that conversation.

### Goal

Extract a single `klai-libs/image-storage` package. Both klai-connector and knowledge-ingest depend on it via the same `[tool.uv.sources]` path-dep pattern we use for `klai-connector-credentials`. Both services delete their duplicated files and tests. No Garage bucket, wire protocol, public URL, ref.images contract, or Qdrant payload changes.

---

## Scope

### In scope

1. New package `klai-libs/image-storage/` containing:
   - `klai_image_storage/storage.py` — `ImageStore`, `ImageUploadResult`, `MAX_IMAGE_SIZE`, `MAX_IMAGES_PER_DOCUMENT`, `PUBLIC_IMAGE_PATH_PREFIX`
   - `klai_image_storage/utils.py` — `is_valid_image_src`, `dedupe_image_urls`, `resolve_relative_url`, `extract_markdown_image_urls`
   - `klai_image_storage/pipeline.py` — two orchestrators:
     - `download_and_upload_crawl_images(media_images, base_url, org_id, kb_slug, image_store, http_client)` (crawl4ai media dicts → public URLs)
     - `download_and_upload_adapter_images(image_urls, org_id, kb_slug, image_store, http_client, parsed_images=None)` (adapter URL tuples + optional base64 parsed_images → public URLs)
   - `tests/` — unified test suite merging the non-overlapping cases from both services' existing tests
2. Path-dep wiring:
   - `klai-connector/pyproject.toml` adds `klai-image-storage` under `[tool.uv.sources]`
   - `klai-knowledge-ingest/pyproject.toml` adds `klai-image-storage` under `[tool.uv.sources]`
3. Import rewrites:
   - `klai-connector/app/services/sync_engine.py::_upload_images` calls `klai_image_storage.pipeline.download_and_upload_adapter_images`
   - `klai-connector/app/adapters/github.py` imports `resolve_relative_url` from `klai_image_storage.utils` (still produces `ImageRef(url=...)` locally — `ImageRef` stays in `app/adapters/base.py` since the adapter contract is unchanged)
   - `klai-connector/app/adapters/notion.py` same
   - `klai-connector/app/clients/knowledge_ingest.py` imports `dedupe_image_urls` from `klai_image_storage.utils`
   - `klai-knowledge-ingest/knowledge_ingest/adapters/crawler.py` imports `ImageStore` from `klai_image_storage.storage` and `download_and_upload_crawl_images` from `klai_image_storage.pipeline`
4. File deletions (after imports are updated and tests pass):
   - `klai-connector/app/services/s3_storage.py`
   - `klai-connector/app/services/sync_images.py`
   - `klai-connector/app/services/image_utils.py` (the whole file — all four functions move to the lib; no non-image callers of this module)
   - `klai-connector/tests/test_s3_storage.py`
   - `klai-connector/tests/test_image_utils.py`
   - `klai-knowledge-ingest/knowledge_ingest/s3_storage.py`
   - `klai-knowledge-ingest/knowledge_ingest/sync_images.py`
   - `klai-knowledge-ingest/knowledge_ingest/image_utils.py`
   - `klai-knowledge-ingest/tests/test_s3_storage.py`
   - `klai-knowledge-ingest/tests/test_image_utils.py`
5. Dep list cleanup:
   - `minio` and `filetype` stay in BOTH services' `pyproject.toml` as top-level deps — they are also transitive through `klai-image-storage` but declaring them explicitly in each service matches our convention for other transitive deps (e.g. `cryptography` declared in both `klai-portal/backend` and `klai-knowledge-ingest` even though it's transitive through `klai-connector-credentials`).
6. CI workflow path-filter updates:
   - `.github/workflows/klai-connector.yml` adds `klai-libs/image-storage/**` to `paths`
   - `.github/workflows/knowledge-ingest.yml` adds same
   - `.github/workflows/portal-api.yml` does NOT add it (portal-api does not consume the lib)
7. Docker build contexts stay unchanged — they already build from repo root since SPEC-CRAWLER-004 Fase 0.

### Out of scope (What NOT to Build)

- No behavioural change: same bucket, same key format, same public URL, same MAX sizes, same validation rules.
- No change to `ref.images` / `DocumentRef.images` / `ImageRef` — the adapter contract is unchanged (that is SPEC-KB-IMAGE-003 territory if ever pursued).
- No change to the wire protocol between klai-connector and knowledge-ingest (the `extra.image_urls` field in ingest payload stays as-is for adapter uploads).
- No change to the Qdrant payload — `image_urls` in chunk payload remains a list of public URLs.
- No elimination of klai-connector's Garage env vars (those stay; klai-connector still uploads for github/notion/parser paths).
- No move of Unstructured parser or base64 parsed-images handling.
- No migration of existing uploaded images — they keep their current public URLs because the key format is preserved bit-for-bit.
- No test coverage loss — every assertion from the two existing test suites has an equivalent in the merged shared-lib suite.

---

## Requirements (EARS)

### REQ-KB-IMAGE-002-01 — Shared package structure

**REQ-01.1 (Ubiquitous).** The repository shall contain a package `klai-libs/image-storage/` with `pyproject.toml` declaring a distribution name `klai-image-storage` and an importable top-level package `klai_image_storage`.

**REQ-01.2 (Ubiquitous).** The package shall re-export the public API via `klai_image_storage.__init__` so consumers write `from klai_image_storage import ImageStore, ImageUploadResult, download_and_upload_crawl_images, download_and_upload_adapter_images, is_valid_image_src, dedupe_image_urls, resolve_relative_url, extract_markdown_image_urls`.

**REQ-01.3 (Ubiquitous).** `ImageStore.build_object_key` shall produce keys bit-for-bit identical to the current klai-connector and knowledge-ingest implementations: `{org_id}/images/{kb_slug}/{sha256}.{ext}` with extension lower-cased and leading dot stripped.

**REQ-01.4 (Ubiquitous).** `ImageStore.build_public_url` shall return `/kb-images/{object_key}`, matching SPEC-KB-IMAGE-001's Caddy reverse-proxy contract.

### REQ-KB-IMAGE-002-02 — Consumer wiring

**REQ-02.1 (Ubiquitous).** `klai-connector/pyproject.toml` and `klai-knowledge-ingest/pyproject.toml` shall each declare `klai-image-storage` as a dependency under `[tool.uv.sources]` using the `{ path = "../klai-libs/image-storage", editable = true }` pattern.

**REQ-02.2 (Ubiquitous).** The `uv.lock` of both services shall pin the same `klai-image-storage` source commit after extraction.

**REQ-02.3 (Ubiquitous).** After this SPEC lands, `ripgrep "from app.services.(s3_storage|image_utils|sync_images) import"` under `klai-connector/app/` returns zero matches. Likewise `ripgrep "from knowledge_ingest.(s3_storage|image_utils|sync_images) import"` under `klai-knowledge-ingest/knowledge_ingest/` returns zero matches.

**REQ-02.4 (Ubiquitous).** All production call-sites import from `klai_image_storage.*`.

### REQ-KB-IMAGE-002-03 — Zero behavioural change

**REQ-03.1 (Ubiquitous).** After this SPEC lands, a github connector sync on a KB with README markdown images shall upload the exact same files to the exact same S3 keys with the exact same public URLs as before.

**REQ-03.2 (Ubiquitous).** After this SPEC lands, a notion connector sync on a page with image blocks shall upload the exact same files to the exact same S3 keys with the exact same public URLs as before.

**REQ-03.3 (Ubiquitous).** After this SPEC lands, a web crawler sync via `/ingest/v1/crawl/sync` on Voys `support` shall upload the exact same files to the exact same S3 keys with the exact same public URLs as before.

**REQ-03.4 (Ubiquitous).** After this SPEC lands, the Qdrant `image_urls` payload field on any chunk shall resolve to a reachable image via Caddy `/kb-images/...`.

### REQ-KB-IMAGE-002-04 — Deletions

**REQ-04.1 (Ubiquitous).** After this SPEC lands, the following files shall not exist in the repository:
- `klai-connector/app/services/s3_storage.py`
- `klai-connector/app/services/sync_images.py`
- `klai-connector/app/services/image_utils.py`
- `klai-connector/tests/test_s3_storage.py`
- `klai-connector/tests/test_image_utils.py`
- `klai-knowledge-ingest/knowledge_ingest/s3_storage.py`
- `klai-knowledge-ingest/knowledge_ingest/sync_images.py`
- `klai-knowledge-ingest/knowledge_ingest/image_utils.py`
- `klai-knowledge-ingest/tests/test_s3_storage.py`
- `klai-knowledge-ingest/tests/test_image_utils.py`

**REQ-04.2 (Ubiquitous).** `klai-connector/tests/test_sync_engine_images.py` and `klai-knowledge-ingest/tests/test_crawler_images.py` shall continue to exist as INTEGRATION tests that exercise the service-specific orchestration (sync_engine loop for the former, `_ingest_crawl_result` call for the latter). They import `ImageStore` from `klai_image_storage` for fixture wiring.

### REQ-KB-IMAGE-002-05 — CI + deploy

**REQ-05.1 (Ubiquitous).** The `klai-connector` GitHub Actions workflow shall trigger on changes in `klai-libs/image-storage/**` (via the `paths:` filter).

**REQ-05.2 (Ubiquitous).** The `knowledge-ingest` GitHub Actions workflow shall trigger on changes in `klai-libs/image-storage/**`.

**REQ-05.3 (Ubiquitous).** After the shared-lib extraction commit lands on main, both services' images shall be rebuilt and redeployed. The rollout order is not material — both containers can run the old code while the other runs the new code, since no wire protocol changes.

### REQ-KB-IMAGE-002-06 — Test coverage

**REQ-06.1 (Ubiquitous).** The shared-lib test suite `klai-libs/image-storage/tests/` shall cover every assertion previously present in the five deleted test files, including:
- Cloudflare srcset-debris rejection (`quality=90`, `fit=scale-down`, `w=1920`)
- Markdown image extraction with empty alt + data-URI skip
- Relative URL resolution including root-relative, dot-relative, parent-traversal
- Deduplication preserving first-seen order
- Content-addressed key format with extension normalisation
- Public URL prefix
- `validate_image` for PNG / JPEG / GIF / WebP magic-bytes + SVG text-header + rejection of plain text + rejection of empty bytes
- `upload_image` happy path with `put_object` call + dedup short-circuit on `stat_object` hit

**REQ-06.2 (Ubiquitous).** `uv run pytest` in `klai-libs/image-storage/` reports ≥ 40 passing tests and 0 failing.

**REQ-06.3 (Ubiquitous).** `uv run pytest` in `klai-connector/` reports ≥ 230 passing (baseline 237 minus the 7 deleted image_utils tests that migrated to the shared lib, expected landing around 230; the pre-existing 7 test_notion.py failures stay) and the same pre-existing failures as main.

**REQ-06.4 (Ubiquitous).** `uv run pytest --ignore=tests/test_adapters_scribe_chunking.py` in `klai-knowledge-ingest/` reports ≥ 380 passing (baseline 402 minus the ~20 deleted image_utils + s3_storage tests that migrated) and the same pre-existing failures as main.

---

## Affected Files

### klai-libs (new)

- `klai-libs/image-storage/pyproject.toml` (new)
- `klai-libs/image-storage/README.md` (new)
- `klai-libs/image-storage/klai_image_storage/__init__.py` (new — re-exports)
- `klai-libs/image-storage/klai_image_storage/storage.py` (new — `ImageStore`, constants)
- `klai-libs/image-storage/klai_image_storage/utils.py` (new — image URL helpers)
- `klai-libs/image-storage/klai_image_storage/pipeline.py` (new — two orchestrators)
- `klai-libs/image-storage/tests/__init__.py` (new)
- `klai-libs/image-storage/tests/conftest.py` (new)
- `klai-libs/image-storage/tests/test_storage.py` (new — port of `test_s3_storage.py`)
- `klai-libs/image-storage/tests/test_utils.py` (new — port of `test_image_utils.py`)
- `klai-libs/image-storage/tests/test_pipeline.py` (new — covers both crawl and adapter orchestrators)

### klai-connector (refactor)

- `app/services/sync_engine.py` — import `ImageStore` + `download_and_upload_adapter_images` from `klai_image_storage`
- `app/adapters/github.py` — import `resolve_relative_url` from `klai_image_storage.utils`
- `app/adapters/notion.py` — no import rewrite needed (did not import from these modules directly; uses `ImageRef` from `app/adapters/base.py`)
- `app/clients/knowledge_ingest.py` — import `dedupe_image_urls` from `klai_image_storage.utils`
- `pyproject.toml` — add `klai-image-storage` path-dep
- `uv.lock` — regenerated
- DELETE: `app/services/s3_storage.py`, `app/services/sync_images.py`, `app/services/image_utils.py`, `tests/test_s3_storage.py`, `tests/test_image_utils.py`

### klai-knowledge-ingest (refactor)

- `knowledge_ingest/adapters/crawler.py` — import `ImageStore` + `download_and_upload_crawl_images` from `klai_image_storage`
- `pyproject.toml` — add `klai-image-storage` path-dep
- `uv.lock` — regenerated
- DELETE: `knowledge_ingest/s3_storage.py`, `knowledge_ingest/sync_images.py`, `knowledge_ingest/image_utils.py`, `tests/test_s3_storage.py`, `tests/test_image_utils.py`

### CI

- `.github/workflows/klai-connector.yml` — add `klai-libs/image-storage/**` to `paths`
- `.github/workflows/knowledge-ingest.yml` — add same

---

## Delta Markers (brownfield)

### [DELTA] klai-libs (new)

- [NEW] `klai-libs/image-storage/` package matching SPEC-CRAWLER-004 Fase 0 pattern

### [DELTA] klai-connector

- [EXISTING] `app/services/s3_storage.py` + `sync_images.py` + `image_utils.py` — behaviour identical to incoming shared-lib versions
- [MODIFY] import rewrites in `sync_engine.py`, `github.py`, `clients/knowledge_ingest.py`
- [REMOVE] three local service files + two local test files
- [NEW] path-dep in `pyproject.toml`

### [DELTA] klai-knowledge-ingest

- [EXISTING] `knowledge_ingest/s3_storage.py` + `sync_images.py` + `image_utils.py`
- [MODIFY] import rewrite in `adapters/crawler.py`
- [REMOVE] three local module files + two local test files
- [NEW] path-dep in `pyproject.toml`

---

## Acceptance Summary

Full Gherkin scenarios in `acceptance.md`. Key gates:

1. Shared-lib pytest suite passes (≥ 40 tests).
2. klai-connector pytest regression passes (≥ 230 passing; pre-existing failures unchanged).
3. knowledge-ingest pytest regression passes (≥ 380 passing; pre-existing failures unchanged).
4. Ruff + pyright strict clean on every touched file.
5. `ripgrep "from app.services.s3_storage"` / `"from knowledge_ingest.s3_storage"` / `"from app.services.sync_images"` / `"from knowledge_ingest.sync_images"` / `"from app.services.image_utils"` / `"from knowledge_ingest.image_utils"` returns zero matches in the respective service directories.
6. `docker exec klai-core-klai-connector-1 python -c "from klai_image_storage import ImageStore; print('OK')"` prints `OK` after deploy.
7. `docker exec klai-core-knowledge-ingest-1 python -c "from klai_image_storage import ImageStore; print('OK')"` prints `OK` after deploy.
8. Post-deploy regression: trigger a github sync on a test tenant's repo with README images; assert images land at the same S3 keys as a control sync performed before extraction.
9. Post-deploy regression: trigger a web-crawler sync on Voys `support`; assert Qdrant chunks keep `/kb-images/...` URLs pointing at reachable objects.

---

## References

- SPEC-CRAWLER-004 (pipeline consolidation that surfaced this duplication; Fase 0's `klai-libs/connector-credentials` is the prior art for path-dep shared libs)
- SPEC-KB-IMAGE-001 (adapter-owned image URL resolution + content-addressed S3 key contract)
- `research.md` in this SPEC directory for the duplication audit
- Future SPEC-KB-IMAGE-003 (optional) — remove klai-connector's S3 dependency entirely by reworking the adapter contract; this SPEC is its prerequisite
