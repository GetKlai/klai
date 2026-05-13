---
id: SPEC-KB-FILE-UPLOAD-001
title: KB File Upload via docling-serve
version: 0.2.0
status: draft
created_at: 2026-05-10
owner: mark.vletter
domain: knowledge-base
related_specs:
  - SPEC-KB-SOURCES-001        # url/text/youtube routes (no file path)
  - SPEC-SEC-SSRF-001          # docling-serve must-not-join socket-proxy
  - SPEC-CRAWLER-004           # image pipeline (Garage S3 blueprint)
  - SPEC-INFRA-CONTAINER-HYGIENE-001  # klasse A labelling
  - SPEC-INFRA-CONFIG-SYNC-001 # bind-mount sync pattern
  - SPEC-SEC-IDENTITY-ASSERT-002      # internal-secret + membership auth
  - SPEC-DEPLOY-AUTO-MIGRATE-001      # alembic auto-migration on portal-api
---

# SPEC-KB-FILE-UPLOAD-001 — KB File Upload via docling-serve

## HISTORY

| Date       | Version | Author        | Note                                         |
|------------|---------|---------------|----------------------------------------------|
| 2026-05-11 | 0.2.0   | codex         | Align live design with Docling Serve async chunk endpoints, pre-chunked ingest, and result lifecycle requirements. |
| 2026-05-10 | 0.1.0   | mark.vletter  | Initial draft. Phase 1-4 plan, EARS REQ-1..9 |

---

## 1. Goal & Outcome

### Goal

Add a real file-upload route to the Klai knowledgebase pipeline so that uploaded
binary documents (PDF / DOCX / XLSX / PPTX / archives) end up as searchable
chunks in Qdrant + Postgres via the existing chunk → embed → graph pipeline.

### Outcome (success measured by)

- Uploading a large structured document via the KB UI produces retrievable
  chunks without exceeding the `knowledge-ingest` `IngestRequest.content`
  bound. The 129 MB Chemie Overal 4VWO PDF is the production regression
  fixture for this path.
- The current `500: 500` failure on `/app/knowledge/<kb>/add-source` for
  non-`.md` files is gone; failure responses are structured with a
  machine-readable `error_code`.
- All 12 listed extensions (`.csv .doc .docx .json .md .pdf .pptx .tar .txt
  .xlsx .xml .zip`) are accepted at the public boundary; binary formats route
  through `docling-serve`, archives are unpacked safely, text formats skip
  docling.
- Magic-byte validation runs server-side **before** any object storage write —
  a `.exe` renamed to `.pdf` returns `400 mime_mismatch` with no Garage object
  created.

### Non-goal

- Audio / video transcription via docling (`asr` extra + ffmpeg).
- Resumable / `tus.io` uploads (only relevant > 1 GB).
- Direct-to-S3 presigned-PUT pattern (only relevant > 1 GB).
- Cross-org sharing of uploaded documents.
- KB content-search UX changes.

---

## 2. Background

The frontend `FileUploadForm` on `/app/knowledge/<kb>/add-source` POSTs to
`${DOCS_BASE}/orgs/{org}/kbs/{kb}/upload` (DOCS_BASE = `/api/docs/api`). That
upstream is the **klai-docs** wiki upload, which only accepts `.md`, stores
content in **Gitea**, and crashes (HTTP 500) on 100 MB binaries because
Next.js `request.formData()` on docs-app loads the whole body into memory and
`file.text()` decodes binary as UTF-8.

The real KB pipeline (`knowledge-ingest` → Qdrant + Postgres) has **no
file-upload endpoint** today. `app_knowledge_sources.py` has only `url`,
`text`, and a deprecated `youtube` stub.

`docling-serve v1.16.1` is already deployed (`deploy/docker-compose.yml:783`)
on the internal `klai-net` and accepts every required document format via
`POST /v1/convert/file`, `POST /v1/chunk/hybrid/file`, and their async
variants. The
architecture document (`docs/architecture/klai-knowledge-architecture.md`)
already describes the intended shape (`klai-connector file upload endpoint`)
but the endpoint was never built — the design predates `SPEC-DECOMM-FOCUS-001`
and the docling integration in `klai-focus/research-api/services/docling.py`
was decommissioned with the rest of klai-focus.

Production testing on 2026-05-11 showed why Docling must be treated as a
generic conversion and chunking product, not as a PDF-to-Markdown helper:
a 129 MB PDF converted through `/v1/convert/file/async` yielded about 491 MB
of Markdown because images were embedded as base64. Switching only
`image_export_mode=placeholder` avoided the image explosion but still kept the
pipeline dependent on one oversized `content` field and on Docling's default
single-use result storage. The live path therefore uses Docling Serve's async
chunk endpoint and forwards Docling chunks directly to `knowledge-ingest`.

---

## 3. Scope

### In scope

- New `POST /api/app/knowledge-bases/{kb_slug}/sources/file` route in
  portal-api.
- New `klai-libs/document-storage` library wrapping the Garage S3 SDK,
  modelled on `klai-libs/image-storage`.
- New `klai-documents` Garage bucket, **private**, presigned-GET only.
- New `POST /ingest/v1/file` route in knowledge-ingest with Procrastinate
  background job.
- New docling adapter in knowledge-ingest (`adapters/docling.py`) targeting
  `/v1/chunk/hybrid/file`.
- New archive adapter in knowledge-ingest (`adapters/archive.py`) with
  sunzip-style streaming guards.
- New `libreoffice-headless` sidecar container (Phase 4 only) for `.doc`
  → `.docx` pre-conversion.
- Caddy `request_body { max_size 200MB }` scoped to the upload handle.
- Frontend `FileUploadForm` switched to the new endpoint; client-side size
  guard, progress bar, and i18n failure-reason mapping.
- Quota & RBAC: per-KB byte quota, per-org daily upload rate limit, new
  `kb_file_upload` product gate.
- Observability: structured `kb_upload_received` and `kb_file_extracted`
  events, Grafana panel.

### Out of scope (explicitly)

- Editing or replacing already-uploaded artifacts (re-upload only).
- Per-page preview of uploaded documents in the UI (artifact list shows
  filename + chunk count only).
- Downloading the original binary back from the KB (presigned-GET exists for
  internal debugging, no UI surface).
- Real-time WebSocket status updates (poll-based instead).
- Retroactive migration of existing wiki `.md` uploads into the new path.

---

## 4. Architecture

