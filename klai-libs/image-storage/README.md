# klai-image-storage

Shared content-addressed image storage for Klai services.

## Purpose

`klai-connector` (connector sync engine) and `klai-knowledge-ingest` (crawl
pipeline) both need to upload extracted images to the Garage S3 cluster and
serve them publicly via Caddy at `/kb-images/{object_key}`. Before
SPEC-KB-IMAGE-002 both services shipped ≈98%-identical copies of the
`ImageStore` client, URL helpers, and download-and-upload orchestrators.
This package consolidates them so only one implementation exists.

## Public API

```python
from klai_image_storage import (
    ImageStore,
    ImageUploadResult,
    MAX_IMAGE_SIZE,
    MAX_IMAGES_PER_DOCUMENT,
    PUBLIC_IMAGE_PATH_PREFIX,
    dedupe_image_urls,
    download_and_upload_adapter_images,
    download_and_upload_crawl_images,
    extract_markdown_image_urls,
    is_valid_image_src,
    resolve_relative_url,
)
```

- `ImageStore` — async S3 client wrapping the synchronous `minio` SDK with
  `asyncio.to_thread`. Content-addressed (SHA-256 of bytes) for free
  deduplication. Keys look like `{org_id}/images/{kb_slug}/{sha256}.{ext}`;
  public URLs look like `/kb-images/{object_key}`.
- `download_and_upload_adapter_images` — orchestrator used by the connector
  sync engine. Accepts markdown-extracted `(alt, url)` tuples plus
  optional base64-encoded parser images (Unstructured).
- `download_and_upload_crawl_images` — orchestrator used by the web-crawl
  pipeline. Accepts a crawl4ai `media.images` list of dicts.
- `is_valid_image_src`, `resolve_relative_url`, `dedupe_image_urls`,
  `extract_markdown_image_urls` — URL hygiene helpers.

## Wiring

Consumers register the path dependency in `pyproject.toml`:

```toml
dependencies = [
    "klai-image-storage",
]

[tool.uv.sources]
klai-image-storage = { path = "../klai-libs/image-storage", editable = true }
```

## Invariants

- `ImageStore.build_object_key` and `ImageStore.build_public_url` are
  content-addressed contracts. Changing either breaks the public URL of
  every previously uploaded image.
- `MAX_IMAGE_SIZE = 5 MB`, `MAX_IMAGES_PER_DOCUMENT = 20`.
- All logging uses `structlog.get_logger()` with structured key/value
  kwargs.

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check
uv run pyright
```
