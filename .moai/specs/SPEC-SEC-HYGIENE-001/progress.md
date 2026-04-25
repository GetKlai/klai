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