```
Browser
  │ multipart/form-data, max 200MB
  ▼
Caddy   request_body { max_size 200MB } scoped to /api/app/knowledge-bases/*/sources/file
  │ session cookie, X-Request-ID, BFF-auth headers
  ▼
portal-api  POST /api/app/knowledge-bases/{kb_slug}/sources/file
  │  1. _get_kb_or_404(kb_slug, perms.org_id, db)        — RLS guard
  │  2. require_capability("kb_file_upload")             — RBAC
  │  3. for each multipart "file":
  │     a. extension whitelist + filetype magic-byte check
  │     b. if archive (.zip/.tar):
  │           streaming-extract via klai_archive_safe (sunzip-style)
  │           each entry → recurse step 3.a (no nesting)
  │     c. assert_can_upload_bytes(kb, total_uncompressed) — FOR UPDATE
  │     d. content-addressed S3 write to garage://klai-documents/<sha256>
  │     e. INSERT INTO kb_uploads (artifact_id, s3_key, bytes, mime, status='pending')
  │     f. emit knowledge.uploaded event per artifact
  │     g. text-path: POST http://knowledge-ingest:8000/ingest/v1/document
  │        body: { org_id, kb_slug, path, content, source_type="file", ... }
  │     h. docling-path:
  │        POST http://docling-serve:5001/v1/chunk/hybrid/file/async
  │        body: file + include_converted_doc=false
  │              + convert_image_export_mode=placeholder
  │              + convert_from_formats=<explicit Docling enum when known>
  │        persist { status='processing', docling_task_id }
  │  4. return 202 { uploads: [...] }
  ▼
portal-api kb_upload_poller
  │ poll /v1/status/poll/{task_id}
  │ on success: fetch /v1/result/{task_id}
  │ normalize ChunkDocumentResponse.chunks[].text
  │ build bounded content preview (≤ 450k chars)
  │ POST /ingest/v1/document
  │ body includes:
  │   skip_chunking=true
  │   chunks=[Docling chunk text...]
  │   content=<bounded preview>
  │   content_hash=<original file sha256>
  ▼
knowledge-ingest /ingest/v1/document
  │ respects pre-computed chunks when skip_chunking=true
  │ stores source hash from content_hash to dedupe the original file,
  │ not the truncated preview
  ▼
portal-api artifact-status polling                      (existing pattern)
```

Data flow invariants:

- Docling is the structural parser and chunker for the docling path. Klai must
  not reconstruct document chunks by walking a giant Markdown string when
  Docling already returned typed chunks.
- `IngestRequest.content` is bounded metadata/classification input for
  pre-chunked Docling documents. The complete document text is represented by
  `chunks[]`.
- `content_hash` is the full original source hash for pre-chunked/truncated
  ingests. Deduplication must not use the bounded preview body.
- Docling results are volatile service state. The poller should fetch the
  terminal result once and ingest it immediately; production also configures
  Docling with non-single-use results and a longer result TTL for restart and
  retry tolerance.
- Magic-byte validation happens **once**, in portal-api. Downstream services
  trust the validated mime and explicit Docling input format.

---

## 5. Decisions

| ID | Question | Decision | Rationale |
|----|----------|----------|-----------|
| D-1 | Per-KB quota | org KB = 5 GB, personal KB = 1 GB. Plan-driven override later via FEATURE_MIN_PROFILE. | Aligns with existing `kb_quota` item-count model; gives 50 × 100 MB headroom per org KB which covers normal seeded course books. Not plan-gated yet to avoid plan-config churn during rollout. |
| D-2 | `.doc` legacy support | Phase 4 (libreoffice-headless sidecar). Phases 1-3 reject `.doc` with `error_code: "doc_format_not_yet_supported"` and message "Converteer naar .docx — .doc komt later". | `.doc` requires a 400 MB libreoffice image and a long-running sidecar with its own deploy cadence. Phase 1 ships value (PDF, DOCX, XLSX, PPTX) without that overhead. |
| D-3 | Generic JSON / XML | Index as plaintext (Markdown chunker fallback). Schema detection (json_docling / JATS / USPTO / XBRL) routes to docling. | Strict schema-only would reject every legitimate `.xml` and `.json` Klai customers actually have. Plaintext indexing is the lossy-but-useful default; schema detection upgrades the path when applicable. |
| D-4 | Async UX | Poll-based artifact status (existing connector pattern). | WebSocket adds infra surface for marginal UX win; poll already works for connector syncs which have similar runtime. |
| D-5 | Garage bucket policy | `klai-documents` is **private**, presigned-GET only, TTL 1 h. **Different from `kb-images`** which uses website-mode + Caddy reverse-proxy. | Documents may contain sensitive content (legal, HR). URL-guessing on a website-mode bucket leaks content. Presigned-GET requires authenticated portal-api session. |
| D-6 | New table vs. extend | Extend knowledge-ingest artifacts via existing `extra` jsonb (add `source_kind: "file"`, `s3_key`, `original_filename`). New `kb_uploads` table on portal-api side for s3_key tracking + per-KB byte quota counter. | Existing artifacts table (with `source_connector_id` pattern) is the authoritative chunk system; mirroring that on portal-api would create dual-write. portal-api owns only what it needs to route status / quota. |
| D-7 | Docling endpoint | Use `POST /v1/chunk/hybrid/file/async` for the docling path, not `/v1/convert/file/async`. | Docling Serve already exposes hybrid chunks with headings, page numbers, token counts and text. Using convert first creates oversized Markdown and loses the product's chunking semantics. |
| D-8 | Converted document payload | Submit with `include_converted_doc=false` and `convert_image_export_mode=placeholder`. | RAG ingestion needs text chunks, not an embedded DoclingDocument or base64 image payloads. This keeps result and ingest payloads bounded. |
| D-9 | Pre-chunked ingest contract | Forward Docling chunk text to `/ingest/v1/document` with `skip_chunking=true`, `chunks=[...]`, bounded `content`, and original file `content_hash`. | Prevents 422s from `content` max length, preserves Docling structure, and dedupes against the uploaded source instead of the preview. |
| D-10 | Docling result lifecycle | Configure `DOCLING_SERVE_SINGLE_USE_RESULTS=false` and `DOCLING_SERVE_RESULT_REMOVAL_DELAY=86400`; still treat Docling results as volatile and ingest immediately after terminal success. | Docling Serve defaults are optimized for one client fetch. The portal poller may need retry tolerance after a transient `/ingest` failure or portal restart. Durable result storage in Klai remains a future hardening step. |
| D-11 | Large-document enrichment | Skip per-chunk LLM enrichment when the ingest content is a truncated preview or when the chunk count exceeds `enrichment_max_chunks` (default 200). Keep the raw Docling vectors as the searchable source of truth. | A 1557-chunk textbook would require thousands of LiteLLM calls and can be rate-limited for minutes. Re-chunking the bounded preview during enrichment would also overwrite the full Docling index with partial content. |
| D-12 | Archive semantics | Treat `.zip` / `.tar` as batch transport containers, never as semantic documents. Each accepted member becomes its own `kb_uploads` row and artifact; the archive itself is not indexed. | Docling and vector-store style retrieval systems operate on actual document files, not archive containers. Per-entry artifacts preserve filenames, status, retry behavior, deletion, citations, and quota accounting. |
| D-13 | Archive acceptance policy | Archive ingest is all-or-nothing. If any member is unsupported, unsafe, malformed, too large, nested, mime-mismatched, or otherwise not accepted by the normal file rules, reject the whole archive before creating downstream ingest jobs. | Partial success is useful for multi-file browser uploads, but archives are opaque to users before upload. In business usage, silently indexing only part of an archive creates incomplete KBs and hard-to-debug trust issues. |

---

## 6. Requirements (EARS)

### REQ-1 — Endpoint and accepted formats

**Ubiquitous.** The system shall expose `POST /api/app/knowledge-bases/{kb_slug}/sources/file`
accepting `multipart/form-data` with one or more `file` parts.

