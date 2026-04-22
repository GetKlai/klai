# Acceptance Criteria — SPEC-KB-IMAGE-002

All scenarios in Gherkin Given/When/Then format, grouped per requirement module. Unless stated otherwise, scenarios run against the feature branch after all six Fase commits are merged to main and both services are deployed.

---

## REQ-KB-IMAGE-002-01 — Shared package structure

### AC-01.1: Package exists and imports cleanly

```gherkin
Given the repository after SPEC-KB-IMAGE-002 lands on main
When a developer runs `uv run python -c "from klai_image_storage import ImageStore, ImageUploadResult, is_valid_image_src, dedupe_image_urls, resolve_relative_url, extract_markdown_image_urls, download_and_upload_crawl_images, download_and_upload_adapter_images"` inside klai-libs/image-storage/
Then the import succeeds with exit code 0
  And all 8 symbols are reachable
```

### AC-01.2: Build object key is bit-for-bit identical to pre-SPEC behaviour

```gherkin
Given the bytes b"PNG\x00example" and the extension "png"
When ImageStore.build_object_key("org-42", "support", b"PNG\x00example", "png") is called
Then the returned key is "org-42/images/support/<sha256-hex-of-bytes>.png"
  And the key matches the key that klai-connector's deleted s3_storage.py produced for the same input (verified via a stored fixture snapshot)
```

### AC-01.3: Public URL prefix unchanged

```gherkin
Given the object key "org-42/images/support/abc.png"
When ImageStore.build_public_url("org-42/images/support/abc.png") is called
Then the return value is "/kb-images/org-42/images/support/abc.png"
```

---

## REQ-KB-IMAGE-002-02 — Consumer wiring

### AC-02.1: Both services declare the path dep

```gherkin
Given klai-connector/pyproject.toml after this SPEC lands
When the [tool.uv.sources] table is inspected
Then it contains an entry for klai-image-storage pointing at "../klai-libs/image-storage" with editable = true

Given klai-knowledge-ingest/pyproject.toml after this SPEC lands
When the [tool.uv.sources] table is inspected
Then it contains the same klai-image-storage entry
```

### AC-02.2: No local image-module imports remain in either service

```gherkin
Given klai-connector/app/ after this SPEC lands
When ripgrep -l "from app.services.(s3_storage|sync_images|image_utils) import" is run
Then the match count is 0

Given klai-knowledge-ingest/knowledge_ingest/ after this SPEC lands
When ripgrep -l "from knowledge_ingest.(s3_storage|sync_images|image_utils) import" is run
Then the match count is 0
```

### AC-02.3: Consumers import from the shared lib

```gherkin
Given klai-connector/app/services/sync_engine.py after this SPEC lands
When the module is inspected
Then it contains a top-level import "from klai_image_storage import ImageStore"
  And it contains a top-level import for download_and_upload_adapter_images (possibly aliased to preserve existing call sites)

Given klai-knowledge-ingest/knowledge_ingest/adapters/crawler.py after this SPEC lands
When the module is inspected
Then it contains a top-level import "from klai_image_storage import ImageStore"
  And it contains a top-level import "from klai_image_storage import download_and_upload_crawl_images"
```

---

## REQ-KB-IMAGE-002-03 — Zero behavioural change

### AC-03.1: github connector sync uploads to same S3 keys

```gherkin
Given a GitHub connector on a test tenant with a README that contains ![logo](./images/logo.png)
  And a control sync was run before the SPEC landed, capturing the public URLs written to Qdrant
When a post-SPEC sync is triggered via the portal UI "Sync now" button
Then the resulting Qdrant chunks carry the exact same public URLs as the control sync
  And no image upload error appears in klai-connector's docker logs
  And the image at /kb-images/<org>/images/<kb>/<sha256>.png resolves with HTTP 200 through Caddy
```

### AC-03.2: notion connector sync unchanged

```gherkin
Given a Notion connector on a test tenant with an image block on one page
  And a control sync was run before the SPEC landed
When a post-SPEC sync is triggered
Then the resulting Qdrant chunks carry the same public URLs as the control sync
  And the image object exists in Garage at the expected key
```

### AC-03.3: web crawler sync via /ingest/v1/crawl/sync unchanged

```gherkin
Given the Voys support web_crawler connector
  And a control sync from before SPEC-KB-IMAGE-002 is retained
When the portal UI "Sync now" is triggered after SPEC-KB-IMAGE-002 lands
Then the resulting Qdrant crawl chunks keep the same image_urls field content
  And every URL resolves via Caddy /kb-images/ with HTTP 200
  And klai-connector performs ZERO S3 writes during this sync (delegation is unchanged)
```

### AC-03.4: Qdrant payload unchanged

```gherkin
Given any Qdrant chunk written after SPEC-KB-IMAGE-002 lands
When the chunk's image_urls payload is inspected
Then the list values still start with "/kb-images/"
  And no schema field was added or removed by this SPEC
```

---

## REQ-KB-IMAGE-002-04 — Deletions

### AC-04.1: Deleted files are absent

```gherkin
Given the repository on main after SPEC-KB-IMAGE-002 lands
When `find` is run for any of:
  - klai-connector/app/services/s3_storage.py
  - klai-connector/app/services/sync_images.py
  - klai-connector/app/services/image_utils.py
  - klai-connector/tests/test_s3_storage.py
  - klai-connector/tests/test_image_utils.py
  - klai-knowledge-ingest/knowledge_ingest/s3_storage.py
  - klai-knowledge-ingest/knowledge_ingest/sync_images.py
  - klai-knowledge-ingest/knowledge_ingest/image_utils.py
  - klai-knowledge-ingest/tests/test_s3_storage.py
  - klai-knowledge-ingest/tests/test_image_utils.py
Then zero results are returned for each of the ten paths
```

