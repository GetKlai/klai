# Implementation Plan: SPEC-KB-IMAGE-001

## Task Decomposition

### Task 1: Garage Infrastructure Setup
**Files:** `deploy/docker-compose.yml`, `deploy/garage/` (new)
**Effort:** Small

1. Garage service toevoegen aan docker-compose.yml
   - Image: `dxflrs/garage:v2.2.0`
   - Volumes: `/opt/klai/garage-meta:/var/lib/garage/meta`, `/opt/klai/garage-data:/var/lib/garage/data`
   - Config: `deploy/garage/garage.toml` (single-node, `replication_factor = 1`, `db_engine = "lmdb"`, `s3_region = "garage"`)
   - Networks: `klai-net`
   - Health check op S3 API endpoint (port 3900)
2. Init-script (`deploy/garage/init-garage.sh`): wacht op Garage startup, assign layout, apply layout, create bucket, create access key
   - Draait als aparte `garage-init` service in docker-compose met `depends_on: garage`
   - Slaat access key/secret op als environment variable of in een shared volume
3. Environment variables: `GARAGE_S3_ENDPOINT`, `GARAGE_ACCESS_KEY`, `GARAGE_SECRET_KEY`, `GARAGE_BUCKET`, `GARAGE_REGION=garage`
4. Garage admin token en RPC secret via SOPS-encrypted .env

### Task 2: S3 Client Utility
**Files:** `klai-connector/app/services/s3_storage.py` (new)
**Effort:** Small

1. `minio` SDK (sync) met `asyncio.to_thread()` wrapper
2. Functions:
   - `async upload_image(org_id, kb_slug, image_bytes, ext) -> str` (returns presigned URL)
   - `async image_exists(org_id, kb_slug, content_hash) -> bool` (deduplicatie check)
   - `async get_presigned_url(object_key, ttl) -> str`
3. Content-addressed naming: SHA256 hash van image bytes
4. Presigned URL generatie met configureerbare TTL (default 7 dagen)
5. Deduplicatie: `stat_object` check of hash al bestaat, skip upload als zo
6. `filetype` library voor magic bytes validatie voordat image wordt geupload
7. Minio client configuratie: `region="garage"`, `secure=False` (intern Docker netwerk)

### Task 3: Pipeline Plumbing — Data Model Changes
**Files:**
- `klai-connector/app/adapters/base.py` — DocumentRef uitbreiden
- `klai-connector/app/clients/knowledge_ingest.py` — image_urls parameter
- `klai-knowledge-ingest/knowledge_ingest/models.py` — IngestRequest uitbreiden
- `klai-knowledge-ingest/knowledge_ingest/routes/ingest.py` — extra_payload flow
- `klai-knowledge-ingest/knowledge_ingest/qdrant_store.py` — optional index
**Effort:** Medium

1. `ImageRef` dataclass: `url: str`, `alt: str | None`, `source_path: str`
2. `DocumentRef.images: list[ImageRef] | None = None`
3. `IngestRequest.image_urls: list[str] | None = None`
4. `extra_payload["image_urls"] = image_urls` in ingest route (VOOR defer_async)
5. Optional Qdrant payload index op `image_urls`

### Task 4: GitHub Adapter — Image Extractie
**Files:** `klai-connector/app/adapters/github.py`
**Effort:** Medium

1. Regex utility: `extract_markdown_image_urls(content: str) -> list[tuple[str, str]]` (alt, url)
2. Na text parse: extraheer image URLs uit markdown content
3. Relative URL resolution: pad relatief aan repo root
4. Image bytes ophalen via GitHub raw content API
5. Upload naar Garage, verzamel presigned URLs
6. Return in DocumentRef.images

### Task 5: Notion Adapter — Image Extractie
**Files:** `klai-connector/app/adapters/notion.py`
**Effort:** Medium

1. Helper: `extract_image_blocks(blocks: list[dict]) -> list[ImageRef]`
2. Recursief blocks doorlopen, `type == "image"` detecteren
3. External images: download via URL
4. File images: download via Notion API expiring URL
5. Upload naar Garage, verzamel presigned URLs

### Task 6: Web Crawler Adapter — Image Extractie
**Files:** `klai-connector/app/adapters/webcrawler.py`
**Effort:** Small