**Event-driven.** WHEN the request body contains a file part whose extension is in
the whitelist `{.csv, .doc, .docx, .json, .md, .pdf, .pptx, .tar, .txt, .xlsx, .xml, .zip}`
AND whose magic-byte content type matches the extension, THE SYSTEM SHALL accept
the part for further processing.

**Event-driven.** WHEN a file part has an extension NOT in the whitelist,
THE SYSTEM SHALL return HTTP 400 with `error_code: "unsupported_extension"`
and reject the entire request without writing to Garage.

**Event-driven.** WHEN a file part has a whitelisted extension but the
magic-byte content type disagrees, THE SYSTEM SHALL return HTTP 400 with
`error_code: "mime_mismatch"` and reject the entire request without writing to
Garage.

**Unwanted behaviour.** IF a text-format file (`.md .txt .csv .json .xml`)
fails UTF-8 decode AND charset-detect cannot recover a confidence ≥ 0.9
encoding, THEN THE SYSTEM SHALL reject with `error_code: "invalid_text_encoding"`.

**Acceptance criteria:**
- AC-1.1: Whitelisted-extension + matching-magic-byte upload returns HTTP 202
  with `{ uploads: [{ artifact_id, status: "pending" }] }`.
- AC-1.2: `.exe` file returns 400 `unsupported_extension`.
- AC-1.3: `.pdf` containing PNG magic bytes returns 400 `mime_mismatch`.
- AC-1.4: Office Open XML formats (`.docx .xlsx .pptx`) are validated as a
  zip containing `[Content_Types].xml` before accepting.
- AC-1.5: Plain-text formats skip magic-byte content check; instead the
  first 1 MB must decode as UTF-8 OR via charset-detect ≥ 0.9 confidence.
- AC-1.6: Garage object count does NOT increase for any rejected upload
  (verified via Garage stats before/after).

---

### REQ-2 — Size and streaming

**Ubiquitous.** The system shall enforce a 200 MB per-file limit at the
edge (Caddy) and stream multipart parts to disk-backed temp files at
portal-api without loading them fully into memory.

**Event-driven.** WHEN a request body exceeds 200 MB, THE SYSTEM SHALL
respond with HTTP 413 from Caddy before the request reaches portal-api.

**State-driven.** WHILE portal-api is processing a 200 MB upload, the
container's RSS shall stay below 100 MB delta over baseline (verified via
container metrics).

**Acceptance criteria:**
- AC-2.1: A 200 MB file uploads successfully end-to-end (Caddy →
  portal-api → Garage).
- AC-2.2: A 201 MB file is rejected at Caddy with HTTP 413.
- AC-2.3: portal-api Starlette `request.form()` is called with
  `max_part_size=200*1024*1024`, `max_files=10`, `max_fields=10`.
- AC-2.4: Garage write uses `boto3.upload_fileobj` (auto-multipart at 8 MB
  parts) — verified by code inspection of `klai_document_storage.client`.
- AC-2.5: A 100 MB PDF completes Caddy → portal-api → Garage in < 60 s on
  the production hardware profile.

---

### REQ-3 — Archive safety and batch semantics

**Ubiquitous.** Archives (`.zip`, `.tar`) shall be treated as batch
containers. The archive file itself SHALL NOT be indexed. Every accepted
member SHALL become an independent upload row, downstream ingest job, artifact,
source row, and citation target.

**Ubiquitous.** Archive ingest SHALL be all-or-nothing. The system SHALL NOT
create any `kb_uploads` rows, Docling tasks, Qdrant points, or artifacts for an
archive until every member has passed archive preflight, extraction guards, and
the same per-file validation used by top-level uploads.

**State-driven.** BEFORE extracting member payloads, the system SHALL perform
archive preflight using metadata available without decompression:

- member count;
- member paths;
- extensions;
- nested archive detection;
- declared uncompressed size where available;
- duplicate normalized paths;
- tar member type;
- cumulative declared uncompressed size where available.

**Event-driven.** WHEN preflight finds any unacceptable member, THE SYSTEM
SHALL reject the entire archive with the member-specific `error_code` and SHALL
NOT process any other member.

**Ubiquitous.** Archive members SHALL use the same accepted document whitelist
as direct uploads, excluding archive extensions and phase-pending formats:
`.csv`, `.docx`, `.json`, `.md`, `.pdf`, `.pptx`, `.txt`, `.xlsx`, `.xml`.
`.doc` inside an archive SHALL reject the whole archive with
`error_code: "doc_format_not_yet_supported"`.

**Event-driven.** WHEN an archive contains more than 200 file members, THE
SYSTEM SHALL reject the archive with `error_code: "archive_too_many_entries"`.

**Event-driven.** WHEN an archive contains more than 10 Docling-path members
(`.pdf`, `.docx`, `.pptx`, `.xlsx`, `.json`, `.xml`), THE SYSTEM SHALL reject
the archive with `error_code: "archive_too_many_docling_entries"`.

**Ubiquitous.** Archive preflight SHALL compute an archive complexity score so
the allowed member count scales with member type and declared size:

| Member shape | Complexity units |
|---|---:|
| `.md`, `.txt`, `.csv` up to 1 MB | 1 |
| `.md`, `.txt`, `.csv` above 1 MB | 2 |
| `.json`, `.xml` | 3 |
| `.docx`, `.xlsx`, `.pptx` | 5 |
| `.pdf` up to 10 MB | 10 |
| `.pdf` above 10 MB | 10 + 1 per additional started 10 MB |

WHEN the archive complexity score exceeds 200 units, THE SYSTEM SHALL reject
the archive with `error_code: "archive_complexity_budget_exceeded"`.

**Event-driven.** WHEN cumulative uncompressed size exceeds 500 MB OR a
member's uncompressed size exceeds 50 MB, THE SYSTEM SHALL reject the archive
with `error_code: "archive_total_size"` or `error_code:
"archive_entry_too_large"`.

**Event-driven.** WHEN an entry name contains a NUL byte, an absolute path,
parent traversal (`..`), a Windows drive prefix, or a path that normalizes
outside the archive root, THE SYSTEM SHALL reject the archive with
`error_code: "archive_path_traversal"`.

**Event-driven.** WHEN two member paths normalize to the same value, THE SYSTEM
SHALL reject the archive with `error_code: "archive_duplicate_entry"`.

**Event-driven.** WHEN an entry's extension is `.zip` or `.tar` (nested
archive), THE SYSTEM SHALL reject the archive with `error_code:
"archive_nested"`.

**Event-driven.** WHEN a tar member has type other than REGTYPE / AREGTYPE, THE
SYSTEM SHALL reject the archive with `error_code: "archive_unsafe_entry"`.

**State-driven.** WHILE extracting each member into a disk-backed staging area,
the system SHALL stream bytes and enforce per-entry size, total size, and
compression-ratio limits. The system SHALL NOT hold all decompressed member
bytes in memory.

**Event-driven.** WHEN a zip member's compression ratio exceeds 10:1 after the
first 1 MB of decompressed output, THE SYSTEM SHALL abort extraction and return
`error_code: "archive_compression_ratio"` within 10 MB of decompression
progress.

**Event-driven.** WHEN an extracted member's magic-byte content fails the same
validation required by REQ-1, THE SYSTEM SHALL reject the entire archive with
the underlying file `error_code` (`mime_mismatch`, `empty_content`, or
`invalid_text_encoding`) and SHALL delete all staged member files.

