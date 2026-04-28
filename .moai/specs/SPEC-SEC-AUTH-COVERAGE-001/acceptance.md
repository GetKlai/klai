# SPEC-SEC-AUTH-COVERAGE-001 Acceptance Scenarios

Given/When/Then scenarios for the eight in-scope endpoints. All scenarios
use `pytest`, `pytest-asyncio`, `respx` mounted against the real
`ZitadelClient` instance, and `structlog.testing.capture_logs()` for
event assertions. Common fixtures live in
`klai-portal/backend/tests/auth_test_helpers.py` (NEW per REQ-5.6).

Scenario count: **34 new scenarios** across four new test files.

Format key:
- **REQ**: which spec.md REQ ids the scenario verifies.
- **File**: target test file.
- **Setup**: respx routes + DB mock + cookie/body inputs.
- **Action**: the `await login(...)` / handler invocation.
- **Assert**: HTTP status, headers, body, captured events, audit log.

---

## TOTP endpoints — `tests/test_auth_totp_endpoints.py`

### Scenario T1 — totp_setup happy path

**REQ**: REQ-1.1, REQ-5.7

**Given** a logged-in user with `user_id="uid-1"` AND Zitadel `register_user_totp` returns `{"uri": "otpauth://...", "totpSecret": "ABCD..."}`,
**When** the user calls `POST /api/auth/totp/setup`,
**Then** the response is 200 with body `{"uri": "otpauth://...", "secret": "ABCD..."}`, `audit.log_event` is called with `action="auth.totp.setup"` `actor="uid-1"`, and no `*_failed` event is emitted.

### Scenario T2 — totp_setup Zitadel 5xx

**REQ**: REQ-1.2

**Given** Zitadel `register_user_totp` returns 502,
**When** the user calls `POST /api/auth/totp/setup`,
**Then** the response is 502, captured logs contain one `totp_setup_failed` event with `reason="zitadel_5xx"`, `zitadel_status=502`, `actor_user_id="uid-1"`, `outcome="502"`, `level="error"`.

### Scenario T3 — totp_confirm happy path

**REQ**: REQ-1.3

**Given** Zitadel `verify_user_totp` returns 204,
**When** the user calls `POST /api/auth/totp/confirm` with body `{"code": "123456"}`,
**Then** the response is 204, `audit.log_event` is called with `action="auth.totp.confirmed"` `actor="uid-1"`, and no `*_failed` event is emitted.

### Scenario T4 — totp_confirm wrong code

**REQ**: REQ-1.4

**Given** Zitadel `verify_user_totp` returns 400 (invalid code),
**When** the user calls `POST /api/auth/totp/confirm` with body `{"code": "000000"}`,
**Then** the response is 400 with detail `"Invalid code, please try again"`, captured logs contain one `totp_confirm_failed` with `reason="invalid_code"`, `zitadel_status=400`, `outcome="400"`, `level="warning"`.

### Scenario T5 — totp_confirm Zitadel 5xx

**REQ**: REQ-1.5

**Given** Zitadel `verify_user_totp` returns 502,
**When** the user calls `POST /api/auth/totp/confirm`,
**Then** the response is 502, captured logs contain one `totp_confirm_failed` with `reason="zitadel_5xx"`, `zitadel_status=502`, `outcome="502"`, `level="error"`.

### Scenario T6 — totp_login happy path

**REQ**: REQ-1.8

**Given** `_pending_totp` contains `{session_id, session_token, failures: 0}` AND Zitadel `update_session_with_totp` returns the session AND `finalize_auth_request` returns a callback URL,
**When** the user calls `POST /api/auth/totp-login` with `{"temp_token": "...", "code": "123456", "auth_request_id": "ar-1"}`,
**Then** the response is 200 with body containing `callback_url` AND `Set-Cookie: klai_sso=…` is present AND `audit.log_event` is called with `action="auth.login.totp"` AND no `*_failed` event is emitted AND the temp_token is removed from `_pending_totp`.

### Scenario T7 — totp_login wrong code (1st failure)

**REQ**: REQ-1.6

**Given** `_pending_totp` has `failures: 0` AND `update_session_with_totp` returns 400,
**When** the user calls `POST /api/auth/totp-login` with wrong code,
**Then** the response is 400 with detail `"Invalid code, please try again"`, `failures` is now 1, `audit.log_event(action="auth.totp.failed")` is called, AND captured logs contain one `totp_login_failed` with `reason="invalid_code"`, `failures=1`, `outcome="400"`, `level="warning"`.

