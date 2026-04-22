# SPEC-KB-IMAGE-002 — Compact

Extract duplicated image-pipeline code (`ImageStore`, image utils, upload orchestrators) into a new `klai-libs/image-storage/` shared package. Both klai-connector and knowledge-ingest consume it via `[tool.uv.sources]` path-dep — the exact prior-art pattern from SPEC-CRAWLER-004 Fase 0's `klai-libs/connector-credentials`.

## Problem (one line)

`ImageStore` + `image_utils` + `sync_images` exist as ≈98%-identical copies in `klai-connector/app/services/` and `klai-knowledge-ingest/knowledge_ingest/`. Same Garage bucket, same content-addressed keys, same `/kb-images/` URL prefix. Two code bases, one behaviour. Duplication risks drift.

## Goal

One shared package. Zero behavioural change. Zero wire-protocol change. Zero Qdrant payload change. Zero S3 key-format change.

## Requirements

### REQ-01 — Shared package structure

- **01.1**: Package `klai-libs/image-storage/` with distribution name `klai-image-storage` and importable top-level `klai_image_storage`.
- **01.2**: Re-exports the public API — `ImageStore, ImageUploadResult, is_valid_image_src, dedupe_image_urls, resolve_relative_url, extract_markdown_image_urls, download_and_upload_crawl_images, download_and_upload_adapter_images`.
- **01.3**: `ImageStore.build_object_key` produces bit-identical keys to the current implementations.
- **01.4**: `ImageStore.build_public_url` returns `/kb-images/{object_key}`.

### REQ-02 — Consumer wiring

- **02.1**: Both services declare `klai-image-storage` via `[tool.uv.sources]` path-dep.
- **02.2**: `uv.lock` of both services pins the same source commit.
- **02.3**: Zero local imports of the deleted modules remain in either service.
- **02.4**: All production call-sites import from `klai_image_storage.*`.

### REQ-03 — Zero behavioural change

- **03.1**: github connector sync uploads to the same S3 keys.
- **03.2**: notion connector sync upload unchanged.
- **03.3**: web crawler sync via `/ingest/v1/crawl/sync` unchanged.
- **03.4**: Qdrant `image_urls` payload still uses `/kb-images/...`.

### REQ-04 — Deletions

- **04.1**: 10 files removed: `s3_storage.py`, `sync_images.py`, `image_utils.py`, `test_s3_storage.py`, `test_image_utils.py` in both services.
- **04.2**: `test_sync_engine_images.py` (klai-connector) and `test_crawler_images.py` (knowledge-ingest) stay as integration tests, importing `ImageStore` from `klai_image_storage`.

### REQ-05 — CI + deploy

- **05.1**: klai-connector workflow `paths:` includes `klai-libs/image-storage/**`.
- **05.2**: knowledge-ingest workflow same.
- **05.3**: Lib-only change rebuilds both consumer images; portal-api does not.

### REQ-06 — Test coverage

- **06.1**: Shared-lib pytest ≥ 40 passing, 0 failing.
- **06.2**: klai-connector pytest ≥ 230 passing, pre-existing failures unchanged.
- **06.3**: knowledge-ingest pytest ≥ 380 passing, pre-existing failures unchanged.
- **06.4**: Ruff + pyright strict clean on every touched file.

## Acceptance (key scenarios)

Full Gherkin in `acceptance.md`. Summary:

- AC-01.1 every re-exported symbol imports cleanly
- AC-01.2 S3 key format bit-identical to pre-SPEC behaviour
- AC-02.2 ripgrep finds zero `from app.services.(s3_storage|sync_images|image_utils)` in klai-connector/app/ and zero `from knowledge_ingest.(s3_storage|sync_images|image_utils)` in knowledge-ingest/
- AC-03.1-3 github/notion/crawl syncs produce bit-identical public URLs vs control syncs captured before the SPEC
- AC-04.1 the 10 deleted files do not exist on main
- AC-05.2 a lib-only commit rebuilds BOTH services
- AC-06.1-4 test + lint gates green
- EC-1 path-dep resolves from a clean uv cache
- EC-5 git-revert of the merge commit is a complete rollback

## Files

### klai-libs (new)

`klai-libs/image-storage/` with `pyproject.toml`, `klai_image_storage/{__init__.py, storage.py, utils.py, pipeline.py}`, and `tests/{conftest.py, test_storage.py, test_utils.py, test_pipeline.py}`.

### klai-connector (refactor)

- `app/services/sync_engine.py` — rewritten imports
- `app/adapters/github.py` — import `resolve_relative_url` from shared lib
- `app/clients/knowledge_ingest.py` — import `dedupe_image_urls` from shared lib
- `pyproject.toml` + `uv.lock` — path-dep added
- DELETE: `app/services/s3_storage.py`, `app/services/sync_images.py`, `app/services/image_utils.py`, `tests/test_s3_storage.py`, `tests/test_image_utils.py`

### klai-knowledge-ingest (refactor)

- `knowledge_ingest/adapters/crawler.py` — rewritten imports
- `pyproject.toml` + `uv.lock` — path-dep added
- DELETE: `knowledge_ingest/s3_storage.py`, `knowledge_ingest/sync_images.py`, `knowledge_ingest/image_utils.py`, `tests/test_s3_storage.py`, `tests/test_image_utils.py`

### CI

- `.github/workflows/klai-connector.yml` — `paths:` filter adds `klai-libs/image-storage/**`
- `.github/workflows/knowledge-ingest.yml` — same

## Exclusions (What NOT to Build)

- NO behavioural change (same bucket, key, URL, MAX sizes, validation).
- NO `ref.images`/`DocumentRef.images`/`ImageRef` changes — adapter contract stays.
- NO wire-protocol change between klai-connector and knowledge-ingest.
- NO Qdrant payload change.
- NO elimination of klai-connector's Garage env vars — those stay because github/notion/parser paths still upload.
- NO Unstructured parser migration — that is SPEC-KB-IMAGE-003 territory if ever pursued.
- NO image-data migration — existing public URLs keep resolving bit-for-bit.

## Constraints

- Five independently-revertable commits (one per fase 1-4 + one docs/close).
- Works even with a clean `uv` cache.
- Matches SPEC-CRAWLER-004 Fase 0 path-dep convention exactly — no new infra.
- Ruff + pyright strict clean on every touched file.
- Zero new network flows or secrets.

## References

- SPEC-CRAWLER-004 Fase 0 (prior art: `klai-libs/connector-credentials`)
- SPEC-KB-IMAGE-001 (adapter-owned image URL resolution + key format contract)
- `research.md` in this SPEC directory (duplication audit)
- Possible follow-up SPEC-KB-IMAGE-003 (eliminate klai-connector's S3 dependency by reworking adapter contract)
