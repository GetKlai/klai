# KB file upload — operator runbook

> SPEC-KB-FILE-UPLOAD-001 — file ingestion for `.md / .txt / .csv /
> .pdf / .docx / .pptx / .xlsx / .json / .xml`. Archive (`.zip / .tar`)
> and `.doc` are documented as `phase_pending` in the UI; they ship in
> a follow-up.

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
```

## What's live in each service

| Service | Touches |
|---|---|
| Caddy | `request_body { max_size 200MB }` on `^/api/app/knowledge-bases/[^/]+/sources/file$` |
| portal-api | new `POST /api/app/knowledge-bases/{kb}/sources/file`, new `GET .../sources/file/{id}/status`, new `kb_uploads` table + cat-D RLS, `kb_upload_poller` background task |
| docling-serve | v1.16.1 on klai-net; async hybrid chunk endpoint; non-single-use result retention configured for retry tolerance |
| knowledge-ingest | receives either normal text content or Docling pre-computed chunks via the existing `/ingest/v1/document` endpoint |

## Deploy steps

1. **Merge the PR.** CI handles the portal-api image build, the frontend
   bundle deploy, and the Caddyfile sync.
2. **Verify alembic ran.** portal-api auto-migrates on container start
   (`entrypoint.sh` runs `alembic upgrade head`). Confirm:

   ```bash
   ssh core-01 "docker exec klai-core-portal-api-1 alembic current"
   # Expect: 85e5d0a7cb98 (head)
   ```

3. **Apply the post-deploy RLS SQL as `klai` superuser.** portal_api
   role does not own `kb_uploads` and cannot ENABLE RLS on it. Run:

   ```bash
   ssh core-01 "docker cp klai-core-postgres-1:/dev/null /tmp/post_deploy.sql"  # placeholder
   # Either copy the file in:
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
   upload a small PDF (5 pages). For the regression path, upload
   `/Users/mvletter/Downloads/Chemie Overal 4VWO.pdf` to a fresh test KB
   and watch it reach `done` with non-zero chunks.

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

Two causes:

1. **docling-serve restarted and lost the task.** `task_id` no longer
   resolves; poller logs `docling result fetch failed`. Solution:
   manual UPDATE to `failed` so the user can re-upload, then ask the
   user to re-submit.
2. **Poller stopped.** Check `kb_upload_poller_started` log on
   portal-api startup. Restart portal-api if missing.

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

`app/services/archive.py` implements stdlib-only safe extraction with
sunzip-style guards. Each archive is unpacked **in memory** under:

| Guard | Value |
|---|---|
| Member count | 50 |
| Per-entry uncompressed | 50 MB |
| Total uncompressed | 500 MB |
| Compression ratio | 10:1 (after 1 MB output — catches 42 KB → 4 GB bombs) |
| Path traversal | reject `..`, `/`, `\`, NUL, drive letters |
| Nested archives | reject `.zip` / `.tar` entries (no recursion) |
| Symlinks / devices (tar) | only `REGTYPE` extracted |
| Whitelist per entry | only TEXT or DOCLING extensions |

Each surviving member recurses through the same `_dispatch_blob`
helper as a top-level file (with `allow_archive=False`). Successful
entries become independent `kb_uploads` rows — the user sees one row
per file in the archive in the UI.

## Out of scope (next iterations)

- `.doc` legacy support — needs a `libreoffice-headless` sidecar
  container to convert to `.docx` before docling.
- Garage S3 backing store — current architecture uses docling-serve's
  internal task storage. A future iteration can persist the original
  binary in Garage for audit + re-process at the cost of one extra
  hop. See SPEC-KB-FILE-UPLOAD-001 §7 for the design.