### Scenario T8 — totp_login wrong code (5th failure → lockout)

**REQ**: REQ-1.7

**Given** `_pending_totp` has `failures: 4` AND `update_session_with_totp` returns 400,
**When** the user calls `POST /api/auth/totp-login` with wrong code,
**Then** the response is 429 with detail `"Too many failed attempts, please log in again"`, the temp_token is REMOVED from `_pending_totp`, AND captured logs contain one `totp_login_failed` with `reason="lockout"`, `failures=5`, `outcome="429"`, `level="error"`.

### Scenario T9 — totp_login already locked

**REQ**: REQ-1.7

**Given** `_pending_totp` has `failures: 5`,
**When** the user calls `POST /api/auth/totp-login`,
**Then** the response is 429 IMMEDIATELY (Zitadel is not called), AND captured logs contain one `totp_login_failed` with `reason="lockout"`, `outcome="429"`.

### Scenario T10 — totp_login expired temp_token

**REQ**: REQ-1.8

**Given** `_pending_totp.get(temp_token)` returns None,
**When** the user calls `POST /api/auth/totp-login`,
**Then** the response is 400 with detail `"Session expired, please log in again"`, AND captured logs contain one `totp_login_failed` with `reason="expired_token"`, `outcome="400"`, `level="warning"`.

### Scenario T11 — totp_login Zitadel 5xx

**REQ**: REQ-1.8

**Given** `_pending_totp` valid AND `update_session_with_totp` returns 502,
**When** the user calls `POST /api/auth/totp-login`,
**Then** the response is 502, AND captured logs contain one `totp_login_failed` with `reason="zitadel_5xx"`, `zitadel_status=502`, `outcome="502"`, `level="error"`.

### Scenario T12 — totp_login finalize fails

**REQ**: REQ-1.8

**Given** TOTP verification succeeds AND `finalize_auth_request` returns 400 with `"already been handled"`,
**When** the user calls `POST /api/auth/totp-login`,
**Then** the response is 409 with detail `"auth_request_stale"` (existing `_finalize_and_set_cookie` behaviour), AND `audit.log_event(action="auth.login.totp")` is still called (TOTP verification succeeded).

### Scenario T13 — totp_login no Set-Cookie on failure

**REQ**: REQ-1.6, REQ-1.7

**Given** any `totp_login_failed` scenario above,
**When** the response is returned,
**Then** the response MUST NOT contain a `Set-Cookie: klai_sso` header (no session artefact on failure paths).

---

## IDP endpoints — `tests/test_auth_idp_endpoints.py`

### Scenario I1 — idp_intent happy path

**REQ**: REQ-2.1, REQ-5.7

**Given** `body.idp_id` is the configured Google IDP id AND `zitadel.create_idp_intent` returns `{"authUrl": "https://accounts.google.com/..."}`,
**When** the user calls `POST /api/auth/idp-intent`,
**Then** the response is 200 with `auth_url` matching the Zitadel response, `audit.log_event(action="auth.idp.intent", actor="anonymous")` is called with the idp_id in details, AND no `*_failed` event is emitted.

### Scenario I2 — idp_intent unknown IDP

**REQ**: REQ-2.2

**Given** `body.idp_id` is a string not in the allowlist,
**When** the user calls `POST /api/auth/idp-intent`,
**Then** the response is 400 with detail `"Unknown IDP"`, AND captured logs contain one `idp_intent_failed` with `reason="unknown_idp"`, `outcome="400"`, `level="warning"`. Zitadel is NOT called.

### Scenario I3 — idp_intent Zitadel 5xx

**REQ**: REQ-2.2

**Given** `zitadel.create_idp_intent` returns 502,
**When** the user calls `POST /api/auth/idp-intent`,
**Then** the response is 502, AND captured logs contain one `idp_intent_failed` with `reason="zitadel_5xx"`, `zitadel_status=502`, `outcome="502"`, `level="error"`.

### Scenario I4 — idp_intent missing authUrl

**REQ**: REQ-2.2

**Given** `zitadel.create_idp_intent` returns 200 with `{}` (no authUrl),
**When** the user calls `POST /api/auth/idp-intent`,
**Then** the response is 502, AND captured logs contain one `idp_intent_failed` with `reason="missing_auth_url"`, `outcome="502"`, `level="error"`.

