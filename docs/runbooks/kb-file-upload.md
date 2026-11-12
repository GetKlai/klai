# KB file upload — operator runbook

> SPEC-KB-FILE-UPLOAD-001 — file ingestion for `.md / .txt / .csv /
> .pdf / .docx / .pptx / .xlsx / .json / .xml`. Archives (`.zip / .tar`) are
> live as an all-or-nothing container: preflight, extract with caps, validate
> every member, then dispatch each member as its own upload. Legacy `.doc` is
> `phase_pending` and not processed.

## Architecture

```
Browser ──multipart 200 MB──▶ Caddy ──▶ portal-api
                                         │
                                         ├─ text path  (.md/.txt/.csv)
                                         │   decode → /ingest/v1/document → kb_uploads.status=done
                                         │
                                         └─ docling path (.pdf/.docx/.pptx/.xlsx/.json/.xml)
                                             magic-byte validate
                                             POST docling-serve /v1/chunk/hybrid/file/async
                                             include_converted_doc=false
                                             convert_image_export_mode=placeholder
                                             → kb_uploads.status=processing + docling_task_id
                                                   │
                                                   ▼
                                         portal-api kb_upload_poller (every 5 s)
                                             docling /v1/status/poll/{task_id}
                                             → success: GET /v1/result → chunks[].text
                                                        → /ingest/v1/document
                                                          skip_chunking=true
                                                          chunks=[...]
                                                          content=<bounded preview>
                                                          content_hash=<source sha256>
                                                        → kb_uploads.status=done
                                             → failure: kb_uploads.status=failed
                                         frontend polls
                                         /api/app/knowledge-bases/{kb}/sources/file/{id}/status
                                         every 2 s until terminal

archive path (.zip/.tar):
Browser ──multipart archive──▶ portal-api archive preflight
                              ├─ any bad member: reject whole archive, no rows/jobs
                              └─ all members valid:
                                   members staged in spooled temp files
                                   (disk above 1 MB)
                                   each member re-dispatched as its own upload
                                   text members → direct ingest
                                   docling members → Docling async queue + poller
```

## What's live in each service

| Service | Touches |
|---|---|
| Caddy | `request_body { max_size 200MB }` on `^/api/app/knowledge-bases/[^/]+/sources/file$` |
| portal-api | new `POST /api/app/knowledge-bases/{kb}/sources/file`, new `GET .../sources/file/{id}/status`, new `kb_uploads` table + cat-D RLS, `kb_upload_poller` background task |
| docling-serve | v1.38.2 on klai-net (`deploy/docker-compose.yml`); async hybrid chunk endpoint; non-single-use result retention configured for retry tolerance |
| knowledge-ingest | receives either normal text content or Docling pre-computed chunks via the existing `/ingest/v1/document` endpoint |

## Deploy steps

1. **Merge the PR.** CI handles the portal-api image build, the frontend
   bundle deploy, and the Caddyfile sync.
2. **Verify alembic ran.** portal-api auto-migrates on container start
   (`entrypoint.sh` runs `alembic upgrade head`). Confirm:

   ```bash
   ssh core-01 "docker exec klai-core-portal-api-1 alembic current"
   ssh core-01 "docker exec klai-core-portal-api-1 alembic heads"
   # Both print the same revision id
   ```

3. **Apply the post-deploy RLS SQL as `klai` superuser.** portal_api
   role does not own `kb_uploads` and cannot ENABLE RLS on it. Run:

   ```bash
   scp klai-portal/backend/alembic/versions/post_deploy_85e5d0a7cb98_kb_uploads_rls.sql \
       core-01:/tmp/post_deploy.sql
   ssh core-01 "docker cp /tmp/post_deploy.sql klai-core-postgres-1:/tmp/"
   ssh core-01 "docker exec klai-core-postgres-1 sh -c \
       'psql -U \$POSTGRES_USER -d \$POSTGRES_DB -f /tmp/post_deploy.sql'"
   ```

   Verify:

   ```bash
   ssh core-01 "docker exec klai-core-postgres-1 sh -c \
       'psql -U \$POSTGRES_USER -d \$POSTGRES_DB -c \
       \"SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = '\''kb_uploads'\'';\"'"
   # Expect: (t, t)
   ```

4. **Smoke test.** Upload `sample.md` via the UI on a test KB. Then
   upload a small PDF (5 pages). For the large-document path, upload a
   textbook-size PDF to a fresh test KB and watch it reach `done` with
   non-zero chunks.

   ```bash
   # Should show one done + one processing within seconds
   ssh core-01 "docker logs --tail 50 klai-core-portal-api-1 | grep kb_upload_received"

   # Should show the poller advancing the PDF row through processing→done
   ssh core-01 "docker logs --tail 50 klai-core-portal-api-1 | grep kb_upload_done"
   ```

## What can go wrong

### docling-serve unreachable on klai-net

Symptom: every PDF upload returns `extraction_failed` immediately on
submission.

Diagnose:

```bash
ssh core-01 "docker exec klai-core-portal-api-1 sh -c \
    'curl -sf http://docling-serve:5001/health > /dev/null && echo OK || echo FAIL'"
```

