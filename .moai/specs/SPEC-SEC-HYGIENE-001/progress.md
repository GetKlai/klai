## SPEC-SEC-HYGIENE-001 Progress — scribe-slice (HY-33..HY-38)

- Started: 2026-04-25
- Worktree: `klai-hygiene-scribe` on `feature/SPEC-SEC-HYGIENE-001-scribe` (forked from origin/main `96231826`)
- Slice scope: HY-33..HY-38 (klai-scribe/scribe-api). HY-19..HY-28 + HY-30..HY-32 + HY-39..HY-50 deferred to other slices.
- Methodology: TDD (RED → GREEN per AC), one PR for the slice.

### Decisions

- **HY-37 allowlist**: explicit set `{whisper, whisper-server, localhost, 127.0.0.1, 172.18.0.1}` + suffix `*.getklai.com` (Optie B from plan discussion). Bridge IP `172.18.0.1` is current prod default — documented inline to satisfy `validator-env-parity` pitfall. Pydantic v2 `field_validator(mode="after")` on `Settings.whisper_server_url`.
- **HY-37 conftest**: existing test default `WHISPER_SERVER_URL=http://transcription-service.test` would fail the validator. Conftest updated to `http://whisper-server:8080` so existing tests still load Settings.
- **HY-35 schema**: alembic migration `0007_c5f9e3a4_add_error_reason.py` adds `error_reason VARCHAR(64)` (nullable). Reaper queries `WHERE status='processing' AND created_at < NOW() - timeout`. No new `started_at` column — `created_at` (set at row insert in transcribe handler) doubles as start time.
- **HY-36 finalize order**: `finalize_success` restructured: capture `audio_path` → `delete_audio` → mutate fields. If delete raises, mutation is skipped, caller commits nothing, DB stays consistent with disk.
- **HY-38 CORS**: docs-only. MX:WARN comment block above `app.add_middleware(CORSMiddleware, ...)`. Grep-test in tests/test_cors_annotation.py.
- **Reaper wiring**: registered in `app.main.lifespan` so it runs on every worker startup. Best-effort: a failure logs `scribe_startup_reaper_failed` and proceeds with normal startup (does NOT block boot).
- **PR strategy**: single PR for all 6 ACs.

### AC checklist

- [x] AC-34 (HY-34) — Zitadel sub regex `^[A-Za-z0-9_-]{1,64}$` in `auth.py`. 17 tests.
- [x] AC-33 (HY-33) — `_safe_audio_path` + `_safe_stored_path` helpers in `audio_storage.py`, char whitelist + path-resolution check, all 4 callsites rerouted. 19 tests.
- [x] AC-36 (HY-36) — finalize order inverted (delete → mutate); `app/services/janitor.py` orphan sweep with grace period. 9 tests.
- [x] AC-35 (HY-35) — `app/services/reaper.py` flips stale processing rows to failed with `error_reason="worker_restart_stranded"`, audio preserved; alembic migration 0007; reaper wired into lifespan. 5 tests.
- [x] AC-37 (HY-37) — `Settings.whisper_server_url` `field_validator` allowlist; `/health` returns generic 503 with opaque body on any whisper failure, full exception in structlog with `exc_info=True`. 24 tests.
- [x] AC-38 (HY-38) — MX:WARN annotation block above CORSMiddleware registration in `main.py`, references SPEC-SEC-HYGIENE-001 REQ-38 + SPEC-SEC-CORS-001. 4 grep tests.

### Verification

- `uv run pytest` — **94 passed**, 15 warnings (deprecation on `datetime.utcnow()` — pre-existing scribe pattern, not new).
- `uv run ruff check app/ tests/` on changed files — only 2 pre-existing errors remain (B008 FastAPI `Depends` default in `auth.py` and RUF012 SQLAlchemy `__table_args__` in `models/transcription.py`); 0 new errors introduced.

### Risks / Follow-ups

