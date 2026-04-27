# SPEC-SEC-AUTH-COVERAGE-001 Implementation Plan

## Strategy: TDD per the project's `quality.development_mode = tdd`

Five sub-stories, each one full RED-GREEN-REFACTOR cycle. Sub-stories are
ordered by dependency: REQ-5.1/5.2 helper extraction lands first because
all other REQs use `_emit_auth_event`; REQ-5.6 shared-helpers refactor
lands second because new test files import from it; then REQs 1-4 in any
order; then REQ-5.3/5.4/5.5 final-pass cleanup + coverage gate.

Estimated cycle length: each sub-story is 6-12 commits worth of work
(write tests RED, implement GREEN, refactor for clarity, ruff/pyright
clean, optional simplify pass).

---

## Cycle order

### Cycle A — REQ-5.1 + REQ-5.2 (`_emit_auth_event` helper)

**Why first**: every other REQ writes a call site for this helper. Land
the helper first, exercise it in the existing MFA tests, then tests for
new endpoints can use it.

Steps:
1. **RED**: Add a unit test `tests/test_emit_auth_event.py` that exercises:
   - Event name parameter shows up in the captured event's `event` field.
   - `email=` is hashed to `email_hash`; raw `email` field is absent.
   - `email_hash=` passes through unchanged.
   - `level="warning"` routes to `_slog.warning`; `level="error"` to `_slog.error`.
   - `request_id` is auto-bound from contextvars when set, absent otherwise.
2. **GREEN**: Add `_emit_auth_event` in `auth.py`. Re-implement
   `_emit_mfa_check_failed` as `return _emit_auth_event("mfa_check_failed", reason=…, mfa_policy=…, …)`.
3. **REFACTOR**: Verify all existing `_emit_mfa_check_failed` callers still
   pass (`pytest tests/test_auth_mfa_fail_closed.py`). No call-site
   change needed (signature is preserved).

### Cycle B — REQ-5.6 (`tests/auth_test_helpers.py`)

**Why second**: every new test file imports from it.

Steps:
1. **RED**: Add `tests/auth_test_helpers.py` with the factories +
   fixtures listed in spec.md REQ-5.6. Add a smoke test
   `tests/test_auth_test_helpers_smoke.py` exercising each factory once.
2. **GREEN**: implement the helpers. Most are extracted from
   `tests/test_auth_mfa_fail_closed.py`.
3. **REFACTOR**: Update `tests/test_auth_mfa_fail_closed.py` to import
   from `auth_test_helpers`. The diff is delete-locals + add-imports;
   test bodies unchanged. Run the full module to verify zero behaviour
   change.

### Cycle C — REQ-1 (TOTP endpoints)

**Files affected**: `app/api/auth.py` (3 endpoint bodies),
`tests/test_auth_totp_endpoints.py` (NEW, 13 scenarios).

Steps:
1. **RED**: Write all 13 T-scenarios. Use respx for Zitadel,
   `MagicMock` for `_pending_totp` initial state where needed.
2. **GREEN**: Modify each endpoint body:
   - Add `audit.log_event(...)` for success paths.
   - Add `_emit_auth_event(...)` for every failure path.
   - Migrate stdlib `logger.exception` to `_slog.exception` (no semantic change beyond the message format).
3. **REFACTOR**: Extract any shared TOTP-specific helper if patterns emerge
   (e.g., a `_audit_totp_action(action, user_id)` if the audit-log call
   sites become repetitive).

### Cycle D — REQ-2 (IDP endpoints)

**Files affected**: `app/api/auth.py` (idp_intent + idp_callback),
`tests/test_auth_idp_endpoints.py` (NEW, 8 scenarios).

The 8 callback scenarios are denser because the handler is large. The
existing `tests/test_idp_callback_provision.py` covers the auto-provision
happy path; this file complements it with failure paths.

### Cycle E — REQ-3 (password endpoints)

**Files affected**: `app/api/auth.py` (password_reset + password_set),
`tests/test_auth_password_endpoints.py` (NEW, 8 scenarios).