If `FAIL`: `docker compose ps docling-serve` on core-01. The container
is internal-only (no Caddy route), so external probes are not useful.

### kb_uploads stuck in `processing` forever

The poller ends a row by itself in these cases (`klai-portal/backend/app/services/kb_upload_poller.py`):

- A row that docling keeps reporting as not finished is failed with
  `processing_deadline_exceeded` after 3 hours (`:57`), logged as
  `kb_upload_processing_deadline_exceeded` (`:62-90`).
- A failed status call to docling-serve fails the row with `docling_unreachable`
  (`:126-131`).
- A failed result fetch after docling reported success logs
  `kb_upload_result_fetch_failed` and fails the row with `extraction_failed`
  (`:155-165`).
- An `ingesting` row whose result is gone fails with `docling_result_not_found`
  (`:196-201`, next section).

A row that stays in `processing` or `ingesting` past those limits means the
**poller stopped**. Check the `kb_upload_poller_started` log on portal-api
startup and restart portal-api if it is missing. Only for a row the poller cannot
see, mark it `failed` by hand so the user can re-upload.

Manual recovery query:

```sql
-- Stale rows still pending after 10 minutes
SELECT id, filename, docling_task_id, status, updated_at, NOW() - updated_at AS age
FROM kb_uploads
WHERE status IN ('processing', 'ingesting')
  AND updated_at < NOW() - INTERVAL '10 minutes'
ORDER BY updated_at ASC;

-- Mark a single row as failed (operator decision)
UPDATE kb_uploads
SET status = 'failed', failure_reason = 'operator_recovery', updated_at = NOW()
WHERE id = '<uuid>';
```

### Docling success followed by `docling_result_not_found`

This means portal-api observed terminal success but could not fetch the
result later. Docling Serve defaults are single-use results with a short
removal delay, so Klai production must run docling-serve with:

```yaml
DOCLING_SERVE_SINGLE_USE_RESULTS: "false"
DOCLING_SERVE_RESULT_REMOVAL_DELAY: "86400"
```

Diagnose the live container environment:

```bash
ssh core-01 "docker exec klai-core-docling-serve-1 sh -c \
    'env | grep DOCLING_SERVE_ | sort'"
```

If the variables are missing after a merge, redeploy the compose stack or
recreate the docling-serve container. Existing failed upload rows cannot be
recovered from Docling if the result was already removed; re-upload the source
after fixing the environment.

### knowledge-ingest returns HTTP 422 for a large document

The docling path must not forward one giant converted document in
`content`. The expected request shape to `/ingest/v1/document` is:

```json
{
  "content": "<= 450000 chars preview",
  "content_hash": "<original file sha256>",
  "skip_chunking": true,
  "chunks": ["Docling chunk text", "..."]
}
```

If logs show `skip_chunking=false` or no `chunks`, the portal-api image is
not running the Docling chunk pipeline. If `content` is larger than 500,000
characters, the preview bound has regressed.

### Large document indexed but enrichment fails or retries

Every chunk is written with its dense and its sparse (BGE-M3) vector at ingest,
without an LLM call, so a large document is hybrid-searchable at once. The
context sentence and the questions follow in batches: a document above
`enrichment_max_chunks` (200) is enriched 200 chunks per `enrich_document_bulk`
task, each next batch queued behind the other documents' work. The chunks come
from the indexed points, so a truncated Docling preview in Postgres does not
limit them. Expected log sequence for a 450-chunk document:

```text
event=enrichment_complete chunks=200 chunk_offset=0
event=enrichment_batch_queued batch_offset=200
event=enrichment_complete chunks=200 chunk_offset=200
event=enrichment_batch_queued batch_offset=400
event=enrichment_complete chunks=50 chunk_offset=400
```

Only a document above `enrichment_hard_max_chunks` (2000 chunks, about 56
minutes of the shared klai-ingest budget at 0.6 requests per second) is not
enriched. It keeps its raw chunks and its artifact extra carries:

```json
{
  "llm_enrichment_skipped": true,
  "llm_enrichment_skip_reason": "too_many_chunks"
}
```

If a batch fails permanently the chain stops there: chunks before it carry
their context, chunks after it stay raw (still dense and sparse). Requeue the
artifact with `knowledge_ingest.scripts.requeue_failed_artifacts`; that starts
over at chunk 0. The script selects active artifacts with `index_status = 'failed'`
(`klai-knowledge-ingest/knowledge_ingest/scripts/requeue_failed_artifacts.py:24-27,64-80`),
and a failed enrichment only sets that status for an upload without a source
connector whose first batch failed (`klai-knowledge-ingest/knowledge_ingest/enrichment_tasks.py:485-500,1233-1253`).
A later batch that failed, or a connector document, stays `synced` and is not
found by the script. For an upload whose later batch failed, the reindex route
re-enqueues enrichment (`POST /knowledge/v1/artifacts/{id}/reindex`,
`klai-knowledge-ingest/knowledge_ingest/routes/kb_sources.py:243-300`).

### Chunks without a sparse vector