1. Hergebruik `extract_markdown_image_urls()` utility uit Task 4
2. Relative URL resolution t.o.v. pagina-URL
3. Filter: skip data: URIs, skip externe domeinen optioneel
4. Download images, upload naar Garage

### Task 7: Parser — Unstructured Image Elements
**Files:** `klai-connector/app/services/parser.py`
**Effort:** Small

1. Bij PDF/DOCX partition: detecteer `Image` element types
2. Extraheer image metadata (base64 data of file reference)
3. Return als apart resultaat naast de text

### Task 8: Sync Engine Integration
**Files:** `klai-connector/app/services/sync_engine.py`
**Effort:** Medium

1. Na adapter fetch + parse: verzamel images
2. Per image: download (als nog niet gedownload), upload naar Garage
3. Graceful error handling: try/except per image, log failures
4. Max 20 images per document, max 5MB per image
5. Pass `image_urls` lijst naar ingest client

### Task 9: Retrieval API Response
**Files:** `klai-retrieval-api/retrieval_api/api/retrieve.py`
**Effort:** Small

1. ChunkResult uitbreiden met `image_urls: list[str] | None`
2. Bij Qdrant query: `image_urls` uit payload halen
3. Meesturen in HTTP response

## Dependencies

```
Task 1 (Garage) ─────────┐
Task 2 (S3 Client) ──────┤
                          ├── Task 8 (Sync Engine) ── Task 9 (Retrieval)
Task 3 (Plumbing) ───────┤
Task 4 (GitHub) ──────────┤
Task 5 (Notion) ──────────┤
Task 6 (Web Crawler) ─────┤
Task 7 (Parser) ──────────┘
```

Tasks 1-7 zijn grotendeels parallel; Task 8 integreert alles; Task 9 is de laatste stap.

## Technology Choices

| Component | Keuze | Versie | Rationale |
|-----------|-------|--------|-----------|
| Object storage | Garage | v2.2.0 (`dxflrs/garage:v2.2.0`) | EU non-profit (Deuxfleurs, FR), lichtgewicht Rust binary, S3-compatible, betrouwbare presigned URLs, MinIO is EOL |
| S3 client | `minio` + `asyncio.to_thread()` | 7.x | Lichtgewicht, werkt met alle S3-compatible stores, geen aiohttp dependency (codebase gebruikt httpx) |
| Image validatie | `filetype` | 1.2.0 | Magic bytes check, puur Python, geen C deps, valideert JPEG/PNG/GIF/WebP/SVG |
| Image hashing | `hashlib` SHA256 | stdlib | Content-addressed storage, deduplicatie |
| URL signing | `minio.presigned_get_object()` | — | Standaard S3 presigned URL via minio SDK |
| Markdown regex | `re` stdlib | — | `!\[([^\]]*)\]\(([^)]+)\)` — simpel, betrouwbaar |

### Onderzochte en afgewezen alternatieven

| Component | Afgewezen optie | Reden |
|-----------|----------------|-------|
| Object storage | SeaweedFS | Historische presigned URL bugs (SigV4 regressies in v3.85-3.87) |
| Object storage | MinIO | OSS repo gearchiveerd dec 2025, geen security patches meer |
| Object storage | RustFS | Alpha (v1.0.0-alpha.90), niet production-ready |
| Object storage | Ceph RGW | Overkill (min 3 nodes, 64 GB RAM per node) |
| S3 client | `boto3` | Sync, zware dependency tree, overkill voor put/get/presign |
| S3 client | `aioboto3` | Dependency hell (patcht botocore internals, exact gepinde versies) |
| S3 client | `miniopy-async` | Trekt `aiohttp` mee (codebase is httpx), bekende presigned URL bug |
| S3 client | httpx + SigV4 | Te veel boilerplate, foutgevoelig, DIY presigned URLs |
| Image validatie | Pillow | Te zwaar (30 MB, C extension) voor alleen format check |

## Risk Mitigation

| Risk | Mitigatie |
|------|----------|
| Notion URL expiry | Download en re-upload naar Garage bij sync, niet later |
| Procrastinate drops image_urls | Include in extra_payload VOOR defer_async (expliciet in Task 3) |
| Image failures blokkeren ingest | try/except per image in Task 8, document gaat door |
| Cross-tenant image leakage | org_id in S3 path + Qdrant org_id filter |
| Garage niet beschikbaar | Feature flag: `ENABLE_IMAGE_STORAGE=true/false` |
