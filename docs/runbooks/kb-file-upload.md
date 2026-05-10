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
                                             POST docling-serve /v1/convert/file/async
                                             → kb_uploads.status=processing + docling_task_id
                                                   │
                                                   ▼
                                         portal-api kb_upload_poller (every 5 s)
                                             docling /v1/status/poll/{task_id}
                                             → success: GET /v1/result → markdown
                                                        → /ingest/v1/document
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
| docling-serve | unchanged — uses the existing v1.16.1 deployment on klai-net |
| knowledge-ingest | unchanged — receives docling-derived markdown via the existing `/ingest/v1/document` endpoint |

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
   scp klai-portal/backend/alembic/versions/post_deploy_85e5d0a7cb98_kb_uploads_rls.sql \
       core-01:/tmp/post_deploy_kb_uploads.sql
   ssh core-01 "docker cp /tmp/post_deploy_kb_uploads.sql klai-core-postgres-1:/tmp/"
   ssh core-01 "docker exec klai-core-postgres-1 sh -c \
       'psql -U \$POSTGRES_USER -d \$POSTGRES_DB -f /tmp/post_deploy_kb_uploads.sql'"
   ```

   Verify:

   ```bash
   ssh core-01 "docker exec klai-core-postgres-1 sh -c \
       'psql -U \$POSTGRES_USER -d \$POSTGRES_DB -c \
       \"SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = '\''kb_uploads'\'';\"'"
   # Expect: (t, t)
   ```

4. **Smoke test.** Upload `sample.md` via the UI on a test KB. Then
   upload a small PDF (5 pages). Watch:

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

## Out of scope (next iterations)

- `.zip / .tar` archive support — needs sunzip-style guards (compression
  ratio cap, per-entry size, path-traversal) that warrant their own
  module + tests.
- `.doc` legacy support — needs a `libreoffice-headless` sidecar
  container to convert to `.docx` before docling.
- Garage S3 backing store — current architecture uses docling-serve's
  internal task storage. A future iteration can persist the original
  binary in Garage for audit + re-process at the cost of one extra
  hop. See SPEC-KB-FILE-UPLOAD-001 §7 for the design.
