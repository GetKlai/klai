# Implementation Plan — SPEC-KB-IMAGE-002

## Overview

Extract duplicated image-pipeline code into a new `klai-libs/image-storage/`
shared package, following the exact prior-art pattern from SPEC-CRAWLER-004
Fase 0 (`klai-libs/connector-credentials`). Both klai-connector and
knowledge-ingest become thin consumers. No behavioural change, no wire
protocol change, no Qdrant payload change, no S3 key-format change.

The refactor lands in **one feature branch, six focused commits**. Each
commit is independently revertable and passes CI green in isolation.
Deploy order between klai-connector and knowledge-ingest is not material
after the shared-lib commit lands, since they don't exchange a new wire
format.

---

## Reference Implementation Anchors

| Concept | Reference |
|---------|-----------|
| Path-dep shared-lib pattern | `klai-libs/connector-credentials/` (SPEC-CRAWLER-004 Fase 0, commit `5dca107e`) |
| Repo-root Docker build context | `klai-connector/Dockerfile`, `klai-knowledge-ingest/Dockerfile` (SPEC-CRAWLER-004 Fase 0 fix commit `df215164`) |
| Current `ImageStore` reference (to port verbatim) | `klai-connector/app/services/s3_storage.py` |
| Current adapter orchestrator | `klai-connector/app/services/sync_images.py::download_and_upload_images` |
| Current crawl orchestrator | `klai-knowledge-ingest/knowledge_ingest/sync_images.py::download_and_upload_crawl_images` |
| Current image helpers | `klai-connector/app/services/image_utils.py` + identical copy in knowledge-ingest |

---

## Technology Stack

- Python 3.12+ (both consuming services are 3.12; shared lib pins py312)
- `minio>=7.2`, `filetype>=1.2` (pinned same versions as both services)
- `httpx>=0.28` for the orchestrator's download client
- `structlog>=25.0` for uniform JSON logging
- `pytest>=8` + `pytest-asyncio` for tests
- `ruff` + `pyright` with strict mode, matching klai-connector's CI gates

---

## Phase Breakdown

### Fase 1 — Scaffold `klai-libs/image-storage/` + tests (RED→GREEN)

**Goal:** the package exists, publishes the public API, and its own test suite passes in isolation.

**Tasks:**

1. Create `klai-libs/image-storage/pyproject.toml` mirroring `klai-libs/connector-credentials/pyproject.toml`:
   - distribution name `klai-image-storage`, version `0.1.0`
   - dependencies: `cryptography` (no — not needed; remove), `httpx>=0.28`, `structlog>=25.0`, `minio>=7.2`, `filetype>=1.2`
   - `[project.optional-dependencies]` + `[dependency-groups]` dev with pytest, pytest-asyncio, ruff, pyright
   - `[tool.hatch.build.targets.wheel] packages = ["klai_image_storage"]`
   - `[tool.ruff]` + `[tool.pyright]` strict matching klai-connector conventions
   - `[tool.pytest.ini_options] asyncio_mode = "auto"`

2. Create `klai_image_storage/__init__.py` with explicit `__all__` re-exports:

   ```
   from klai_image_storage.storage import ImageStore, ImageUploadResult, MAX_IMAGE_SIZE, MAX_IMAGES_PER_DOCUMENT, PUBLIC_IMAGE_PATH_PREFIX
   from klai_image_storage.utils import is_valid_image_src, dedupe_image_urls, resolve_relative_url, extract_markdown_image_urls
   from klai_image_storage.pipeline import download_and_upload_crawl_images, download_and_upload_adapter_images
   __all__ = [...]
   ```

3. Port `storage.py` verbatim from klai-connector's `s3_storage.py` (chosen as source of truth because klai-connector's version is slightly more mature — e.g. comments on magic-byte validation). Replace `from app.core.logging import get_logger` with `import structlog; logger = structlog.get_logger()` to match knowledge-ingest's logging convention.

4. Port `utils.py` verbatim from either location (they are identical).

5. Port `pipeline.py`:
   - `download_and_upload_crawl_images` — ported verbatim from knowledge-ingest's `sync_images.py`
   - `download_and_upload_adapter_images` — ported verbatim from klai-connector's `sync_images.py` (the version with the optional `parsed_images` parameter that handles Unstructured base64 output — keeps parser path working)

6. Port tests into `klai-libs/image-storage/tests/`:
   - `test_storage.py` = union of klai-connector's and knowledge-ingest's `test_s3_storage.py` (dedupe identical assertions)
   - `test_utils.py` = union of both `test_image_utils.py` files (they are identical — just move once)
   - `test_pipeline.py` = new, covers both orchestrators via `httpx.MockTransport` fixtures

7. Run `uv sync --group dev` in `klai-libs/image-storage/`, then `uv run pytest` — expect ≥ 40 tests green. Run `uv run ruff check` + `uv run pyright`.

**Commit 1 message:** `feat(klai-libs): new image-storage shared package (SPEC-KB-IMAGE-002 Fase 1)`