**Event-driven.** WHEN all members pass validation, THE SYSTEM SHALL create one
archive batch id, create one upload row per member with initial status
`pending`, and enqueue one independent ingest job per member. Docling-path
members SHALL enter the normal Docling queue; text-path members SHALL enter the
normal direct ingest path. The request handler SHALL NOT run Docling conversion
inline for archive members. The request SHALL return HTTP 202 with one upload
entry per member, not one upload entry for the archive.

**Ubiquitous.** Each member upload row and artifact SHALL include archive
provenance metadata:

- `archive_batch_id`;
- `archive_filename`;
- `archive_source_ref`;
- `archive_member_path`;
- `archive_member_index`;
- `archive_member_count`.

**Unwanted behaviour.** IF any member fails archive or file validation, THEN
THE SYSTEM SHALL reject the whole archive. Partial indexing of a user-supplied
archive is forbidden.

**Acceptance criteria:**
- AC-3.1: A canonical 42 KB → 4 GB zip-bomb fixture aborts within 10 MB
  decompression.
- AC-3.2: A `.zip` with 201 valid `.md` entries returns 400
  `archive_too_many_entries`.
- AC-3.3: A `.tar` containing `../../etc/passwd` returns 400
  `archive_path_traversal`.
- AC-3.4: A `.zip` containing `nested.zip` rejects the whole archive with
  `archive_nested`; no upload rows are created.
- AC-3.5: A `.tar` symlink entry returns 400 `archive_unsafe_entry`.
- AC-3.6: A `.zip` with mixed `[1.md, 2.pdf, 3.exe]` returns 400
  `unsupported_extension`; no artifacts, upload rows, Docling tasks, or Qdrant
  points are created.
- AC-3.7: A `.zip` with 5 valid members (`.md`, `.txt`, `.pdf`, `.docx`,
  `.xlsx`) returns 202 with 5 upload entries and a shared `archive_batch_id`.
- AC-3.8: A `.zip` with 11 valid PDF members returns 400
  `archive_too_many_docling_entries`.
- AC-3.9: A `.zip` with two paths that normalize to the same member path
  returns 400 `archive_duplicate_entry`.
- AC-3.10: A `.zip` with 150 valid small `.md` files returns 202 with 150
  upload entries and a shared `archive_batch_id`.
- AC-3.11: A `.zip` whose members stay under hard count limits but exceed 200
  complexity units returns 400 `archive_complexity_budget_exceeded`.
- AC-3.12: During a 500 MB archive staging run, portal-api RSS stays below
  100 MB delta over baseline.

---

### REQ-4 — Pipeline routing

**Ubiquitous.** The knowledge-ingest pipeline shall route each artifact to one
of three paths based on validated mime + content sniff: text-path,
docling-path, or doc-path.

**Decision table:**

| Detected content | Path | docling format param |
|------------------|------|----------------------|
| `text/markdown` (.md) | text | — |
| `text/plain` (.txt) | text | — |
| `text/csv` (.csv) | text | — |
| `application/json` without DoclingDocument schema | text | — |
| `application/xml` without JATS / USPTO / XBRL root | text | — |
| `application/pdf` | docling | `pdf` |
| `application/vnd.openxmlformats-...wordprocessingml.document` | docling | `docx` |
| `application/vnd.openxmlformats-...spreadsheetml.sheet` | docling | `xlsx` |
| `application/vnd.openxmlformats-...presentationml.presentation` | docling | `pptx` |
| `application/json` matching DoclingDocument schema | docling | `json_docling` |
| `application/xml` with `<article>` JATS root | docling | `xml_jats` |
| `application/xml` with `<us-patent-application>` root | docling | `xml_uspto` |
| `application/xml` with XBRL namespace | docling | `xml_xbrl` |
| `application/msword` (.doc) — Phase ≤ 3 | reject | `error_code: doc_format_not_yet_supported` |
| `application/msword` (.doc) — Phase 4 | doc → docling | `docx` after libreoffice |

**Event-driven.** WHEN a file routes to docling-path, THE SYSTEM SHALL POST
the binary to `http://docling-serve:5001/v1/chunk/hybrid/file/async` with
`include_converted_doc=false`, `convert_image_export_mode=placeholder`, and
the correct explicit `convert_from_formats` value when the detected format
maps to a Docling enum.

**Event-driven.** WHEN Docling reports terminal success, THE SYSTEM SHALL fetch
`/v1/result/{task_id}` once, parse `ChunkDocumentResponse.chunks[].text`, and
forward those values to `knowledge-ingest` as `skip_chunking=true` and
`chunks=[...]`.

**Ubiquitous.** For docling-path uploads, the `content` field sent to
`knowledge-ingest` SHALL be a bounded preview no larger than 450,000
characters and `content_hash` SHALL be the original uploaded source hash.

**Ubiquitous.** WHEN a docling-path upload has `document_text_truncated=true`
OR produces more chunks than `enrichment_max_chunks`, THE SYSTEM SHALL NOT
enqueue per-chunk LLM enrichment. The raw Docling chunks SHALL remain indexed
and searchable.

**Event-driven.** WHEN docling-serve returns non-2xx OR times out (≥ 600 s),
THE SYSTEM SHALL mark the artifact `failed` with `failure_reason:
"docling_timeout"` or `"extraction_failed"` and NOT retry automatically.

**Acceptance criteria:**
- AC-4.1: `.md` upload produces chunks via the existing Markdown chunker
  (verified by absence of HTTP call to docling-serve in test).
- AC-4.2: `.pdf` upload submits to docling
  `/v1/chunk/hybrid/file/async` with `convert_from_formats=pdf`.
- AC-4.3: A 5-page sample PDF produces ≥ 5 chunks each with non-null
  `page_numbers` payload field.
- AC-4.4: An `.xml` file whose root local-name is `article` (JATS) routes
  with `format=xml_jats`.
- AC-4.5 (Phase ≤ 3): `.doc` returns 400 `doc_format_not_yet_supported`.
- AC-4.6 (Phase 4): `.doc` is converted via libreoffice-headless and
  produces docling chunks.
- AC-4.7: The Chemie Overal 4VWO 129 MB PDF produces a `/ingest/v1/document`
  request whose `content` is ≤ 450,000 chars, whose `chunks` array is non-empty,
  and whose `skip_chunking` is true.
- AC-4.8: The same Chemie upload does not produce HTTP 422 from
  `knowledge-ingest` and never transitions from Docling success to
  `docling_result_not_found`.
- AC-4.9: The same Chemie upload stores 1557 raw chunks in Qdrant and skips
  LLM enrichment with `llm_enrichment_skip_reason=document_text_truncated`.
  No enrichment job may overwrite the index with chunks derived from the
  bounded preview.

---

### REQ-5 — Quota and RBAC

**Ubiquitous.** Uploads shall be gated by (a) the `kb_file_upload` product
in `UserPermissions.effective_products`, (b) per-KB byte quota, (c) per-org
daily upload-count rate limit.

**Event-driven.** WHEN the caller's `effective_products` does NOT include
`kb_file_upload`, THE SYSTEM SHALL return 403 with `error_code:
"not_entitled"`.