### Scenario I5 — idp_callback session creation 5xx

**REQ**: REQ-2.4

**Given** `zitadel.create_session_with_idp_intent` returns 502,
**When** the user calls `GET /api/auth/idp-callback?id=...&token=...&auth_request_id=ar-1`,
**Then** the response is 302 with Location starting with `/login?authRequest=ar-1`, AND captured logs contain one `idp_callback_failed` with `reason="session_creation_5xx"`, `zitadel_status=502`, `outcome="302→failure_url"`, `level="error"`.

### Scenario I6 — idp_callback missing session id

**REQ**: REQ-2.4

**Given** `create_session_with_idp_intent` returns 200 but the response has no `sessionId`,
**When** the user calls the callback,
**Then** the response is 302 to failure_url, AND captured logs contain one `idp_callback_failed` with `reason="missing_session"`, `outcome="302→failure_url"`, `level="error"`.

### Scenario I7 — idp_callback finalize 5xx

**REQ**: REQ-2.4

**Given** session created OK AND user lookup succeeds AND `finalize_auth_request` returns 502,
**When** the user calls the callback,
**Then** the response is 302 to failure_url, AND captured logs contain one `idp_callback_failed` with `reason="finalize_5xx"`, `zitadel_status=502`, `outcome="302→failure_url"`, `level="error"`.

### Scenario I8 — idp_callback happy single-org

**REQ**: REQ-2.3

**Given** session created AND user identity fetched AND existing portal_users has exactly ONE row for the zitadel_user_id AND `finalize_auth_request` returns a callback URL,
**When** the user calls the callback,
**Then** the response is 302 to the callback URL with `Set-Cookie: klai_sso=…`, `audit.log_event(action="auth.login.idp", actor=zitadel_user_id, details={"method":"idp", "auto_provisioned": false})` is called, AND no `*_failed` event is emitted.

---

## Password endpoints — `tests/test_auth_password_endpoints.py`

### Scenario P1 — password_reset known email

**REQ**: REQ-3.1, REQ-5.7

**Given** `zitadel.find_user_id_by_email` returns `"uid-1"` AND `zitadel.send_password_reset` returns 204,
**When** the user calls `POST /api/auth/password/reset` with `{"email": "alice@acme.com"}`,
**Then** the response is 204, `audit.log_event(action="auth.password.reset", actor="anonymous", details={"email_hash": <sha256>})` is called, AND no `*_failed` event is emitted.

### Scenario P2 — password_reset unknown email

**REQ**: REQ-3.1, REQ-3.2

**Given** `find_user_id_by_email` returns None,
**When** the user calls `POST /api/auth/password/reset` with an unknown email,
**Then** the response is 204 (anti-enumeration), `audit.log_event(...)` is called, AND captured logs contain one `password_reset_failed` with `reason="unknown_email"`, `email_hash=<sha256>`, `outcome="204"`, `level="warning"`.

### Scenario P3 — password_reset find_user_by_email 5xx

**REQ**: REQ-3.3

**Given** `find_user_id_by_email` returns 502,
**When** the user calls `POST /api/auth/password/reset`,
**Then** the response is 204 (anti-enumeration is preserved on infrastructure failures), AND captured logs contain one `password_reset_failed` with `reason="zitadel_5xx"`, `zitadel_status=502`, `email_hash=<sha256>`, `outcome="204"`, `level="error"`.

### Scenario P4 — password_reset send_password_reset 5xx

**REQ**: REQ-3.3

**Given** `find_user_id_by_email` returns `"uid-1"` AND `send_password_reset` returns 502,
**When** the user calls `POST /api/auth/password/reset`,
**Then** the response is 204 (silent), AND captured logs contain one `password_reset_failed` with `reason="zitadel_5xx"`, `zitadel_status=502`, `outcome="204"`, `level="error"`.

### Scenario P5 — password_set happy path

**REQ**: REQ-3.4

**Given** `zitadel.set_password_with_code` returns 204,
**When** the user calls `POST /api/auth/password/set` with `{"user_id": "uid-1", "code": "123456", "new_password": "..."}`,
**Then** the response is 204, `audit.log_event(action="auth.password.set", actor="uid-1", details={"reason":"set"})` is called, AND no `*_failed` event is emitted.

### Scenario P6 — password_set expired link (410)

**REQ**: REQ-3.5