- **R-37**: `/health` now returns 503 (was 200/degraded) when whisper is unreachable. Status.getklai.com config must be updated to interpret 503 as a degraded but expected state — coordinated with monitoring update.
- **R-35-migration**: alembic migration `0007` must be applied before this code deploys (otherwise `error_reason` column is missing → reaper UPDATE fails). Standard alembic flow handles this in CI.
- **R-37-prod**: prod env `WHISPER_SERVER_URL=http://172.18.0.1:8000` is in the allowlist (verified). No env-parity action needed.
- **datetime.utcnow() deprecation**: pre-existing in scribe model + transcribe handler. Not addressed in this slice (out of scope, would touch unchanged code).

---

## SPEC-SEC-HYGIENE-001 Progress — connector-slice (HY-30, HY-31, HY-32)

Branch: `feature/SPEC-SEC-HYGIENE-001-connector` (worktree at
`C:/Users/markv/stack/02 - Voys/Code/klai-hygiene-connector`).
Branched from `origin/main` at `6b75922f`. Merged scribe-slice
(`#179`) and SPEC-SEC-CORS-001 (`#180`) before push to main.

Slice scope: HY-30, HY-31, HY-32 — all in `klai-connector/`. Independent
of the scribe slice; ships as its own merge.

Methodology: TDD per `.moai/config/sections/quality.yaml` (`development_mode: tdd`).
Each finding follows RED → GREEN → REFACTOR with the regression test
written first and confirmed failing against the pre-fix code.

### HY-30 — `HTTPException` NameError → 500 oracle  ✅
Commit: `10715d18`