Chunks written before the base ingest embedded sparse vectors, and chunks
written while the sparse sidecar was down, have only the dense vector, so
retrieval-api's hybrid search is dense-only for them. Count them per knowledge
base (read-only; this is the dry run), then repair:

```bash
docker exec klai-core-knowledge-ingest-1 \
    python -m knowledge_ingest.scripts.sparse_backfill --org <org_id>
docker exec klai-core-knowledge-ingest-1 \
    python -m knowledge_ingest.scripts.sparse_backfill --org <org_id> --execute
```

The table lists, per knowledge base, the chunks, those with and without a
sparse vector, and those with a context sentence and with questions. `--execute`
writes only `vector_sparse` on the points that lack it, in batches of the
sidecar batch size, and prints the table again. It is safe to rerun or stop;
`--kb <slug>` limits it to one knowledge base. A sidecar that cannot answer
shows up as `failed` in the summary and the points stay selected for the next
run. A container that is not the live one (see `docker run` clone of the
service) works the same, and is the safer way to run a long backfill because a
deploy recreates the live container.

### portal-api container OOM during upload

200 MB binary uploads land in Starlette's `SpooledTemporaryFile` which
spools to disk above ~1 MB. RSS impact stays below 50 MB per concurrent
upload. If OOM is observed: check container memory limits in
`deploy/docker-compose.yml` for portal-api.

## Quotas and limits

| Limit | Value | Source |
|---|---|---|
| Per-file size cap | 200 MB | Caddy `request_body` + `MAX_BINARY_FILE_BYTES` |
| Per-text-file size cap | 10 MB | `MAX_TEXT_FILE_BYTES` |
| Files per upload request | 10 | `_MAX_FILES_PER_REQUEST` |
| Per-KB item count | per-plan | existing `assert_can_add_item_to_kb` |

## Observability

VictoriaLogs queries:

- `service:portal-api AND event:kb_upload_received` — every accept/reject decision
- `service:portal-api AND event:kb_upload_done` — completed uploads
- `service:portal-api AND event:kb_upload_docling_failed` — failed docling tasks
- `service:portal-api AND event:kb_upload_poll_docling_error` — poller couldn't reach docling
- `service:portal-api AND event:kb_upload_poll_transient` — docling slow (>5 s status poll)
- `service:portal-api AND event:docling_submit_accepted` — submitted task IDs and source byte size
- `service:portal-api AND event:docling_markdown_embedded_images_stripped` — defensive evidence that embedded image payloads were removed

## Archive pipeline (`.zip` / `.tar`)

Archive files are transport containers, not documents. The archive itself is
never sent to Docling or indexed as one artifact. The flow
(`klai-portal/backend/app/api/app_knowledge_sources.py:579-640`):

1. Preflight archive metadata before decompression where possible.
2. Reject the whole archive if any member is not acceptable.
3. Stream member payloads into spooled temporary files (disk above 1 MB).
4. Run the same per-file validation as direct uploads on every member.
5. Only after every member is valid, dispatch each member through the same
   dispatcher as a direct upload, one upload row per member.

This means unsupported files inside an archive are not skipped. A zip containing
`notes.md`, `handbook.pdf`, and `script.exe` is rejected entirely. This is
intentional: partial archive ingest makes a KB look complete when it is not.

Preflight can catch member count, paths, extensions, nested archives, duplicate
normalized paths, tar member types, and declared sizes. It cannot fully prove
compression ratio or magic-byte correctness. Those checks still happen while
streaming the member content.

`klai-portal/backend/app/services/archive.py` does stdlib-only safe extraction
with sunzip-style guards; its caps are the constants at the top of that file.

| Guard | Value |
|---|---|
| Member count | 200 file members |
| Docling-path members | 10 |
| Archive complexity budget | 200 units |
| Per-entry uncompressed | 50 MB |
| Total uncompressed | 500 MB |
| Compression ratio | 10:1 (after 1 MB output — catches 42 KB → 4 GB bombs) |
| Path traversal | reject NUL, absolute paths, `..`, drive letters, paths outside archive root |
| Duplicate normalized paths | reject whole archive |
| Nested archives | reject whole archive on `.zip` / `.tar` entries |
| Symlinks / devices (tar) | reject whole archive; only `REGTYPE` / `AREGTYPE` allowed |
| Whitelist per entry | `.csv`, `.docx`, `.json`, `.md`, `.pdf`, `.pptx`, `.txt`, `.xlsx`, `.xml` |

Complexity units:

| Member shape | Units |
|---|---:|
| `.md`, `.txt`, `.csv` up to 1 MB | 1 |
| `.md`, `.txt`, `.csv` above 1 MB | 2 |
| `.json`, `.xml` | 3 |
| `.docx`, `.xlsx`, `.pptx` | 5 |
| `.pdf` up to 10 MB | 10 |
| `.pdf` above 10 MB | 10 + 1 per additional started 10 MB |

Accepted entries go through the same direct upload dispatcher with
`allow_archive=False`. Text entries complete through direct ingest; Docling entries enter the Docling
queue. Each member gets its own source row; the archive does not.
