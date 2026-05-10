# SPEC-KB-FILE-UPLOAD-001 — Implementation Progress

## Phase 1A — text-path closure of the 500-bug

**Status:** code complete, awaiting deploy.
**Date:** 2026-05-10
**Branch:** mvletter/large-pdf-upload-fails (Conductor workspace)

### What landed in this commit

- `klai-portal/backend/app/services/file_upload.py` — new validation
  service: extension whitelist, magic-byte gate stub (decode-based for
  text path), UTF-8 / cp1252 normalisation with BOM strip, content-
  addressed source_ref.
- `klai-portal/backend/app/api/app_knowledge_sources.py` — new route
  `POST /api/app/knowledge-bases/{kb_slug}/sources/file` that accepts
  multipart `files`, validates each, accepts `.md` `.txt` `.csv` and
  forwards to the existing `/ingest/v1/document` text pipeline. Other
  whitelisted formats (`.pdf .docx .pptx .xlsx .json .xml .zip .tar
  .doc`) return `phase_pending` per file in the `skipped` array; the
  whole-request rejection happens only when nothing is accepted.
- `klai-portal/backend/tests/test_file_upload.py` — 48 unit tests on the
  validation helpers (extension classifier, BOM strip, encoding
  fallback, size cap, source_ref dedupe).
- `klai-portal/backend/tests/test_app_knowledge_sources_file.py` — 9
  integration tests: happy path for each text format, `.pdf →
  phase_pending` regression test, mixed `.md + .pdf` partial-success,
  rejection paths.
- `klai-portal/frontend/src/routes/app/knowledge/$kbSlug_.add-source.
  _components/FileUploadForm.tsx` — endpoint flipped from
  `${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/upload` (klai-docs wiki) to
  `/api/app/knowledge-bases/${kbSlug}/sources/file` (portal-api). Drops
  `DOCS_BASE` and `getOrgSlug` imports. Client-side per-file 10 MB cap
  + extension allowlist (`.md .txt .csv` Phase 1A). New
  `serverSkipped` UI rail surfaces per-file `phase_pending` so the user
  sees which files were rejected. Drop-zone copy updated to "Markdown,
  TXT, CSV" + "PDF, Word, Excel, PowerPoint volgen binnenkort".
- `deploy/caddy/Caddyfile` — `request_body { max_size 10MB }` scoped
  to `^/api/app/knowledge-bases/[^/]+/sources/file$` via `path_regexp`,
  placed before the general `/api/*` handler so other endpoints retain
  the default request-body limit. Phase 1B raises this to 200 MB when
  binary streaming lands.

### Tests passing

- `tests/test_file_upload.py` — 48 unit tests
- `tests/test_app_knowledge_sources_file.py` — 9 route integration tests
- `tests/test_app_knowledge_sources.py` — 14 (no regression, existing
  url/text routes still pass)
- `tests/test_caddyfile_static_route_position.py` — 2 (Caddy lint
  invariant intact)
- Frontend `tsc --noEmit --ignoreDeprecations 6.0` — clean

Total in scope: **71 backend tests + frontend type-check green.**

### Acceptance criteria mapped

Phase 1A satisfies a subset of REQ-1 / REQ-6 / REQ-7 from the SPEC:

- [x] AC-1.1 partial — `.md .txt .csv` upload returns 202 with
  artifact_id list (text path only).
- [x] AC-1.2 — `.exe` returns 400 `unsupported_extension`.
- [x] AC-1.5 — text formats decode UTF-8 first; cp1252 fallback;
  invalid encoding returns 400 `invalid_text_encoding`.
- [x] AC-2.2 partial — Caddy `max_size 10MB` returns 413 for oversize
  on this path (Phase 1B raises to 200 MB).
- [x] AC-6.1 — endpoint returns `{ uploads: [...], skipped: [...] }`.
- [x] AC-6.2 — archive entries n/a in Phase 1A; skipped array carries
  `phase_pending` rejections instead.
- [x] AC-6.3 — failure_reason is non-null and from a documented enum.
- [x] AC-7.1 — `FileUploadForm.tsx` no longer imports `DOCS_BASE`.
- [x] AC-7.2 — client-side size guard rejects > 10 MB before network.
- [x] AC-7.4 partial — main failure_reasons mapped to NL strings
  (full Paraglide string-table migration in Phase 1B).
- [x] AC-7.6 partial — accept attribute is `.md,.txt,.csv` (Phase 1A
  whitelist; full whitelist lands in Phase 1B + 2 + 3).

### Acceptance criteria deferred (Phase 1B / 2 / 3 / 4)

These are documented in spec.md §7 Phasing and scope-to-follow-up
commits:

- AC-1.4 (OOX validation), AC-9.4 (magic-byte before storage write):
  needs Garage upload path, lands Phase 1B.
- AC-2.3 / AC-2.4 / AC-2.5 (200 MB streaming, RSS bound, p95 < 60s):
  needs `klai-libs/document-storage` + Garage, Phase 1B.
- AC-3.x (archive safety): needs `adapters/archive.py`, Phase 3.
- AC-4.x (docling routing): needs `adapters/docling.py`, Phase 2.
- AC-5.4 (FOR UPDATE concurrent quota race): needs `kb_uploads_quota`
  table + helper, Phase 1B. Phase 1A reuses existing
  `assert_can_add_item_to_kb` (item-count quota, not byte quota).
