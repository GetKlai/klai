## SPEC-SEC-AUTH-COVERAGE-001 Progress

- Started: 2026-04-27 (run phase)
- Branch: `feature/SPEC-SEC-AUTH-COVERAGE-001`
- Worktree: `~/.moai/worktrees/klai/SPEC-SEC-AUTH-COVERAGE-001`
- Base: `origin/main` @ `8de571ef` (SPEC v0.2.0 — 14 endpoints scope)
- Mode: TDD per quality.yaml
- Solo (no team)

### Phase log

- Phase 0.9 (JIT language detection): Python 3.13 (klai-portal/backend pyproject.toml)
- Phase 0.95 (mode select): Standard Mode (multi-domain, ~14 files, sub-agent solo)
- Phase 1 (strategy): Decomposed into Cycles A–J per `plan.md`. No formal manager-strategy delegation — orchestrator reasoned inline per ultrathink.
- Phase 1.5 (task decomposition): Cycles map 1-to-1 to REQs:
  - Cycle A → REQ-5.1 + REQ-5.2 (`_emit_auth_event` helper) — DONE
  - Cycle B → REQ-5.6 (`tests/auth_test_helpers.py`) — DONE
  - Cycle C → REQ-1 (TOTP setup/confirm/login) — PENDING
  - Cycle D → REQ-2 (IDP intent/callback) — PENDING
  - Cycle E → REQ-3 (password_reset/set) — PENDING
  - Cycle F → REQ-4 (sso_complete) — PENDING
  - Cycle H → REQ-1 v0.2.0 (passkey_setup/confirm) — PENDING
  - Cycle I → REQ-1 v0.2.0 (email_otp_setup/confirm/resend) — PENDING
  - Cycle J → REQ-2.6/2.7/2.8 + REQ-3.8/3.9 (verify_email + idp_signup) — PENDING
  - Cycle G → REQ-5.3/5.4/5.5 cleanup + coverage gate — PENDING (final pass)

### Cycle A — `_emit_auth_event` helper (DONE)

- Added `_emit_auth_event(event, *, reason, outcome, level, email, email_hash, zitadel_status, **fields)` to `klai-portal/backend/app/api/auth.py`.
- Refactored `_emit_mfa_check_failed` as a thin wrapper that calls
  `_emit_auth_event("mfa_check_failed", ...)`. Public signature preserved
  so all SPEC-SEC-MFA-001 callers continue to work.
- Moved the `@MX:ANCHOR` from `_emit_mfa_check_failed` to `_emit_auth_event`
  with updated `@MX:REASON` (fan_in projected ≥20 across both SPECs) and
  `@MX:SPEC: SPEC-SEC-AUTH-COVERAGE-001 (predecessor: SPEC-SEC-MFA-001)`.
- Added `from typing import Any` import.
- Privacy invariant preserved: `email` is sha256-hashed inside; raw email
  NEVER appears in the emitted event. `email_hash=` parameter accepted as
  pre-hashed alternative for callers who already hashed.

### Cycle B — `tests/auth_test_helpers.py` (DONE)

- Created `klai-portal/backend/tests/auth_test_helpers.py` with extracts from
  `test_auth_mfa_fail_closed.py`:
  - `_TEST_EMAIL` constant
  - `respx_zitadel` pytest fixture (mounted on `settings.zitadel_base_url`)
  - `_make_login_body`
  - `_expected_email_hash`
  - `_session_ok`
  - `_make_db_mock`
  - `_audit_emit_patches`
  - `_capture_events(captured, event_name)` — generic filter (new)
  - `_mfa_events(captured)` — backward-compatible wrapper around `_capture_events(..., "mfa_check_failed")`
- Re-exported `respx_zitadel` from `tests/conftest.py` so pytest auto-discovers
  the fixture across all auth test files. Avoids F811 redefinition warnings
  that arise from explicit `import respx_zitadel` + parameter shadowing in
  test signatures.
- Refactored `test_auth_mfa_fail_closed.py` to import the helpers; removed
  the in-file definitions. Net diff: −106 / +14 lines, behaviour unchanged.

### Verification at end of Cycle A + B

- `pytest tests/test_auth_mfa_fail_closed.py tests/test_auth_security.py`:
  23/23 passed in 0.93 s.
- `ruff check app/api/auth.py tests/test_auth_mfa_fail_closed.py tests/auth_test_helpers.py tests/conftest.py`: clean.
- `ruff format --check`: 4 files already formatted.
- `pyright app/api/auth.py`: 0 errors, 0 warnings, 0 informations.

### Files changed (Cycles A + B)

- `klai-portal/backend/app/api/auth.py` (+44 / −15) — new helper, wrapper, `Any` import.
- `klai-portal/backend/tests/auth_test_helpers.py` (NEW, ~165 lines).
- `klai-portal/backend/tests/conftest.py` (+10) — `respx_zitadel` re-export.
- `klai-portal/backend/tests/test_auth_mfa_fail_closed.py` (−106 / +14) — refactor to import.
- `.moai/specs/SPEC-SEC-AUTH-COVERAGE-001/progress.md` (NEW, this file).

### Resume instructions for next session

To continue with Cycles C..J (the actual endpoint work, ~50 scenarios):

1. `cd C:/Users/markv/.moai/worktrees/klai/SPEC-SEC-AUTH-COVERAGE-001`
2. Verify foundation: `uv run pytest tests/test_auth_mfa_fail_closed.py -q` (should show 23 passed).
3. `/moai run --resume SPEC-SEC-AUTH-COVERAGE-001` — orchestrator picks up from Cycle C.
4. Cycle order is: C/D/E/F/H/I/J in any order (independent), then G last (cleanup + coverage gate).
5. Each cycle ≈ one focused commit on this branch. PR opens at /moai sync.

### Known limitations / deferred to future cycles

- Cycle C..J implementation is genuine work, not orchestration. Each cycle:
  - Writes ~5–13 respx-mocked test scenarios (RED)
  - Refactors the corresponding endpoint body in `auth.py` (GREEN)
  - Migrates stdlib `logger.*` to `_slog.*` for that endpoint
  - Adds `audit.log_event` for state-changing success paths
- Cycle G adds:
  - `@MX:ANCHOR` retroactively on `_finalize_and_set_cookie` and `_validate_callback_url`
  - Removal of `# nosemgrep: python-logger-credential-disclosure` annotations on `password_reset` / `password_set`
  - Coverage gate: `pytest --cov=app.api.auth --cov-fail-under=85`
  - 2–3 phantom scenarios if coverage falls short
