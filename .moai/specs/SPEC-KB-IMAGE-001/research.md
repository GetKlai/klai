# Research: SPEC-KB-IMAGE-001 — Image Storage in Connector Pipeline

## 1. Current Architecture

### Document Processing Pipeline (Text-Only)

Flow: External Source -> Adapter -> Parser -> Knowledge-Ingest -> Qdrant + PostgreSQL

**All binary data is discarded in the parser.**

- `klai-connector/app/services/parser.py:46-52` — Unstructured.io `partition()` returns Element objects including Image elements, but only `str(e)` is used, which discards image binary data
- `klai-connector/app/services/sync_engine.py:187-216` — Orchestrates fetch -> parse -> ingest, no image handling
- `klai-connector/app/clients/knowledge_ingest.py:25-77` — HTTP payload to knowledge-ingest contains only `content` (text string)

### Image Handling per Connector

#### GitHub Adapter (`klai-connector/app/adapters/github.py`)
- Line 38: `SUPPORTED_TYPES` excludes image extensions (`.png`, `.jpg`, etc.)
- Line 145: Files not in SUPPORTED_TYPES are skipped entirely
- Markdown image references (`![alt](path)`) preserved as text but images not fetched
- Images embedded in PDFs/DOCX are discarded by parser

#### Notion Adapter (`klai-connector/app/adapters/notion.py`)
- Lines 222-313: Uses `notion_sync.fetch_blocks_recursive` + `extract_block_text`
- Image blocks return empty string or alt text only — no URL extraction
- Notion image block structure: `block.image.external.url` or `block.image.file.url`
- File-based images (not URL) require fetch from Notion API

#### Web Crawler Adapter (`klai-connector/app/adapters/webcrawler.py`)
- Lines 237-309: Crawl4AI returns markdown with `![alt](url)` syntax
- Image URLs are embedded in markdown text but not extracted separately
- Crawl4AI DOES discover images but we don't capture them

## 2. Integration Points

### Connector Service
| File | Lines | Change |
|------|-------|--------|
| `adapters/base.py` | 8-26 | Add `images` field to `DocumentRef` |
| `adapters/github.py` | 38, 145-156 | Add image types, extract image URLs from markdown |
| `adapters/notion.py` | 222-313 | Extract image block URLs |
| `adapters/webcrawler.py` | 237-309 | Regex extract `![alt](url)` from markdown |
| `services/parser.py` | 46-52 | Extract Image elements from Unstructured partition |
| `services/sync_engine.py` | 187-216 | Upload images to S3, pass URLs to ingest |
| `clients/knowledge_ingest.py` | 25-77 | Add `image_urls` parameter |

### Knowledge-Ingest Service
| File | Lines | Change |
|------|-------|--------|
| `models.py` | 9-23 | Add `image_urls` to IngestRequest |
| `routes/ingest.py` | 199-475 | Add image_urls to extra_payload (MUST survive Procrastinate) |
| `qdrant_store.py` | 77-97 | Optional: index on image_urls |
| `pg_store.py` | 30-68 | Store in `extra` JSONB (no schema change needed) |

### Retrieval Pipeline
| File | Lines | Change |
|------|-------|--------|
| `retrieval_api/api/retrieve.py` | 53-200+ | Include image_urls in response |

## 3. Schema Analysis

### Qdrant Chunk Payload (proposed addition)
```json
{
  "image_urls": ["https://garage.internal/org-id/images/sha256.webp"],
  // ... existing fields unchanged
}
```

### PostgreSQL Artifact (no schema change — uses existing `extra` JSONB)
```json
{
  "source_connector_id": "abc-123",
  "image_urls": ["https://garage.internal/..."]
}
```

## 4. Infrastructure

### Current State
- No object storage in Docker Compose stack
- No file/binary serving endpoints in portal-api
- All data is tenant-scoped via `org_id`

### Garage S3 (Recommended)
- Rust-based, AGPL v3, Deuxfleurs (French non-profit)
- 1GB RAM footprint, single binary, S3-compatible API
- MinIO is in maintenance mode since Dec 2025 — not recommended
- Alternatives evaluated: SeaweedFS (more complex), RustFS (too young)

## 5. Reference Implementations in Codebase

### Extra Payload Pattern
`knowledge_ingest/routes/ingest.py:318-376` — `extra_payload` dict flows through to Qdrant and PostgreSQL. Image URLs follow this exact pattern.

### Tenant Isolation Pattern
All data scoped by `org_id` in Qdrant filters and PostgreSQL WHERE clauses. S3 paths: `/{org_id}/images/{kb_slug}/{sha256}.{ext}`

### Procrastinate Enrichment Warning
From ingest.py line 435: Any metadata NOT in `extra_payload` before `defer_async()` is silently deleted by enrichment worker. Image URLs MUST be included.

## 6. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Notion image URL expiry | HIGH | Download and re-upload to Garage immediately |
| Procrastinate drops image_urls | HIGH | Include in extra_payload before defer |
| Image download failures block ingest | MEDIUM | try/except per image, log and continue |
| Large images (>5MB) | MEDIUM | Resize/compress on upload, max 5MB |
| Multi-layer field loss (Qdrant->Retrieval->Frontend) | MEDIUM | End-to-end test with real images |
| Backward compat (existing chunks) | LOW | Field is optional (None = no images) |

## 7. Open Questions

1. Image size limits — max per document? max per org?
2. Image formats — JPEG/PNG only or also WEBP/SVG?
3. Presigned URL TTL — 24 hours? 7 days? 30 days?
4. Frontend display — inline in chat? modal? thumbnail + full?
5. Garage deployment — same server or separate? Replication needed?