### AC-04.2: Integration-layer tests still exist and still pass

```gherkin
Given klai-connector/tests/test_sync_engine_images.py and
      klai-knowledge-ingest/tests/test_crawler_images.py after this SPEC lands
When uv run pytest is invoked on each
Then both files collect successfully (no ImportError)
  And their assertions pass using the klai_image_storage.ImageStore fixture
```

---

## REQ-KB-IMAGE-002-05 — CI + deploy

### AC-05.1: Workflow path filters

```gherkin
Given .github/workflows/klai-connector.yml after this SPEC lands
When the `paths:` filter is read
Then it contains "klai-libs/image-storage/**"

Given .github/workflows/knowledge-ingest.yml after this SPEC lands
When the `paths:` filter is read
Then it contains "klai-libs/image-storage/**"

Given .github/workflows/portal-api.yml after this SPEC lands
When the `paths:` filter is read
Then it does NOT contain "klai-libs/image-storage/**"
```

### AC-05.2: A lib-only change rebuilds both consumer images

```gherkin
Given a follow-up commit that only touches klai-libs/image-storage/
When the commit pushes to main
Then the klai-connector build workflow triggers and completes successfully
  And the knowledge-ingest build workflow triggers and completes successfully
  And the portal-api build workflow does NOT trigger
```

### AC-05.3: Both containers import the shared lib at runtime

```gherkin
Given both containers redeployed after this SPEC lands
When the agent runs `docker exec <container> python -c "import klai_image_storage; print(klai_image_storage.__name__)"` on each
Then both commands print "klai_image_storage"
  And neither command raises ImportError
```

---

## REQ-KB-IMAGE-002-06 — Test coverage

### AC-06.1: Shared-lib suite coverage minimum

```gherkin
Given the shared package on the feature branch
When `uv run pytest -v` is executed inside klai-libs/image-storage/
Then the passing count is >= 40
  And the failing count is 0
```

### AC-06.2: klai-connector regression

```gherkin
Given klai-connector on the feature branch
When `uv run pytest` is executed
Then the failing count is 7 (the pre-existing tests/adapters/test_notion.py failures unchanged vs main)
  And the passing count is >= 230
```

### AC-06.3: knowledge-ingest regression

```gherkin
Given klai-knowledge-ingest on the feature branch
When `uv run pytest --ignore=tests/test_adapters_scribe_chunking.py` is executed
Then the failing count equals the pre-existing baseline (16 on main as of 2026-04-22)
  And the passing count is >= 380
```

### AC-06.4: ruff + pyright clean on every touched file

```gherkin
Given the feature branch
When `uv run ruff check .` is executed in klai-libs/image-storage/, klai-connector/, and klai-knowledge-ingest/
Then each run reports "All checks passed!" or, for the services, only pre-existing warnings unrelated to this SPEC

Given klai-libs/image-storage/ and klai-connector/ (both use pyright strict)
When `uv run pyright` is executed
Then each run reports 0 errors, 0 warnings, 0 informations on the touched files
```

---

## Edge Cases

### EC-1: path-dep works from a clean uv cache

```gherkin
Given a freshly cloned repo
When a developer runs `uv sync --group dev` in klai-connector/ from a clean cache
Then the klai-image-storage wheel builds from the path-dep
  And `uv run python -c "from klai_image_storage import ImageStore"` succeeds
```

### EC-2: running knowledge-ingest tests does not require klai-connector env

```gherkin
Given knowledge-ingest's test suite on the feature branch
When `uv run pytest` is executed without klai-connector installed
Then the shared lib is still discoverable (via path-dep) and tests pass
```

### EC-3: running image-utils tests does not accidentally hit real Garage

```gherkin
Given the shared-lib test suite
When `uv run pytest tests/test_pipeline.py` is executed
Then no real network request is made (httpx.MockTransport fixture is in use)
  And no minio.Minio.put_object is invoked (ImageStore._client is patched)
```

### EC-4: CI correctly fans out to both builds when only the lib changes

```gherkin
Given a PR that only touches klai-libs/image-storage/klai_image_storage/utils.py
When the PR pushes a commit
Then both the klai-connector.yml and knowledge-ingest.yml workflows queue and pass
  And no other service workflow triggers
```

### EC-5: Rollback is a git revert

```gherkin
Given SPEC-KB-IMAGE-002 was merged to main and one container (say knowledge-ingest) exhibits an image-upload regression
When a git revert of the merge commit is pushed to main
Then both services rebuild with the old local-copy behaviour
  And the regression disappears
  And no data migration is required
```

---

## Quality Gate Criteria

| Gate | Threshold | Evidence |
|------|-----------|----------|
| Shared-lib pytest | >= 40 passing, 0 failing | `uv run pytest` in klai-libs/image-storage/ |
| klai-connector regression | >= 230 passing; pre-existing failures == baseline | `uv run pytest` in klai-connector/ |
| knowledge-ingest regression | >= 380 passing; pre-existing failures == baseline | `uv run pytest --ignore=tests/test_adapters_scribe_chunking.py` in klai-knowledge-ingest/ |
| Ruff lint | 0 errors on every touched file | `uv run ruff check` |
| Pyright strict | 0 errors on klai-libs/image-storage + klai-connector | `uv run pyright` |
| Post-deploy import smoketest | Both containers import klai_image_storage without error | `docker exec ... python -c "import klai_image_storage"` |
| Post-deploy github sync regression | S3 keys + public URLs unchanged vs control | Manual portal UI trigger + Qdrant diff |
| Post-deploy notion sync regression | same as above | same |
| Post-deploy web crawler regression | Voys support chunks unchanged on re-sync | same |