**Event-driven.** WHEN the upload would push the KB's total uploaded
bytes over its quota (5 GB org, 1 GB personal), THE SYSTEM SHALL return 413
with `error_code: "kb_quota_bytes_exceeded"`.

**Event-driven.** WHEN the org has already accepted ≥ 100 file uploads in
the rolling 24-hour window, THE SYSTEM SHALL return 429 with
`error_code: "rate_limit_exceeded"` and a `Retry-After` header.

**Ubiquitous.** Quota enforcement shall use `SELECT ... FOR UPDATE` on the
`kb_uploads_quota` row in the same transaction as the artifact insert
(per `portal-backend.md` SELECT FOR UPDATE pattern).

**Acceptance criteria:**
- AC-5.1: User without `kb_file_upload` → 403 `not_entitled` (verified
  via integration test with capability-stripped fixture).
- AC-5.2: A KB at 4.5 GB used + 600 MB upload → 413
  `kb_quota_bytes_exceeded` (since 4.5 + 0.6 = 5.1 > 5).
- AC-5.3: 101st upload in 24 h returns 429.
- AC-5.4: 10 parallel 600 MB uploads into a fresh 5 GB KB → exactly 8
  accepted (5000 / 600 ≈ 8.33), 2 rejected with `kb_quota_bytes_exceeded`,
  no over-quota state.
- AC-5.5: `kb_file_upload` is added to `FEATURE_MIN_PROFILE` with
  `ProfileRole.PERSONAL` (any authenticated member of an org with the
  product enabled).

---

### REQ-6 — Status and structured errors

**Ubiquitous.** Every accepted upload shall produce one or more artifacts;
each artifact shall be polled to completion via the existing artifact
status endpoint.

**Failure-reason enum (machine-readable):**
```
unsupported_extension
mime_mismatch
invalid_text_encoding
oox_validation_failed
file_too_large
archive_compression_ratio
archive_total_size
archive_entry_too_large
archive_too_many_entries
archive_too_many_docling_entries
archive_complexity_budget_exceeded
archive_path_traversal
archive_duplicate_entry
archive_nested
archive_unsafe_entry
kb_quota_bytes_exceeded
rate_limit_exceeded
not_entitled
storage_unavailable
extraction_failed
docling_timeout
internal_error
doc_format_not_yet_supported
```

**Event-driven.** WHEN the endpoint accepts a request, THE SYSTEM SHALL
return HTTP 202 with body `{ uploads: [{ artifact_id: UUID, source_ref:
str, source_type: "file", filename: str, status: "pending" }, ...] }`.

**Event-driven.** WHEN any artifact reaches a terminal state, the artifact
row shall record exactly one of `status ∈ {done, failed}`. If `failed`,
`failure_reason` shall be a non-null value from the enum above.

**Acceptance criteria:**
- AC-6.1: Single-file POST returns one artifact in the response.
- AC-6.2: Archive POST returns N artifacts (one per accepted entry) plus
  a `skipped: [{ filename, reason }]` array for rejected entries.
- AC-6.3: After all artifacts reach `done`, retrieval-api `/retrieve`
  with the same kb_slug returns chunks owned by those artifacts.
- AC-6.4: The status enum and failure_reason enum are exported from a
  single shared schema module imported by both portal-api and
  knowledge-ingest (no string drift).

---

### REQ-7 — UI fix on FileUploadForm

**Ubiquitous.** `FileUploadForm.tsx` shall POST to
`/api/app/knowledge-bases/${kbSlug}/sources/file` (NOT to `${DOCS_BASE}`),
validate file size client-side, surface per-file progress, and map every
failure_reason to a localized NL message.

**Acceptance criteria:**
- AC-7.1: `FileUploadForm.tsx` no longer imports `DOCS_BASE`. `tree-utils.ts`
  comment clarifies that `DOCS_BASE` is for the wiki/docs editor only.
- AC-7.2: Client-side size guard rejects per-file > 200 MB before any
  network request, with a NL error string.
- AC-7.3: Each upload shows a progress bar 0–100 % via XHR `upload.onprogress`.
- AC-7.4: Each `error_code` from REQ-6 maps to a Paraglide message:
  - `unsupported_extension` → "Bestandstype niet ondersteund. Toegestane
    formaten: PDF, Word, Excel, PowerPoint, CSV, JSON, XML, ZIP, TAR, MD,
    TXT."
  - `file_too_large` → "Bestand te groot (max 200 MB). Splits het bestand of
    gebruik een connector."
  - `mime_mismatch` → "Bestand lijkt geen geldige {extension} te zijn.
    Controleer of het bestand niet beschadigd is."
  - `kb_quota_bytes_exceeded` → "Geen ruimte meer in deze kennisbank.
    Verwijder oude bronnen of upgrade je plan."
  - `rate_limit_exceeded` → "Te veel uploads vandaag. Probeer het over een
    uur opnieuw."
  - `not_entitled` → "Bestanden uploaden is niet beschikbaar in jouw plan."
  - `archive_*` → "Archief bevat een onveilig of te groot bestand. Bekijk
    de details en probeer opnieuw."
  - `extraction_failed`, `docling_timeout` → "Kon dit bestand niet
    verwerken. Probeer opnieuw of converteer naar PDF."
  - `doc_format_not_yet_supported` → "Converteer naar .docx — `.doc`
    wordt binnenkort ondersteund."
  - default → "Upload mislukt. Probeer opnieuw of neem contact op."
- AC-7.5: After all uploads succeed, the form shows a 1.5 s success banner
  and navigates to `/app/knowledge/{kb}/overview`.
- AC-7.6: The `accept` attribute is exactly
  `.csv,.doc,.docx,.json,.md,.pdf,.pptx,.tar,.txt,.xlsx,.xml,.zip`.

---

### REQ-8 — Observability

**Ubiquitous.** Every upload shall produce structured log events queryable in
VictoriaLogs and a Grafana panel.

**Events:**

- `kb_upload_received` (portal-api):
  ```
  service=portal-api event=kb_upload_received
  org_id, kb_slug, filename, mime, bytes,
  archive_entries (0 if not archive),
  decision in {accepted, rejected},
  failure_reason if rejected,
  request_id
  ```

- `kb_file_extracted` (knowledge-ingest):
  ```
  service=knowledge-ingest event=kb_file_extracted
  artifact_id, org_id, kb_slug, mime, bytes,
  chunks_count, docling_used in {true, false},
  duration_ms, status in {done, failed},
  failure_reason if failed,
  request_id
  ```

- `knowledge.uploaded` (product_events):
  Existing event extended with `properties.file_type` and `properties.bytes`.

**Acceptance criteria:**
- AC-8.1: Every POST to `/sources/file` produces exactly one
  `kb_upload_received` log line (verified by VictoriaLogs query in
  integration test).
- AC-8.2: Every successful artifact produces exactly one
  `kb_file_extracted` log line with `status: "done"`.
- AC-8.3: VictoriaLogs query `service:portal-api AND event:kb_upload_received
  AND decision:rejected | stats count() by failure_reason` returns the
  rejection histogram.
- AC-8.4: Grafana panel `KB Uploads — Hourly` exists in the Klai dashboard,
  showing accepted vs rejected counts and bytes by failure_reason.