REQ-5.3 work intersects here: removing `# nosemgrep` annotations on
`password_reset` / `password_set` after migrating their `logger.exception`
calls to `_slog.exception`.

### Cycle F — REQ-4 (sso_complete)

**Files affected**: `app/api/auth.py` (sso_complete),
`tests/test_auth_sso_endpoints.py` (NEW, 4 scenarios).

Smallest cycle. Includes a Fernet-cookie helper in `auth_test_helpers.py`
for the happy path test (encrypt a known sid+stk, pass as cookie).

### Cycle G — REQ-5.3 + REQ-5.4 + REQ-5.5 (cleanup + coverage gate)

Final pass:
1. **REQ-5.3**: Sweep remaining stdlib `logger.*` calls in the in-scope
   endpoints. Verify `# nosemgrep: python-logger-credential-disclosure`
   annotations are gone.
2. **REQ-5.4**: Add `@MX:ANCHOR` to `_finalize_and_set_cookie`,
   `_validate_callback_url`, `_emit_auth_event` with full
   `@MX:REASON` and `@MX:SPEC` sub-lines.
3. **REQ-5.5**: Run `pytest --cov=app.api.auth --cov-branch --cov-fail-under=85`. If under 85%, identify the gap and add scenarios. Likely candidates: less-common idp_callback branches (multi-org → /select-workspace, allowed-domain mismatch, get_session_details fail-soft).

---

## Risk analysis

### Risk A: in-scope endpoints depend on out-of-scope helpers

Mitigation: The endpoints' test scenarios use respx + DB mocks. They do
NOT exercise the in-memory `_pending_totp` cache directly except via the
endpoint. When SPEC-SEC-SESSION-001 replaces the cache with Redis, the
test scenarios continue to pass because they assert on observable
behaviour (HTTP status, captured events) not implementation details.

### Risk B: `idp_callback` is large (126 lines, 7 try/except branches)

Mitigation: Cover the most-common failure paths first (8 scenarios per
spec.md). The auto-provision-DB-failure branch is already covered by
`test_idp_callback_provision.py`. If line coverage on this single
function lags below 80%, add 2-3 targeted scenarios for the
`get_session_details` fail-soft path and the multi-org redirect.

### Risk C: Helper extraction breaks the existing MFA test suite

Mitigation: `_emit_mfa_check_failed` keeps its public signature. The
existing 13 MFA tests (in `test_auth_mfa_fail_closed.py`) are run after
helper extraction (Cycle A REFACTOR step). Pre-existing PR pattern
verifies via CI — if the MFA tests break, Cycle A is reverted before
proceeding to Cycle B.

### Risk D: Anti-enumeration regression on `password_reset`

Mitigation: Spec REQ-3.1 / REQ-3.2 / REQ-3.3 explicitly preserve 204 on
all paths. Test scenarios P2/P3/P4 assert the response code is 204 even
when `_emit_auth_event` fires. CI fails if any password_reset scenario
returns anything other than 204.

### Risk E: Cycle G (REQ-5.5 coverage gate) reveals deeper coverage gaps

Mitigation: If coverage is < 85% after Cycles A-F, the gate explicitly
identifies missing lines via `--cov-report=term-missing`. Add 2-3
"phantom" scenarios for legacy fail-soft branches (e.g., the `try:
emit_event` in login() pre-fix already-covered scenario, or the
`audit.log_event` exception swallow). These scenarios assert that the
fail-soft swallows a fake exception — pure observability tests, no
behaviour change.

---

## Dependency graph

```
Cycle A (REQ-5.1, 5.2) ────┬──→ Cycle B (REQ-5.6)
                            │           │
                            └───────────┴──→ Cycle C (REQ-1, TOTP)
                                              │
                                              ├──→ Cycle D (REQ-2, IDP)
                                              │
                                              ├──→ Cycle E (REQ-3, password)
                                              │
                                              └──→ Cycle F (REQ-4, SSO)
                                                          │
                                                          └──→ Cycle G (REQ-5.3, 5.4, 5.5 cleanup)
```

Cycles C-F are independent and can run in any order or parallel; Cycle
G must come last because it gates on full-suite coverage.