- AC-8.x (Grafana panel `KB Uploads — Hourly`): structlog events
  `kb_upload_received` already emitted; Grafana panel definition is a
  separate config-as-code commit.
- AC-9.5 (private bucket policy): needs Garage bucket provisioning,
  Phase 1B.

### Divergence from SPEC §7 Phase 1 deliverables

Per `spec-discipline` rule (HARD), this Phase 1A is intentionally a
**subset** of the SPEC's Phase 1:

| SPEC §7 Phase 1 deliverable | Phase 1A | Phase 1B |
|---|---|---|
| Caddy `request_body 200MB` | scoped 10MB ✓ | raise to 200MB |
| SOPS `GARAGE_DOCUMENTS_BUCKET` | — | ✓ |
| `klai-libs/document-storage` lib | — | ✓ |
| Garage bucket `klai-documents` (manual on core-01) | — | ✓ |
| portal-api new route | text path ✓ | binary path |
| portal-api `file_upload.py` service | text validation ✓ | + Garage write |
| portal-api `kb_uploads_quota.py` | — | ✓ FOR UPDATE byte quota |
| portal-api `kb_file_upload` product | dropped (uses existing knowledge product) | n/a |
| portal-api `garage_documents_bucket` setting | — | ✓ |
| portal-api alembic `kb_uploads` table | — | ✓ |
| portal-api `/internal/kb-uploads/status` webhook | — | ✓ |
| knowledge-ingest `/ingest/v1/file` | text routes via existing `/document` | new route Phase 1B |
| knowledge-ingest text-mime path | reuses existing markdown chunker ✓ | extends to binary mimes Phase 2 |
| frontend FileUploadForm endpoint flip | ✓ | — |
| frontend XHR progress | — (uses fetch/apiFetch) | ✓ Phase 1B |
| frontend Paraglide messages | inline NL strings ✓ | full Paraglide table Phase 1B |
| `docs/runbooks/kb-file-upload.md` | — | ✓ |
| `.claude/rules/klai/projects/portal-backend.md` | — | ✓ |

**Justification:** the user-visible 500 closes today via the route
flip + text-path acceptance. Garage / new tables / webhook / new
ingest endpoint are all infrastructure that requires bucket
provisioning on core-01 + alembic deploy + cross-service env-var
parity (SOPS first, validator second per pitfall). Doing all of that
in one commit risks env-parity regressions. Phase 1B is the dedicated
follow-up.

### Decision: drop `kb_file_upload` product

The SPEC REQ-5 introduced a new `kb_file_upload` product gate. On
implementation review this is over-scoped: existing `/sources/url` and
`/sources/text` routes do NOT check a product gate — they rely on
KB-writability via `_get_writable_kb_or_raise` (which already enforces
plan + role + quota). File uploads follow the same pattern. The
`kb_file_upload` product would only matter if we wanted to gate file
upload separately from URL/text — no current product reason to do so.
This is recorded as a divergence from SPEC REQ-5 to be resolved in the
SPEC's next revision (probably: drop the requirement in favour of "if
the user can use knowledge they can upload files").

### Operator steps required before Phase 1A is fully live

1. Caddyfile auto-syncs via `deploy-compose.yml` on push to main (
   class-A bind-mount, see `infra/deploy.md`).
2. portal-api auto-rebuilds and recreates via `portal-api.yml`.
3. Frontend auto-rebuilds via `klai-portal.yml`.
4. **No SOPS update needed for Phase 1A** (no new env vars).
5. **No alembic migration in Phase 1A** (uses existing artifacts +
   item-count quota).

After CI green: smoke-test with a `sample.md` upload at
`https://my.getklai.com/app/knowledge/<kb>/add-source`; verify in
VictoriaLogs `service:portal-api AND event:kb_upload_received AND
decision:accepted`.

### Phase 1B — next commits in scope

1. New SOPS env var `GARAGE_DOCUMENTS_BUCKET=klai-documents`.
2. Manual Garage bucket creation on core-01 + key grant.
3. New `klai-libs/document-storage` library (mirrors `image-storage`).
4. New `kb_uploads` + `kb_uploads_quota` alembic tables (cat-D RLS via
   `post_deploy_*.sql` per existing pattern).
5. New `assert_can_upload_bytes` quota helper with FOR UPDATE.
6. New `/ingest/v1/file` endpoint on knowledge-ingest.
7. Caddy raise to 200 MB.
8. Frontend XHR progress + full Paraglide string table.
9. New runbook `docs/runbooks/kb-file-upload.md`.

### Confidence

- Backend: 95 — 71 tests green, lint clean, format clean, no
  regression on existing route tests. Tested locally against the unit
  + integration suite.
- Frontend: 80 — TypeScript clean, no test framework hit yet (Vitest
  not run). Browser e2e via Playwright is Phase 1B with deployed env.
- Caddy: 70 — directive syntax follows existing `path_regexp` pattern
  but not Caddy-validated locally. CI will fail-loud on bad config
  via `deploy-compose.yml` health-check. Operator should `caddy
  validate` on the rendered Caddyfile before merging if available.