- AC-8.5: Cross-service trace correlation: a single `request_id` query
  shows Caddy → portal-api → knowledge-ingest → docling-serve chain.

---

### REQ-9 — Security gates

**Ubiquitous.** The endpoint shall accept NO URL field; only multipart
file. docling-serve and libreoffice-headless shall remain on `klai-net`
only, never on the docker-socket-proxy network. Magic-byte validation
shall happen server-side **before** any object-storage write.

**Event-driven.** WHEN the BFF session cookie is missing or invalid,
THE SYSTEM SHALL return 401 (no information leak about KB existence).

**Event-driven.** WHEN the kb_slug exists but in a different org than
`perms.org_id`, THE SYSTEM SHALL return 404 (NOT 403 — info-leak
prevention).

**Ubiquitous.** The `klai-documents` Garage bucket shall have no public-read
ACL. Read access is via presigned-GET URL only, generated server-side, with
TTL ≤ 1 hour.

**Ubiquitous.** The `/ingest/v1/file` endpoint on knowledge-ingest shall
enforce both `X-Internal-Secret` and identity assertion via
`klai-identity-assert` middleware (per `SPEC-SEC-IDENTITY-ASSERT-002`).

**Acceptance criteria:**
- AC-9.1: `docker network inspect socket-proxy` does NOT contain
  `docling-serve`, `libreoffice-headless` (Phase 4), `knowledge-ingest`,
  `portal-api`, `garage` (per `SPEC-SEC-SSRF-001` REQ-5 must-not-list).
- AC-9.2: POST without session cookie → 401.
- AC-9.3: POST with valid session for a kb_slug in another org → 404 (not
  403).
- AC-9.4: A `.exe` renamed to `.pdf` returns 400 `mime_mismatch` AND no
  Garage object is created (verified via Garage stats delta).
- AC-9.5: `mc anonymous get-status garage/klai-documents` reports `none`.
- AC-9.6: Concurrent quota-race test: 10 × 600 MB uploads into a fresh 5 GB
  KB → exactly 8 accepted, 2 rejected, no double-spend.
- AC-9.7: POST to `/ingest/v1/file` without `X-Internal-Secret` → 401.
- AC-9.8: portal-api `MIME magic-byte check` runs before
  `klai_document_storage.put_object` — verified by ordered call assertion
  in unit test.

---

## 7. Phasing

Each phase is independently shippable. Phase 1 already replaces the broken
500 with a working text-only path; binary support, archives and `.doc` add
in subsequent phases.

### Phase 1 — text-only path + plumbing

Deliverables:
- Caddy `request_body { max_size 200MB }` on the upload handle.
- New env var `GARAGE_DOCUMENTS_BUCKET=klai-documents` in SOPS, validated
  via pydantic.
- New `klai-libs/document-storage` library (Garage S3 wrapper, presigned-GET).
- New Garage bucket `klai-documents` (private; provisioned manually on
  core-01 via `garage bucket create` + key grant).
- New portal-api route `POST /api/app/knowledge-bases/{kb_slug}/sources/file`
  accepting only `.md`, `.txt`, `.csv`. Other extensions return
  `error_code: "phase_pending"`.
- New `kb_uploads` + `kb_uploads_quota` tables in portal-api (alembic).
- New knowledge-ingest route `POST /ingest/v1/file` for text mimes only.
- Frontend `FileUploadForm` switched to the new endpoint; size guard,
  progress bar, i18n failure-reason map.
- Tests: unit (storage, magic-byte, quota race), integration (.md happy
  path), Playwright (.md upload e2e).

Phase 1 closes the immediate 500-bug for `.md`, `.txt`, `.csv`. Other
formats fail-fast with a clear message instead of a silent Gitea write.

### Phase 2 — docling structuur-pad

Deliverables:
- New `knowledge-ingest/adapters/docling.py` (httpx client to docling-serve
  with timeout + structured chunk parsing).
- knowledge-ingest mime whitelist extends to PDF, DOCX, XLSX, PPTX,
  json_docling, xml_jats, xml_uspto, xml_xbrl.
- portal-api whitelist extends to `.pdf .docx .pptx .xlsx .json .xml`.
- JSON / XML schema detection (heuristic root-element).
- Tests: unit (docling adapter mock), integration (real docling-serve in CI
  compose with sample PDF), Playwright (real `.pdf` end-to-end).

### Phase 3 — archives

Deliverables:
- New/updated `portal-api/app/services/archive.py` with two-stage archive
  handling: metadata preflight, disk-backed staging, per-member validation,
  then all-or-nothing job creation.
- portal-api whitelist extends to `.zip .tar`.
- Per-entry quota counts uncompressed bytes.
- Archive entries become independent `kb_uploads` rows and artifacts with a
  shared `archive_batch_id`; the archive itself is never indexed.
- One archive may contain at most 200 accepted members, at most 10
  Docling-path members, and at most 200 archive complexity units.
- Tests: zip-bomb fixture, path-traversal fixture, nested-archive,
  symlink-in-tar, duplicate normalized path, too-many-Docling-members,
  mixed-content rejection (md + pdf + exe → whole archive rejected),
  Playwright real `.zip` upload.

### Phase 4 — `.doc` legacy support

Deliverables:
- New `libreoffice-headless` sidecar container on `klai-net` (NOT on
  socket-proxy). Custom thin HTTP wrapper around `soffice --headless
  --convert-to docx`.
- New `knowledge-ingest/adapters/libreoffice.py` (HTTP client → docx →
  docling).
- portal-api whitelist extends to `.doc`.
- Tests: integration (.doc → .docx → docling chunks), smoke
  (libreoffice-headless restart-resilience).

---

## 8. Risks & mitigations