**Given** `set_password_with_code` returns 410,
**When** the user calls `POST /api/auth/password/set`,
**Then** the response is 400 with detail `"Link has expired or is invalid, request a new reset link"`, AND captured logs contain one `password_set_failed` with `reason="expired_link"`, `zitadel_status=410`, `outcome="400"`, `level="warning"`.

### Scenario P7 — password_set invalid code (400)

**REQ**: REQ-3.5

**Given** `set_password_with_code` returns 400,
**When** the user calls `POST /api/auth/password/set`,
**Then** the response is 400 with detail `"Link has expired or is invalid, request a new reset link"`, AND captured logs contain one `password_set_failed` with `reason="invalid_code"`, `zitadel_status=400`, `outcome="400"`, `level="warning"`.

### Scenario P8 — password_set Zitadel 5xx

**REQ**: REQ-3.6

**Given** `set_password_with_code` returns 502,
**When** the user calls `POST /api/auth/password/set`,
**Then** the response is 502 with detail `"Failed to set password, please try again later"`, AND captured logs contain one `password_set_failed` with `reason="zitadel_5xx"`, `zitadel_status=502`, `outcome="502"`, `level="error"`.

---

## SSO endpoint — `tests/test_auth_sso_endpoints.py`

### Scenario S1 — sso_complete happy path

**REQ**: REQ-4.5

**Given** `klai_sso` cookie is a valid Fernet-encrypted payload of `{sid, stk}` AND `finalize_auth_request` returns a callback URL,
**When** the user calls `POST /api/auth/sso-complete` with `{"auth_request_id": "ar-1"}`,
**Then** the response is 200 with body `{"callback_url": "https://chat.getklai.com/..."}`, NO `audit.log_event` is called (REQ-4.4 — SSO success is silent), AND no `*_failed` event is emitted.

### Scenario S2 — sso_complete missing cookie

**REQ**: REQ-4.1

**Given** no `klai_sso` cookie,
**When** the user calls `POST /api/auth/sso-complete`,
**Then** the response is 401 with detail `"No SSO session"`, AND captured logs contain one `sso_complete_failed` with `reason="no_cookie"`, `outcome="401"`, `level="warning"`. Zitadel is NOT called.

### Scenario S3 — sso_complete tampered cookie

**REQ**: REQ-4.2

**Given** `klai_sso="not-a-valid-fernet-token"`,
**When** the user calls `POST /api/auth/sso-complete`,
**Then** the response is 401 with detail `"SSO cookie invalid"`, AND captured logs contain one `sso_complete_failed` with `reason="cookie_invalid"`, `outcome="401"`, `level="warning"`.

### Scenario S4 — sso_complete finalize 5xx

**REQ**: REQ-4.3

**Given** valid cookie AND `finalize_auth_request` returns 502,
**When** the user calls `POST /api/auth/sso-complete`,
**Then** the response is 401 with detail `"SSO session no longer valid"`, AND captured logs contain one `sso_complete_failed` with `reason="session_expired"`, `zitadel_status=502`, `outcome="401"`, `level="warning"`.

---

## Cross-cutting verifications

### Coverage gate (REQ-5.5)

`pytest --cov=app.api.auth --cov-branch --cov-fail-under=85` SHALL exit 0.
Branch coverage SHALL be ≥95%.

### Helper extraction verification (REQ-5.1, REQ-5.2)

`grep "_emit_mfa_check_failed" auth.py` returns exactly 1 line (the wrapper definition); the helper itself contains exactly `return _emit_auth_event("mfa_check_failed", ...)`.

`_emit_auth_event` accepts BOTH `email=` (raw, hashed inside) AND `email_hash=` (pre-hashed). When `email` is passed, the emitted event has `email_hash` and NOT `email`. When `email_hash` is passed, the emitted event passes through.

### Stdlib logger migration (REQ-5.3)

`grep "logger\." klai-portal/backend/app/api/auth.py` returns:
- the `logger = logging.getLogger(__name__)` line ONLY in the in-scope sections; OR
- only `logger.*` calls in OUT-OF-SCOPE endpoints (passkey_*, email_otp_*, verify_email, idp_intent_signup, idp_signup_callback).

Specifically: ZERO `logger.*` calls inside the bodies of `totp_setup`, `totp_confirm`, `totp_login`, `idp_intent`, `idp_callback`, `password_reset`, `password_set`, `sso_complete`.

### Semgrep suppression removal (REQ-5.3)