- REQ-30.1: imported `HTTPException` in `routes/connectors.py:5`.
- REQ-30.2: 6 regression tests in `tests/test_connector_routes_not_found.py`
  cover GET/PUT/DELETE on a missing UUID + cross-tenant case (org A's
  JWT hitting org B's UUID returns 404, not 403/500). All 6 fail with
  500 against pre-fix code (verified) and pass post-fix.
- REQ-30.3: 2 contract tests in `tests/test_ruff_config.py` pin
  `select=["F", ...]` and ensure `F821` is not on the ignore list. Local
  `uv run ruff check` already flagged the original bug — the gap was a
  CI lint pass, not the pyproject config.
- REQ-30.4: audited every file in `app/routes/` via grep — `connectors.py`
  was the only offender. `deps.py`, `sync.py`, `health.py`, `fingerprint.py`
  all import what they use.

Tests: 8 new, all green. Pre-existing `SyncEngine._image_transport` failures
unchanged.

### HY-31 — `/api/v1/compute-fingerprint` rewired (Branch B)  ✅
Commit: `e4ddaa8b`

Branch B chosen (rewire) — feature is in active production use at
`klai-portal/backend/app/services/klai_connector_client.py:91` and
`klai-portal/backend/app/api/connectors.py:47`. Removal would have
shipped a feature break.

Verified the runtime behaviour pre-fix is even worse than the SPEC
text suggested: the original `logger.warning(...)` call inside the
exception handler would itself raise `TypeError` against stdlib Logger
(no kwargs), so the "Crawl failed: ModuleNotFoundError" leak prediction
never executed. Effective state was: silent 500 + portal swallows the
error + canary saved without protection.

- REQ-31.1 (b): rewired to crawl4ai HTTP API at `settings.crawl4ai_api_url`.
  Mirror of `knowledge_ingest.crawl4ai_client.crawl_page` (same
  PruningContentFilter, same chrome-strip excluded_tags, same
  on_page_context_created cookie hook). New helpers `_build_crawl_payload`,
  `_extract_markdown`, `_fetch_page_markdown`. Switched module-level
  logger from `app.core.logging.get_logger` to `structlog.get_logger`
  directly (the codebase helper returns stdlib).
- REQ-31.2: 502 detail collapsed to literal `"Crawl failed"`. Original
  exception only via `logger.exception` → structlog → VictoriaLogs.
- REQ-31.3: 11 regression tests in `tests/test_compute_fingerprint.py`
  cover all four contract paths (200 happy, 422 too-short, 502 generic,
  502 leak detection across 7 strings). Plus an AST-based static guard
  that rejects any future re-import of `app.adapters.webcrawler`.
- REQ-31.4: portal client unchanged — same REST contract, no caller-side
  migration needed.

Tests: 11 new, all green. Ruff clean on `routes/fingerprint.py`.

### HY-32 — Per-org Redis sliding-window rate limit  ✅
Commit: `e7efe1db`

- REQ-32.1 (b): app-layer Redis ZSET sliding window keyed on org_id
  (preferred over Caddy zone — keys on org_id rather than `{remote_host}`).
  New module `app/services/rate_limit.py` mirrors
  `klai-portal/backend/app/services/partner_rate_limit.py`.
- REQ-32.2: env-tunable defaults set higher than the SPEC literal based
  on /run research:
  - `CONNECTOR_RL_READ_PER_MIN=120`  (≈ Auth0 free tier; > Heroku 75/min)
  - `CONNECTOR_RL_WRITE_PER_MIN=30`  (3× SPEC; still 1800/hour ceiling)
  Acceptance test sets limits to the SPEC literal (60/10) so it exercises
  the SPEC-described boundaries verbatim — no test rewrite needed if
  defaults change.
- REQ-32.3: fail-open on any Redis exception. `enforce_org_rate_limit`
  catches → logs `connector_rate_limit_redis_unavailable` at WARNING
  with `exc_info=True` → allows the request through. Same pattern as
  portal `signup_email_rl` and `partner_dependencies`.
- REQ-32.4: 7 regression tests in `tests/test_connector_rate_limit.py`
  cover the AC-32 matrix (write limit + reset, read limit + reset,
  fail-open + structlog event, cross-tenant isolation, portal-secret
  bypass). Custom in-memory fake redis (4 ZSET methods) — no
  `fakeredis` dep added.

Wiring: each route in `connectors.py` gets
`dependencies=[Depends(enforce_org_rate_limit("read"|"write"))]`. POST/
PUT/DELETE = write, GET (list + by-id) = read. Portal control-plane
calls (auth middleware sets `request.state.from_portal=True`) skip the
check.

Settings: empty `REDIS_URL` default = feature OFF. No klai-infra/SOPS
pre-flight required — operators enable per-environment by setting
`REDIS_URL` on the connector service's compose `environment` block.
Fail-open semantics make the env-flip non-breaking either way.

Tests: 7 new + 6 not-found-with-deps update for the new dependency
chain. All green; ruff clean on touched files.

### Quality summary (connector slice)

- 26 new tests (6 + 2 + 11 + 7) — all green.
- Pre-existing 11 `SyncEngine._image_transport` failures unchanged
  (image-storage SPEC, separate scope).
- Ruff: clean on every touched file. The 4 remaining repo-wide ruff
  errors are all pre-existing in untouched files (notion.py N806,
  enums.py UP042, connector.py model E501).
- Pyright: 11 strict-mode "partially unknown" noise warnings remain in
  `routes/fingerprint.py` from JSON parsing — same shape as
  `knowledge_ingest.crawl4ai_client._extract_result`. The connector
  codebase is not pyright-clean overall; chasing zero-strict in this
  hygiene SPEC is scope creep.

### Follow-ups (out of slice scope)

- klai-infra: when ready to enable rate limiting in any environment,
  set `REDIS_URL` in `klai-infra/core-01/.env.sops` AND add it to the
  connector environment block in `deploy/docker-compose.yml`. Fail-open
  semantics mean partial rollouts don't break.
- CI: investigate why ruff F821 didn't flag the HY-30 bug before merge.
  The pyproject.toml selects "F" (which includes F821) — local
  `ruff check` catches it. The gap is somewhere in the CI workflow
  that runs lint on connector PRs. SPEC-SEC-CORS-001 added a
  `klai-connector.yml` workflow on main; the F821 gap may already be
  closed by it (verify with the next CI run).