| ID | Risk | Severity | Mitigation |
|----|------|----------|------------|
| R-1 | Zip-bomb (42 KB → 4 GB OOM in knowledge-ingest) | HIGH | Streaming decompress with byte counter; abort within 10 MB on ratio > 10:1 OR per-entry > 50 MB OR total > 500 MB. Canary fixture in unit tests. |
| R-2 | docling-serve timeout / hang on large OCR PDF | MED | httpx timeout 600 s; on timeout artifact.failed + failure_reason=docling_timeout; Grafana alert on docling p95 > 300 s. |
| R-3 | Garage S3 unavailable during upload | MED | boto3 retries (3 × exponential backoff). On persistent failure 503 + `storage_unavailable`. Grafana alert on 503-rate. No local-disk fallback (drift risk). |
| R-4 | RLS bypass — IDOR on kb_slug | CRIT | Mandatory `_get_kb_or_404(kb_slug, perms.org_id, db)` before any artifact insert. Integration test for cross-org POST → 404. |
| R-5 | Quota bypass via concurrent uploads | HIGH | `SELECT … FOR UPDATE` on `kb_uploads_quota` row in same tx as artifact insert. Race-test in AC-9.6. |
| R-6 | Mime-spoof (.exe renamed .pdf served via presigned URL → browser exec) | HIGH | Magic-byte validation BEFORE Garage write. Garage Content-Type set from validated mime. Presigned-GET serves Content-Disposition: attachment for non-PDF. |
| R-7 | Fail-open auth on /ingest/v1/file | HIGH | Reuse klai-identity-assert + X-Internal-Secret. Failing tests for missing headers (per fail-open-auth + empty-secret-fail-open pitfalls). |
| R-8 | Caddy 200 MB limit scoped wrong path | MED | Place `request_body` inside path-scoped `handle /api/app/knowledge-bases/*/sources/file`. Test: 200 MB POST to `/api/me` → 413 (still default cap). |
| R-9 | alembic head split | HIGH | `alembic heads` CI guard already enforced. Rebase before merge if main moved (per alembic-multi-pr-head-split pitfall). |
| R-10 | env-var parity gap (GARAGE_DOCUMENTS_BUCKET validator added before SOPS var) | HIGH | SOPS update FIRST; verify decrypt; THEN merge code (per validator-env-parity pitfall). PR body checklist. |
| R-11 | Bind-mount config sync forgotten on libreoffice (Phase 4) | MED | If a libreoffice config file is bind-mounted, follow `infra/deploy.md` § "Bind-mount config sync — required pattern": paths trigger + sparse-checkout + sync_and_recreate. Image-only config = N/A. |
| R-12 | CSV / TXT non-UTF-8 encoding rejected though valid (cp1252) | MED | Best-effort UTF-8 → fallback charset-detect ≥ 0.9 confidence → re-encode to UTF-8 before chunking. Document detected encoding in artifact metadata. |
| R-13 | docling-serve resource exhaustion on multiple large PDFs concurrently | MED | Procrastinate worker concurrency cap at 2 docling-jobs/worker. Monitor docling-serve memory in Grafana. |
| R-14 | Frontend uploads many small files at once → 100/24h rate limit hit fast | LOW | Client batches ≤ 10 files per submit; per-org rate is per-artifact, archives count entries individually. Phase 5 may relax to per-byte quota. |
| R-15 | `kb-documents` bucket reachable from internet via DNS-leak | MED | Garage `klai-documents` bucket has no website-mode binding; only S3 API on internal port 3900 (not exposed via Caddy). Verified by AC-9.5. |
| R-16 | Archive partial ingest creates a misleading KB | HIGH | All-or-nothing archive staging: validate every member before creating any upload rows, Docling tasks, artifacts, or Qdrant points. One bad member rejects the whole archive. |
| R-17 | Archive staging OOMs portal-api | HIGH | Stream members to disk-backed temp files/object storage; never accumulate all decompressed bytes in memory. AC-3.12 covers RSS. |

---

## 9. Code impact

| File | Phase | Shape |
|------|-------|-------|
| `deploy/caddy/Caddyfile` | 1 | +`request_body { max_size 200MB }` inside path-scoped `handle /api/app/knowledge-bases/*/sources/file`, before `reverse_proxy portal-api:8010`. |
| `deploy/docker-compose.yml` | 1 | New env `GARAGE_DOCUMENTS_BUCKET: ${GARAGE_DOCUMENTS_BUCKET}` on `portal-api` and `knowledge-ingest`. |
| `deploy/docker-compose.yml` | 4 | New service `libreoffice-headless` on `klai-net` only; klasse-A labels via compose. |
| `klai-libs/document-storage/{__init__.py, pyproject.toml, klai_document_storage/{client.py, content_addr.py, presign.py, errors.py}}` | 1 | NEW lib (~300 LOC). Public API `DocumentStore.put(stream, mime, bytes) → s3_key`, `presigned_get(s3_key, ttl) → url`. Mirrors klai-libs/image-storage. |
| `klai-portal/backend/app/api/app_knowledge_sources.py` | 1 | +`POST /knowledge-bases/{kb_slug}/sources/file` (~120 LOC). Calls `services/file_upload.py`. |
| `klai-portal/backend/app/services/file_upload.py` | 1-3 | NEW (~200 LOC growth across phases): orchestration, multipart streaming, magic-byte, archive (Phase 3), Garage write, ingest enqueue. |
| `klai-portal/backend/app/services/kb_uploads_quota.py` | 1 | NEW (~80 LOC): `assert_can_upload_bytes(kb_id, bytes_to_add)` with FOR UPDATE. |
| `klai-portal/backend/app/core/features.py` | 1 | +`"kb_file_upload": ProfileRole.PERSONAL` in `FEATURE_MIN_PROFILE`. |
| `klai-portal/backend/app/core/config.py` | 1 | +`garage_documents_bucket: str` setting with `_require_garage_documents_bucket` validator. |
| `klai-portal/backend/alembic/versions/<rev>_kb_uploads.py` | 1 | NEW table `kb_uploads(artifact_id UUID PK, kb_id, org_id, s3_key, filename, mime, bytes, status, failure_reason, created_at, updated_at)` + `kb_uploads_quota(kb_id PK, bytes_used)` + RLS policies (cat-D) + indexes. |
| `klai-portal/backend/app/api/internal.py` (or new) | 1 | Webhook receiver for knowledge-ingest status updates: `POST /internal/kb-uploads/status`. |
| `klai-knowledge-ingest/knowledge_ingest/routes/ingest.py` | 1, 2, 3, 4 | +`POST /ingest/v1/file` (~80 LOC initial; grows per phase). |
| `klai-knowledge-ingest/knowledge_ingest/adapters/docling.py` | 2 | NEW (~150 LOC): httpx client to docling-serve, schema detection, chunk normalization. |
| `klai-portal/backend/app/services/archive.py` | 3 | NEW/UPDATED (~250 LOC): archive preflight, disk-backed staging, all-or-nothing validation, and member metadata. |
| `klai-knowledge-ingest/knowledge_ingest/adapters/libreoffice.py` | 4 | NEW (~80 LOC): HTTP client → docx → docling. |
| `klai-knowledge-ingest/knowledge_ingest/config.py` | 1, 4 | +`docling_url: str`, `libreoffice_url: str` (Phase 4). |
| `klai-knowledge-ingest/alembic/versions/<rev>_artifact_file_source.py` | 1 | If existing artifacts table needs new jsonb fields (`source_kind`, `s3_key`, `original_filename`), add via `extra` jsonb (no schema change). May be a no-op if jsonb is already free-form. |
| `klai-portal/frontend/src/routes/app/knowledge/$kbSlug_.add-source._components/FileUploadForm.tsx` | 1 | Endpoint flip; drop DOCS_BASE; client-side size guard; XHR progress; i18n error map. |
| `klai-portal/frontend/src/lib/kb-editor/tree-utils.ts` | 1 | Comment clarifying DOCS_BASE is wiki-only. |
| `klai-portal/frontend/src/paraglide/messages/{nl,en}/*` | 1 | New i18n strings per AC-7.4. |
| `.claude/rules/klai/projects/portal-backend.md` | 1 | New entry: "KB file upload — magic-byte before storage, Garage presigned-GET, FOR UPDATE quota". |
| `docs/runbooks/kb-file-upload.md` | 1 | NEW runbook: rollout order, debug failed uploads via VictoriaLogs, quota override, Garage bucket recreate. |
| `docs/architecture/klai-knowledge-architecture.md` | 1 | Update §4 ingestion architecture to reference the file path. |
| `klai-infra/core-01/.env.sops` | 1 | +`GARAGE_DOCUMENTS_BUCKET=klai-documents`. |

---

## 10. Test plan

### Phase 1

**Unit (portal-api):**
- `test_document_storage.py` — Garage `put` round-trip, presigned-GET TTL,
  content-address sha256.