---

## Quality gate (per cycle)

After each cycle:
- `pytest tests/<cycle's test files>` — green
- `uv run ruff check klai-portal/backend/app/api/auth.py klai-portal/backend/tests/test_auth_*.py` — clean
- `uv run ruff format --check klai-portal/backend/app/api/auth.py klai-portal/backend/tests/test_auth_*.py` — clean
- `uv run --with pyright pyright klai-portal/backend/app/api/auth.py` — 0/0/0
- `uv run pytest klai-portal/backend/tests/ -q` — full suite clean (catches collateral regressions early)

After Cycle G:
- `uv run --with pytest-cov pytest --cov=app.api.auth --cov-branch --cov-fail-under=85` — clean
- Branch coverage ≥95% via `--cov-report=term-missing`

---

## MX tag plan (Phase 3.5)

Targets identified during Phase 0.5 research:

| Symbol | Current state | Action | Reason |
|---|---|---|---|
| `_emit_auth_event` (NEW) | n/a | Add `@MX:ANCHOR` with `@MX:REASON: fan_in≈15` | Single source of truth for structured auth-failure event schema; consumed by Grafana queries. |
| `_finalize_and_set_cookie` (line 179) | no anchor | Retroactive `@MX:ANCHOR` with `@MX:REASON: fan_in=3` | Called from login, totp_login, sso_complete; cookie contract change must coordinate all three. |
| `_validate_callback_url` (line 139) | no anchor | Retroactive `@MX:ANCHOR` with `@MX:REASON: fan_in=3` | Trust boundary for OIDC callback URL; affects login, idp_callback, sso_complete. |
| `_emit_mfa_check_failed` (line 242) | has anchor (SPEC-SEC-MFA-001) | Update body to delegate to `_emit_auth_event`; keep anchor + update SPEC reference to also include SPEC-SEC-AUTH-COVERAGE-001 | Public contract preserved; implementation now thin wrapper. |
| `_mfa_unavailable` (line 233) | has anchor (SPEC-SEC-MFA-001) | No change | Already correctly anchored. |

Note: `_pending_totp` (TTLCache) intentionally NOT anchored. Its replacement
under SEC-SESSION-001 will redesign the contract — anchoring now would
need a refactor anyway.

---

## Effort estimate

Sub-stories are independent. Effort below assumes one engineer / one
focused session per cycle:

| Cycle | Test scenarios | Code touch points | Notes |
|---|---|---|---|
| A | 8 | Add `_emit_auth_event`; refactor `_emit_mfa_check_failed` | Mechanical |
| B | 1 (smoke) | Extract helpers; refactor existing test imports | Mechanical |
| C | 13 | totp_setup, totp_confirm, totp_login bodies | Most failure-path scenarios |
| D | 8 | idp_intent + idp_callback | Largest endpoint (idp_callback 126 lines) |
| E | 8 | password_reset, password_set | Includes nosemgrep removal |
| F | 4 | sso_complete | Smallest |
| G | 0 (or 2-3 if coverage gap) | Anchors, logger sweep, coverage report | Final pass |
| **TOTAL** | **~42 scenarios** | **8 endpoint bodies + 4 new test files + 1 helper module** | |

If pytest --cov shows a gap after Cycle F, Cycle G adds 2-3 scenarios.
Total can stretch to ~45 scenarios.

---

## Out-of-scope reminders (referenced from spec.md)

The following will NOT be touched by this SPEC, even though they live in
`auth.py`:

- `_pending_totp` cache (line 73-97) — finding #13, SPEC-SEC-SESSION-001.
- `_fernet` Fernet key fallback (line 106) — finding #16, SPEC-SEC-SESSION-001.
- `exc.response.text` log reflection — finding A4, SPEC-SEC-INTERNAL-001.
- `passkey_*` endpoints — deferred follow-up.
- `email_otp_*` endpoints — deferred follow-up.
- `verify_email` endpoint — deferred follow-up.
- `idp_intent_signup` / `idp_signup_callback` — signup flow, separate scope.