**Risks:** minor behavioural drift between the two donor implementations. Mitigation: diff them line-by-line before porting; keep the klai-connector version as the base (it's been in production longer); port knowledge-ingest-specific additions (e.g. `filetype` magic-byte list) as additions on top.

### Fase 2 — Wire into klai-connector, rewrite imports, delete locals

**Goal:** klai-connector's `app/services/` no longer contains image modules; all callers import from `klai_image_storage`.

**Tasks:**

1. Add `klai-image-storage` to `klai-connector/pyproject.toml`:

   ```toml
   dependencies = [
       ...,
       "klai-image-storage",
   ]

   [tool.uv.sources]
   klai-image-storage = { path = "../klai-libs/image-storage", editable = true }
   ```

2. `uv lock` in klai-connector.

3. Rewrite imports:
   - `app/services/sync_engine.py`: `from app.services.s3_storage import ImageStore` → `from klai_image_storage import ImageStore`; `from app.services.sync_images import download_and_upload_images` → `from klai_image_storage import download_and_upload_adapter_images as download_and_upload_images` (alias preserves the existing call sites without a behavioural rename; the alias can be removed in a later polish pass once consumers are ready)
   - `app/adapters/github.py`: `from app.services.image_utils import resolve_relative_url` → `from klai_image_storage import resolve_relative_url`
   - `app/clients/knowledge_ingest.py`: `from app.services.image_utils import dedupe_image_urls` → `from klai_image_storage import dedupe_image_urls`

4. Delete:
   - `app/services/s3_storage.py`
   - `app/services/sync_images.py`
   - `app/services/image_utils.py`
   - `tests/test_s3_storage.py`
   - `tests/test_image_utils.py`

5. Run `uv run pytest`. Expect:
   - 7 pre-existing test_notion.py failures unchanged
   - ≥ 230 passing (baseline 237 minus the 7 image_utils tests that moved to shared lib)

6. Run `uv run ruff check app/ tests/` + `uv run pyright`.

**Commit 2 message:** `refactor(connector): consume klai-image-storage, delete local copies (SPEC-KB-IMAGE-002 Fase 2)`

**Risks:**
- Transitive dep drift: minio/filetype no longer listed in klai-connector pyproject after the move (they come in transitively through klai-image-storage). Check: pyright strict and ruff don't complain about missing deps. If the service references minio/filetype symbols at top-level, they must stay in service pyproject.toml. Audit before deleting from pyproject.
- Check the `test_sync_engine_images.py` still works (it was NOT deleted; it tests orchestration and imports ImageStore for fixtures — the import rewrite in Fase 1 covers this).

### Fase 3 — Wire into knowledge-ingest, rewrite imports, delete locals

**Goal:** knowledge-ingest's top-level modules no longer contain image files; all callers import from `klai_image_storage`.

**Tasks:**

1. Add path-dep to `klai-knowledge-ingest/pyproject.toml`.
2. `uv lock` in knowledge-ingest.
3. Rewrite imports:
   - `knowledge_ingest/adapters/crawler.py`: `from knowledge_ingest.s3_storage import ImageStore` → `from klai_image_storage import ImageStore`; `from knowledge_ingest.sync_images import download_and_upload_crawl_images` → `from klai_image_storage import download_and_upload_crawl_images`
4. Delete:
   - `knowledge_ingest/s3_storage.py`
   - `knowledge_ingest/sync_images.py`
   - `knowledge_ingest/image_utils.py`
   - `tests/test_s3_storage.py`
   - `tests/test_image_utils.py`
5. Run `uv run pytest --ignore=tests/test_adapters_scribe_chunking.py`. Expect:
   - Same pre-existing failures as main (16)
   - ≥ 380 passing
6. `uv run ruff check` + (pyright if configured — knowledge-ingest doesn't have it in CI but lint-check the rewritten files).

**Commit 3 message:** `refactor(knowledge-ingest): consume klai-image-storage, delete local copies (SPEC-KB-IMAGE-002 Fase 3)`

### Fase 4 — CI path-filter updates

**Goal:** a change in `klai-libs/image-storage/**` triggers a rebuild of both consumer service images.

**Tasks:**

1. `.github/workflows/klai-connector.yml` — add `klai-libs/image-storage/**` to the `paths:` filter (alongside the existing `klai-libs/connector-credentials/**`).
2. `.github/workflows/knowledge-ingest.yml` — same.
3. `.github/workflows/portal-api.yml` — does NOT need the filter (portal-api does not consume this lib).

**Commit 4 message:** `ci: trigger klai-connector + knowledge-ingest on klai-libs/image-storage changes (SPEC-KB-IMAGE-002 Fase 4)`

### Fase 5 — Deploy + verify

**Goal:** shared lib is imported at runtime on core-01; existing syncs (github, notion, crawl) still produce images at the same public URLs.

**Tasks:**

1. Merge PR to main. Both `klai-connector.yml` and `knowledge-ingest.yml` workflows fire (they both match the `klai-libs/image-storage/**` filter).
2. Watch both GHCR builds complete + deploy SSH step.
3. SSH to core-01 and verify each container imports the new package:
   ```
   docker exec klai-core-klai-connector-1 python -c "from klai_image_storage import ImageStore; print(ImageStore.__module__)"
   # expect: klai_image_storage.storage
   docker exec klai-core-knowledge-ingest-1 python -c "from klai_image_storage import ImageStore; print(ImageStore.__module__)"
   # expect: klai_image_storage.storage
   ```
4. Smoketest:
   - Trigger a github sync on a test tenant repo with README images via portal UI → assert the uploaded objects land at `{org_id}/images/{kb_slug}/{sha256}.{ext}` (same as before).
   - Trigger a re-sync of Voys `support` (web crawl) via portal UI → assert the Qdrant chunks still have `image_urls: ["/kb-images/..."]` and the images are reachable via the frontend.
5. No rollback plan needed beyond `git revert` — the shared lib is behaviourally identical to what was there before.

### Fase 6 — Documentation

**Goal:** the architecture doc reflects the new layout.

**Tasks:**

1. Update `docs/architecture/knowledge-ingest-flow.md` § Part 2 or a new "Shared libraries" subsection to describe:
   - `klai-libs/connector-credentials` for AES-GCM credential encryption
   - `klai-libs/image-storage` for Garage S3 uploads + image URL helpers
   - the path-dep pattern, the `pyproject.toml` convention, the shared test suite
2. Add a pitfall entry in `.claude/rules/klai/projects/knowledge.md` or equivalent: "When you see identical modules in two services, stop and reach for `klai-libs/` before you let them drift."
3. Update SPEC frontmatter to `status: completed` after Fase 5 verification.

**Commit 6 message:** `docs(architecture): shared klai-libs image-storage package (SPEC-KB-IMAGE-002 Fase 6)`

---

## MX Tag Plan

High fan_in targets requiring `@MX:ANCHOR`:

- `klai_image_storage.storage.ImageStore` — cross-service contract for S3 key format + public URL prefix
- `klai_image_storage.pipeline.download_and_upload_adapter_images` — called by klai-connector's sync_engine for every github/notion/drive sync
- `klai_image_storage.pipeline.download_and_upload_crawl_images` — called by knowledge-ingest's crawler

Danger-zone targets requiring `@MX:WARN`:

- Any change to `build_object_key` is a breaking change for existing uploaded images — doc this on the method.

---

## Risk Analysis and Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Shared-lib version skew between klai-connector and knowledge-ingest builds | L | H | Both services pin via `[tool.uv.sources]` path-dep with `editable = true`; `uv.lock` tracks the same repo commit; CI rebuilds both when the lib changes |
| Import rewrites miss a call-site | M | M | Run `ripgrep` for every old module path after the edits; CI's pyright + ruff catch F821 undefined references |
| Test suites inadvertently shrink | L | M | REQ-06 counts are explicit; acceptance checks them per service |
| Runtime regression in prod | L | H | Zero behavioural change by design; smoketest on github + crawl after deploy |
| minio / filetype transitive deps removed from service pyproject cause pyright to flag implicit imports | L | L | Leave minio/filetype as top-level deps in each service pyproject to match existing convention; audit after Fase 2/3 lands |

---

## Estimated Effort

- Fase 1: 1 commit (shared lib + tests) — ~300 LOC lib + ~500 LOC merged tests
- Fase 2: 1 commit (klai-connector wire-in + deletes) — ~50 LOC edits, ~450 LOC deletes
- Fase 3: 1 commit (knowledge-ingest wire-in + deletes) — same shape as Fase 2
- Fase 4: 1 commit (CI path filters) — ~6 lines total
- Fase 5: 0 commits (deploy + verify)
- Fase 6: 1 commit (docs + SPEC close) — ~80 LOC

Total: **5 commits**, one feature branch, one PR.

---

## Open Questions

1. Do we drop `minio` and `filetype` from klai-connector and knowledge-ingest pyproject deps now that they come transitively through `klai-image-storage`, or keep them explicit for auditability? → Default: keep explicit, matches our convention for `cryptography` alongside `klai-connector-credentials`. Re-evaluate in a follow-up.
2. Should the shared lib also publish `ImageRef` (currently in `klai-connector/app/adapters/base.py`)? → No. `ImageRef` is an adapter-contract type; SPEC-KB-IMAGE-003 will revisit whether adapters should still produce it at all. Keeping it in klai-connector is consistent with this SPEC's zero-behaviour-change promise.
3. Will this SPEC coexist with SPEC-CRAWLER-004 Fase F closure? → Yes. Fase F's amended scope (delete only `webcrawler.py` + tests + Layer C dead code) is orthogonal to this SPEC. Land SPEC-CRAWLER-004 Fase F + G first so main is stable, then land this SPEC on top.
