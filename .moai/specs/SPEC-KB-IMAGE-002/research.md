# Research — SPEC-KB-IMAGE-002

## Audit scope

Identify every location where image-storage / image-util / image-upload code lives today, quantify the duplication, and confirm that a shared library extraction is safe (identical behaviour, same data contract) before proposing the SPEC.

## Findings

### Duplicated modules

| Symbol | klai-connector | knowledge-ingest | Divergence |
|---|---|---|---|
| `ImageStore` class | `app/services/s3_storage.py` | `knowledge_ingest/s3_storage.py` | Near-identical — same minio client, same `put_object` semantics, same content-addressed SHA-256 key, same `/kb-images/{org_id}/images/{kb_slug}/{hash}.{ext}` public URL prefix, same `validate_image` magic-byte + SVG text check, same MAX_IMAGE_SIZE + MAX_IMAGES_PER_DOCUMENT constants |
| `is_valid_image_src` | `app/services/image_utils.py` | `knowledge_ingest/image_utils.py` | Identical (Cloudflare srcset-debris guard ported verbatim in SPEC-CRAWLER-004 Fase A) |
| `dedupe_image_urls` | `app/services/image_utils.py` | `knowledge_ingest/image_utils.py` | Identical |
| `extract_markdown_image_urls` | `app/services/image_utils.py` | `knowledge_ingest/image_utils.py` | Identical |
| `resolve_relative_url` | `app/services/image_utils.py` | `knowledge_ingest/image_utils.py` | Identical |
| download+upload orchestration | `app/services/sync_images.py::download_and_upload_images` | `knowledge_ingest/sync_images.py::download_and_upload_crawl_images` | ≈95% identical — same MAX_IMAGE_SIZE/PER_DOC constants, same partial-failure logging, same magic-byte validation via ImageStore.validate_image. Only differences: input shape (crawl helper takes `media_images: list[dict]` from crawl4ai; klai-connector helper takes `image_urls: list[tuple[str,str]]` from markdown extraction + optional `parsed_images: list[{data_b64, mime_type}]` from Unstructured) |

### Callers that exercise each path today

**klai-connector (all three image paths):**

- GitHub adapter (`app/adapters/github.py:209`) — calls `_extract_markdown_images` which instantiates `ImageRef(url=resolve_relative_url(...), alt=alt, source_path="")`. Sets `ref.images`.
- Notion adapter (`app/adapters/notion.py:301`) — collects image blocks via `_extract_image_blocks`, sets `ref.images`.
- Sync engine (`app/services/sync_engine.py:734`) — iterates `ref.images` + `parsed_images` and calls `download_and_upload_images` → `ImageStore.upload_image`.
- `app/clients/knowledge_ingest.py:59` — calls `dedupe_image_urls` on the resulting public URLs before sending to knowledge-ingest as `extra.image_urls`.

**knowledge-ingest (one image path — the crawl):**

- Crawl adapter (`knowledge_ingest/adapters/crawler.py:234-240`) — reads `result.media.images` from crawl4ai, calls `download_and_upload_crawl_images` which uses its local `ImageStore` + `image_utils` + filters via `is_valid_image_src` + dedupes via `dedupe_image_urls`.

Both services write to the **same Garage bucket** (`klai-images` by default) via the **same S3 API endpoint** using the **same content-addressed keys**. The images uploaded by either container are interchangeable — the frontend cannot tell them apart, and the public URL format is bit-for-bit identical.

### Test duplication

| klai-connector | knowledge-ingest | Notes |
|---|---|---|
| `tests/test_s3_storage.py` | `tests/test_s3_storage.py` | Same assertions on build_object_key, build_public_url, validate_image, upload_image |
| `tests/test_image_utils.py` | `tests/test_image_utils.py` | Same Cloudflare srcset cases, same dedup preserves-order cases |
| `tests/test_sync_engine_images.py` | `tests/test_crawler_images.py` | Different orchestration input shape (ref.images vs media.images) but same validation guarantees |

Total: ~600 LOC of duplicated tests across both repos.

### Deploy context

Since SPEC-CRAWLER-004 Fase 0's build-context fix, all three Dockerfiles build from the repo root (`klai-portal/backend`, `klai-connector`, `klai-knowledge-ingest`). They already have `klai-libs/` in their build context for the existing `klai-connector-credentials` path-dep. A second path-dep (`klai-image-storage`) lands with zero additional Docker or CI plumbing besides a path-filter line in each of the three service workflow files.

### Behaviour that MUST be preserved bit-for-bit

- Content-addressed S3 key: `{org_id}/images/{kb_slug}/{sha256}.{ext}` (SPEC-KB-IMAGE-001 contract)
- Public URL format: `/kb-images/{object_key}` (Caddy reverse-proxy contract)
- Deduplication semantics: same bytes → same key → skip upload, return same public URL
- Magic-byte MIME validation (`filetype` library) with SVG text-header fallback
- MAX_IMAGE_SIZE = 5 MiB
- MAX_IMAGES_PER_DOCUMENT = 20
- Graceful degradation: HTTP 4xx/5xx on a single image logs a warning, ingest continues

### Risks of extracting now vs later

- Klai-connector uses `minio` client directly; knowledge-ingest also. Both pinned to `>=7.2`. Compatible.
- Klai-connector uses `filetype>=1.2`; knowledge-ingest also. Compatible.
- Neither service uses any custom subclass of `ImageStore` or imports "private" attributes from the other. Safe to extract as-is.
- The shared lib can target `py>=3.12` to satisfy both services (knowledge-ingest is 3.12, klai-connector is 3.12, klai-portal/backend would inherit via transitive dep if it ever needs images — not today).

### Why this SPEC and not "move everything to knowledge-ingest"

During SPEC-CRAWLER-004 Fase F impact analysis we considered moving klai-connector's URL-image path entirely to knowledge-ingest (github/notion adapters rewrite their output so knowledge-ingest extracts+uploads). That option requires:

- Rewriting github + notion adapters to inline absolute image URLs in markdown
- Extending knowledge-ingest's ingest_document to extract images from markdown for `source_type=="connector"`
- Moving Unstructured parser OR transporting base64 parsed images over the wire (Pipeline B problem)
- Eliminating klai-connector's S3 dependency entirely (security goal)

That is a much larger change with real behavioural risk for stable github/notion syncs. The shared-library extraction proposed in this SPEC is a **strictly-smaller, zero-behaviour-change prerequisite** that eliminates the duplication without touching any adapter, any S3 contract, or any ingest protocol. Once the shared lib is in place, a follow-up (potentially SPEC-KB-IMAGE-003) can remove klai-connector's S3 dependency by reworking the adapter contract — that is a separable decision the team can take later.

### Decision

Extract a new `klai-libs/image-storage` package containing `ImageStore`, `image_utils`, and a unified `download_and_upload_images` helper supporting both the crawl input shape (media_images dicts) and the adapter input shape (image URL tuples + optional base64 parsed_images). Both klai-connector and knowledge-ingest import from the shared package. Delete the duplicated files and tests. No behaviour change.