`grep "nosemgrep: python-logger-credential-disclosure" klai-portal/backend/app/api/auth.py` returns 0 matches.

### MX:ANCHOR verification (REQ-5.4)

`grep "@MX:ANCHOR" klai-portal/backend/app/api/auth.py` returns lines for:
- `_mfa_unavailable` (existing from SPEC-SEC-MFA-001)
- `_emit_mfa_check_failed` (existing — though now a wrapper, the anchor stays as a stable contract reference)
- `_emit_auth_event` (NEW)
- `_finalize_and_set_cookie` (NEW)
- `_validate_callback_url` (NEW)

Each `@MX:ANCHOR` line is preceded by a `@MX:REASON` line and followed by a `@MX:SPEC: SPEC-SEC-AUTH-COVERAGE-001` (or earlier SPEC) sub-line.

### Shared test helpers verification (REQ-5.6)

`tests/auth_test_helpers.py` exists and exports at least:
- `respx_zitadel` fixture
- `_make_login_body`, `_make_totp_login_body`, `_make_totp_confirm_body`, `_make_totp_setup_request` (no body — depends-only), `_make_idp_intent_body`, `_make_idp_callback_query`, `_make_password_reset_body`, `_make_password_set_body`, `_make_sso_complete_body`
- `_make_sso_cookie(sid, stk)` (Fernet-encrypted helper)
- `_make_db_mock`
- `_audit_emit_patches`
- `_capture_events(captured, name)` filter helper (replaces `_mfa_events` — generic)
- `_expected_email_hash(email)`

`tests/test_auth_mfa_fail_closed.py` imports from `tests/auth_test_helpers` and the diff is purely additive (no test-logic change).

---

## Out-of-test verification (Sync-phase manual check)

These items can only be verified at deploy time, not in unit tests:

- **Grafana queryability**: `service:portal-api event:totp_login_failed` (and the other 6 event names) returns the expected schema in production VictoriaLogs after first real failure. Documented in `docs/runbooks/auth-event-schema.md` (NEW, optional — could be folded into `mfa-check-failed.md` runbook).
- **No PII regression**: `service:portal-api event:password_reset_failed` returns events with `email_hash` field but NEVER `email` field with plaintext. Verifiable via VictoriaLogs query.
- **Audit trail completeness**: SELECT distinct(action) FROM portal_audit_log WHERE created_at > <merge> includes `auth.totp.setup`, `auth.totp.confirmed`, `auth.password.reset`, `auth.password.set`, `auth.idp.intent`, `auth.login.idp`. (Not yet present today.)
- **Coverage report**: CI's coverage report on the merged commit shows ≥85% on `app.api.auth`.

---

## Coverage map — REQ ↔ scenarios

| REQ | Verifying scenarios |
|---|---|
| REQ-1.1 | T1 |
| REQ-1.2 | T2 |
| REQ-1.3 | T3 |
| REQ-1.4 | T4 |
| REQ-1.5 | T5 |
| REQ-1.6 | T7 |
| REQ-1.7 | T8, T9 |
| REQ-1.8 | T6, T10, T11, T12, T13 |
| REQ-2.1 | I1 |
| REQ-2.2 | I2, I3, I4 |
| REQ-2.3 | I8 |
| REQ-2.4 | I5, I6, I7 |
| REQ-2.5 | I1..I8 |
| REQ-3.1 | P1, P2 |
| REQ-3.2 | P2 |
| REQ-3.3 | P3, P4 |
| REQ-3.4 | P5 |
| REQ-3.5 | P6, P7 |
| REQ-3.6 | P8 |
| REQ-3.7 | P1..P8 |
| REQ-4.1 | S2 |
| REQ-4.2 | S3 |
| REQ-4.3 | S4 |
| REQ-4.4 | S1 (asserts no audit) |
| REQ-4.5 | S1..S4 |
| REQ-5.1 | "Helper extraction verification" check |
| REQ-5.2 | "Helper extraction verification" check (email vs email_hash branch) |
| REQ-5.3 | "Stdlib logger migration" + "Semgrep suppression removal" greps |
| REQ-5.4 | "MX:ANCHOR verification" grep |
| REQ-5.5 | Coverage gate (`--cov-fail-under=85`) |
| REQ-5.6 | "Shared test helpers verification" check + import diff |
| REQ-5.7 | T1, I1, P1, S1 (REQ-5.7 sample verification — every test module uses respx-not-MagicMock) |