- `test_magic_byte_validation.py` — extension/mime mismatch matrix (12
  legit + 12 spoofed cases).
- `test_kb_uploads_quota.py` — FOR UPDATE serializes, daily rate limit,
  per-KB byte cap.
- `test_file_upload_orchestrator.py` — text path: `.md` → ingest call;
  `.pdf` (Phase 1) → 400 phase_pending.

**Unit (knowledge-ingest):**
- `test_ingest_v1_file_text.py` — `.md` body → markdown chunker → pg_store
  + Qdrant index call (mocked).

**Integration:**
- portal-api → knowledge-ingest happy path with stub Qdrant: upload `.md`,
  poll status, assert `done`.
- Cross-org RLS: caller_org=A POST kb_slug from org=B → 404.
- Quota race: 10 × 600 MB into fresh 5 GB KB → 8/2 split.

**Playwright e2e:**
- Login as test user → /app/knowledge/test-kb/add-source → drag `sample.md`
  → click Upload → wait for success banner → assert redirect to overview →
  assert sample.md row in source list with status "Verwerkt".
- Drag `oversized.md` (210 MB stub via repeating string) → assert client-side
  rejection with NL message before any network call.

### Phase 2

**Unit (knowledge-ingest):**
- `test_docling_adapter.py` — mock httpx; assert correct format param per
  extension; schema detection fixtures (json_docling, JATS XML).

**Integration:**
- Real `docling-serve` in CI compose. 5-page sample PDF → ≥ 5 chunks with
  `page_numbers` populated.
- Real DOCX → docling → chunks.

**Playwright e2e:**
- Upload **real chemie-PDF fixture** (small, ~5 MB; full 105 MB only in
  manual production smoke). Poll until `done`. Assert kennisbank-overview
  shows ≥ 100 chunks attributed to that artifact via API.

### Phase 3

**Unit (knowledge-ingest):**
- `test_archive_zip_bomb.py` — canonical 42 KB → 4 GB fixture; assert abort
  within 10 MB.
- `test_archive_path_traversal.py` — entries `../../etc/passwd`, absolute
  paths, backslashes.
- `test_archive_nested.py` — `nested.zip` inside `outer.zip` rejected.
- `test_archive_symlink.py` — tar symlink REGTYPE check.
- `test_archive_mixed.py` — `[1.md, 2.pdf, 3.exe]` → 2 artifacts + 1 skipped.

**Integration:**
- Upload real `.zip` with 5 mixed valid entries → 5 artifacts each with own
  chunks.

**Playwright e2e:**
- Upload `chapters.zip` → 5 sources appear in KB overview within 60 s.

### Phase 4

**Integration:**
- Real `legacy.doc` → libreoffice → `.docx` → docling chunks → Qdrant
  searchable.

**Smoke:**
- `docker restart libreoffice-headless` while upload in flight → conversion
  retried successfully.

---

## 11. Rollout sequence

Per phase, follow this order to satisfy `validator-env-parity` and
`bind-mount-without-sync-workflow` pitfalls.

### Phase 1

1. **SOPS** — add `GARAGE_DOCUMENTS_BUCKET=klai-documents` to
   `klai-infra/core-01/.env.sops`. Push klai-infra; CI sync-env workflow
   (no `--allow-removal` needed). Verify on core-01: `docker exec
   klai-core-portal-api-1 printenv GARAGE_DOCUMENTS_BUCKET` returns the
   value (after step 4).
2. **Garage bucket** — manual on core-01:
   ```
   ssh core-01 "docker exec klai-core-garage-1 /garage bucket create klai-documents"
   ssh core-01 "docker exec klai-core-garage-1 /garage bucket allow \
       --read --write --owner --key portal-api klai-documents"
   ```
   Verify: `garage bucket info klai-documents` shows portal-api access; `mc
   anonymous get-status` returns `none`.
3. **Caddy patch** — push to `deploy/caddy/Caddyfile`. The
   `deploy-compose.yml` workflow auto-syncs the file via
   `sync_and_recreate caddy` (Class-A bind-mount). Verify on core-01:
   `cat /opt/klai/caddy/Caddyfile | grep request_body`.
4. **portal-api code** — merge PR. CI builds `ghcr.io/getklai/portal-api`,
   `portal-api.yml` workflow recreates the container.
   `entrypoint.sh` runs `alembic upgrade head` (per
   `SPEC-DEPLOY-AUTO-MIGRATE-001`); the new `kb_uploads` migration is
   applied. Verify: `docker exec klai-core-portal-api-1 alembic current`
   shows the new head; `docker exec ... printenv GARAGE_DOCUMENTS_BUCKET`
   matches SOPS.
5. **knowledge-ingest code** — same flow.
6. **Frontend** — merge PR. `klai-portal.yml` deploys; verify bundle
   timestamp on `/srv/portal/assets/*.js` matches deploy time.
7. **e2e smoke** — upload `sample.md` as a test user via
   `https://my.getklai.com`. Verify chunks via
   `victorialogs query 'service:knowledge-ingest event:kb_file_extracted
   status:done'`. Verify Grafana panel `KB Uploads — Hourly` shows 1
   accepted upload.

### Phases 2-4

Same pattern, smaller surfaces. Phase 4 adds the libreoffice-headless
container which goes through `deploy-compose.yml` workflow (Class-A
bind-mount sync if any config file bind-mount, image-only otherwise).

---

## 12. Open questions (resolve in run phase)

- Q-A: Existing `artifacts.extra` jsonb shape — confirm whether
  `source_kind` / `s3_key` already used elsewhere; if so, namespace under
  `file_upload.{...}` to avoid key collision.
- Q-B: Is there an existing artifact-status webhook from knowledge-ingest
  to portal-api, or do we add a new `POST /internal/kb-uploads/status`?
  (Prefer reuse; check during Phase 1 implementation.)
- Q-C: `kb_uploads_quota` granularity — per kb_id (matches REQ-5 wording)
  vs. per (org_id, scope). Phase 1 default: per kb_id.
- Q-D: Should presigned-GET URLs require an authenticated portal-api
  session (BFF cookie) or are they self-contained TTL URLs? Phase 1
  default: self-contained TTL ≤ 1 h, generated only inside an
  authenticated handler.

---

## 13. Acceptance: definition of done for THIS spec

This SPEC is `approved` (status: approved) when:

- All 9 EARS requirements have testable acceptance criteria with
  measurable outcomes.
- Architecture decisions D-1..D-6 are recorded with rationale.
- Risk register R-1..R-15 each maps to a concrete mitigation.
- Code-impact table covers every file with the change shape.
- Test plan maps tests to acceptance criteria.
- Rollout sequence respects validator-env-parity, alembic-stamped-past,
  bind-mount-sync, and gh-cleanup-cross-worktree pitfalls.

This SPEC is `done` (status: done) when:

- Phase 1, 2, 3 are merged to main, deployed to core-01, and a smoke test
  upload of the canonical chemie-PDF fixture (105 MB) returns chunks
  retrievable via `retrieval-api /retrieve` for the test KB.
- Phase 4 is in scope for closure; if deferred to a follow-up SPEC, it is
  explicitly recorded here as such.

<moai>DONE</moai>
