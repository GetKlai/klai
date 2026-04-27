# SPEC-SEC-AUTH-COVERAGE-001 Research

Deep-reading analysis of `klai-portal/backend/app/api/auth.py` endpoints
not covered by SPEC-SEC-MFA-001 (`login` + `_resolve_and_enforce_mfa` are
already hardened and tested). Captures pre-existing audit findings, current
test coverage, observability gaps, and the patterns SPEC-SEC-MFA-001
established that this SPEC must propagate.

---

## 1. Scope: which endpoints, which not

`auth.py` exposes 16 route handlers. SPEC-SEC-AUTH-COVERAGE-001 covers
the eight that close the REQ-5.6 coverage gap from SPEC-SEC-MFA-001:

| Endpoint | Path | Type | In scope |
|---|---|---|---|
| `login` | `POST /api/auth/login` | password login | ❌ already covered (SPEC-SEC-MFA-001, SPEC-SEC-MFA-001 v0.3.0) |
| `totp_login` | `POST /api/auth/totp-login` | TOTP completion | ✅ |
| `totp_setup` | `POST /api/auth/totp/setup` | TOTP enrolment start | ✅ |
| `totp_confirm` | `POST /api/auth/totp/confirm` | TOTP enrolment commit | ✅ |
| `idp_intent` | `POST /api/auth/idp-intent` | OAuth IDP login start | ✅ |
| `idp_callback` | `GET /api/auth/idp-callback` | OAuth IDP callback | ✅ |
| `password_reset` | `POST /api/auth/password/reset` | reset email | ✅ |
| `password_set` | `POST /api/auth/password/set` | reset commit | ✅ |
| `sso_complete` | `POST /api/auth/sso-complete` | SSO cookie reuse | ✅ |
| `passkey_setup` / `passkey_confirm` | `POST /api/auth/passkey/...` | passkey enrolment | ❌ deferred (separate follow-up) |
| `email_otp_setup` / `email_otp_confirm` / `email_otp_resend` | `POST /api/auth/email-otp/...` | email OTP flow | ❌ deferred |
| `verify_email` | `POST /api/auth/verify-email` | email verification | ❌ deferred |
| `idp_intent_signup` / `idp_signup_callback` | `POST/GET /api/auth/idp-intent-signup`, `/api/auth/idp-signup-callback` | IDP signup variant | ❌ deferred (signup flow is its own SPEC scope) |

The deferred endpoints will be folded into a future SPEC if the coverage
gap on them remains material after this SPEC lands. They are deferred,
not ignored.

---

## 2. Pre-existing audit findings on the in-scope endpoints

`SPEC-SEC-AUDIT-2026-04` (Cornelis 2026-04-22 + internal-wave audit
2026-04-24) flags the following on these eight endpoints. **All of these
are tracked in OTHER SPECs and are explicitly OUT OF SCOPE here.**

| # | Finding | File ref | Routed to | Status |
|---|---|---|---|---|
| 13 | TOTP attempt counter is per-instance only (in-memory `_pending_totp` TTLCache, no Redis backing) | `auth.py:73-97` (the cache class) — affects `totp_login` | [SPEC-SEC-SESSION-001](../SPEC-SEC-SESSION-001/spec.md) | Open |
| 15 | `klai_idp_pending` cookie has no IP / UA binding | `signup.py:243-391` — outside auth.py but related | SPEC-SEC-SESSION-001 | Open |
| 16 | `klai_sso` cookie key regenerates on empty env (Fernet.generate_key() fallback) | `auth.py:106` (the `_fernet` initialisation) — affects `sso_complete`, `idp_callback`, `_finalize_and_set_cookie` | SPEC-SEC-SESSION-001 | Open |
| A4 | `exc.response.text` log reflection (PII / token leakage in logs) | "20+ sites in klai-portal/backend/app/api/auth.py" — affects every `logger.exception("…%s", exc.response.text)` site | [SPEC-SEC-INTERNAL-001](../SPEC-SEC-INTERNAL-001/spec.md) | Open |

**SPEC-SEC-AUTH-COVERAGE-001 does NOT fix any of these.** What it DOES
do, indirectly:

- Tests written under this SPEC will be parameterised so they continue to
  pass after SEC-SESSION-001 lands (e.g., a TOTP test that verifies
  "after 5 failures the token is invalidated" works against either a
  per-instance counter or a Redis-backed counter — the test expresses the
  invariant, not the implementation).
- Tests will include a regression check that confirms `exc.response.text`
  is NOT present in `caplog` records — once SEC-INTERNAL-001 lands, this
  check protects against regression. Until then, the check passes with
  `xfail` markers documented in acceptance.md.

---

## 3. Pattern propagation from SPEC-SEC-MFA-001

SPEC-SEC-MFA-001 established three patterns that this SPEC propagates to
the eight in-scope endpoints. The pattern set is the actual bulk of the
work; tests are the verification surface.

### 3.1 Pattern A — structlog for new log statements

[`portal-logging-py.md`](../../../.claude/rules/klai/projects/portal-logging-py.md)
mandates: *"Always use structlog. Never use logging.getLogger() for new
log statements."*

Current state of in-scope endpoints (read from `auth.py` 2026-04-27):

| Endpoint | logger.* calls | _slog.* calls | Convert? |
|---|---|---|---|
| `totp_login` | 1× exception | 0 | YES |
| `totp_setup` | 1× exception | 0 | YES |
| `totp_confirm` | 1× exception | 0 | YES |
| `idp_intent` | 1× exception, 1× error | 0 | YES |
| `idp_callback` | 4× exception, 1× error | 2× info, 2× exception | partial — convert remaining 5 |
| `password_reset` | 2× exception | 0 | YES |
| `password_set` | 1× exception | 0 | YES |
| `sso_complete` | 1× exception | 0 | YES |

Total: ~16 stdlib `logger.*` call sites to migrate to `_slog.*`.

This is mechanical work, low risk, but with two caveats:

1. The `# nosemgrep: python-logger-credential-disclosure` annotations on
   `password_reset`/`password_set` were added because Semgrep flagged
   `logger.exception("...status=%s", exc.response.status_code)` as
   credential exposure. Migrating to structlog should remove the need
   for the suppression — `_slog.exception("set_password_failed", status=exc.response.status_code)` is structurally clean.
2. The `exc.response.text` interpolation is finding A4 (out of scope),
   so on conversion we keep ONLY the status code, not the response body.
   This is a free hardening that comes with the migration even though A4
   is formally tracked elsewhere.

### 3.2 Pattern B — structured event emission

SPEC-SEC-MFA-001 introduced the `mfa_check_failed` event for monitoring
MFA enforcement failures. The same pattern applies to other auth-flow
failure points:

| Event name | Fires on | mfa_check_failed analogue |
|---|---|---|
| `totp_confirm_failed` | TOTP confirm 4xx (wrong code) or 5xx (Zitadel) | reason: `invalid_code` / `zitadel_5xx`; outcome: `400` / `502` |
| `totp_login_failed` | TOTP-login 4xx (wrong code), counter exhausted, expired token | reason: `invalid_code` / `lockout` / `expired_token`; outcome: `400` / `429` |
| `password_reset_failed` | reset Zitadel 5xx (today: silently swallowed) | reason: `zitadel_5xx` / `unknown_email`; outcome: `204` (we keep anti-enumeration) |
| `password_set_failed` | reset commit 4xx (expired link) or 5xx | reason: `invalid_code` / `expired_link` / `zitadel_5xx`; outcome: `400` / `502` |
| `idp_intent_failed` | unknown IDP id, Zitadel 5xx | reason: `unknown_idp` / `zitadel_5xx`; outcome: `400` / `502` |
| `idp_callback_failed` | session creation 5xx, finalize 5xx, missing session id | reason: per-leg; outcome: `302→failure_url` |
| `sso_complete_failed` | no cookie, decrypt fail, finalize 4xx/5xx | reason: `no_cookie` / `cookie_invalid` / `session_expired`; outcome: `401` |

The schema mirrors `mfa_check_failed`:

```jsonc
{
  "event": "<endpoint>_<action>_failed",
  "service": "portal-api",
  "level": "warning" | "error",
  "request_id": "...",        // bound by LoggingContextMiddleware
  "reason": "...",
  "zitadel_status": <int|null>,
  "email_hash": "<sha256 hex>",  // when email is in scope; else absent
  "outcome": "<http code|fail-open>"
}
```

This enables a single Grafana alert family for the whole auth surface
rather than one alert per endpoint.

### 3.3 Pattern C — @MX:ANCHOR on shared helpers

`_emit_mfa_check_failed` (fan_in 8) and `_mfa_unavailable` (fan_in 6)
got `@MX:ANCHOR` tags during SPEC-SEC-MFA-001 sync. This SPEC adds:

- **`_emit_auth_event(event, ...)`** — shared helper for all the structured
  failure events listed in 3.2. Replaces ad-hoc `_slog.warning("foo_failed", ...)` calls. Will have fan_in ~15+ once all endpoints use it. **@MX:ANCHOR required.**
- **`_finalize_and_set_cookie`** — already exists at `auth.py:179`,
  fan_in 3 (called from `login`, `totp_login`, and `sso_complete`).
  **@MX:ANCHOR retroactively added** so future changes coordinate cookie
  contract changes across all three callers.
- **`_validate_callback_url`** — `auth.py:139`, fan_in 3 (login finalize
  + idp callback + sso complete). **@MX:ANCHOR retroactively added.**

---

## 4. Endpoint-by-endpoint observability and coverage gaps

Numbered findings here become REQ tags in spec.md.

### 4.1 `totp_setup` (`POST /api/auth/totp/setup`)

- **Code**: `auth.py:570-584` — calls `zitadel.register_user_totp(user_id)`. On 5xx → 502.
- **Audit log**: NONE. A failed TOTP enrolment attempt against another
  user's account (if `get_current_user_id` were ever bypassed) would be
  invisible.
- **Tests**: NONE.
- **Recommendations**:
  - Emit `audit.log_event(action="auth.totp.setup", actor=user_id, …)` on success.
  - Emit `_emit_auth_event("totp_setup_failed", reason=…)` on any failure.
  - Tests: happy path returns `uri`+`secret`; 5xx returns 502 + emits event.

### 4.2 `totp_confirm` (`POST /api/auth/totp/confirm`)

- **Code**: `auth.py:611-629` — calls `zitadel.verify_user_totp(user_id, code)`. 4xx → 400; 5xx → 502.
- **Audit log**: NONE on success; NONE on failure. Brute-force attempts
  against the confirm endpoint (which has no per-account rate limit on
  the portal side — Zitadel does its own rate-limit) are invisible.
- **Tests**: NONE.
- **Recommendations**:
  - Emit `audit.log_event(action="auth.totp.confirmed", actor=user_id, …)` on success.
  - Emit `_emit_auth_event("totp_confirm_failed", reason="invalid_code"|"zitadel_5xx", …)` on failures.
  - Tests: happy path returns 204; wrong code returns 400 + emits event;
    Zitadel 5xx returns 502 + emits event with status.

### 4.3 `totp_login` (`POST /api/auth/totp-login`)

- **Code**: `auth.py:458-532` — most complex of the three TOTP endpoints.
  Reads `_pending_totp` cache, increments `failures` counter, locks out
  at `_TOTP_MAX_FAILURES=5`, emits `audit.log_event(action="auth.totp.failed")`
  on each wrong code. On success: `audit.log_event(action="auth.login.totp")` then `_finalize_and_set_cookie`.
- **Audit log**: PRESENT (success + failure). Good baseline.
- **Tests**: NONE for this endpoint specifically. `tests/test_auth_security.py::TestAuthAuditLogging` covers `auth.login.totp` and `auth.totp.failed` actions but not the specific failure behaviour (lockout at 5, expired token, finalize failure).
- **Pre-existing finding**: #13 (in-memory counter) — out of scope.
- **Recommendations**:
  - Add `_emit_auth_event("totp_login_failed", reason=…, …)` to mirror
    the existing `audit.log_event` (so monitoring can use either layer).
  - Tests: 7+ scenarios:
    - Valid token + correct code → 200 + cookie.
    - Valid token + wrong code (1st time) → 400, failures=1.
    - Valid token + wrong code (5th time) → 429, token invalidated.
    - Expired temp_token → 400.
    - Already-locked token → 429 immediately.
    - Zitadel 5xx during update_session_with_totp → 502.
    - finalize_auth_request stale (409) → propagate per existing helper.

### 4.4 `idp_intent` (`POST /api/auth/idp-intent`)

- **Code**: `auth.py:739-766` — validates `idp_id` against allowlist, builds success/failure URLs from `settings.portal_url`, calls `zitadel.create_idp_intent`.
- **Audit log**: NONE.
- **Tests**: NONE.
- **Concerns**:
  - `success_url` is built from `settings.portal_url` (server-controlled), not request-controlled — no open redirect risk.
  - `idp_id` is constrained to the allowlist `{settings.zitadel_idp_google_id, settings.zitadel_idp_microsoft_id}` minus empty strings — good, no injection.
  - Empty `authUrl` from Zitadel → 502 (handled).
- **Recommendations**:
  - Emit `audit.log_event(action="auth.idp.intent", actor="anonymous", details={"idp_id": …})` on success.
  - Emit `_emit_auth_event("idp_intent_failed", reason="unknown_idp"|"zitadel_5xx"|"missing_auth_url", …)`.
  - Tests: 4 scenarios:
    - Known IDP id → returns auth_url.
    - Unknown IDP id → 400.
    - Zitadel 5xx → 502 + event.
    - Zitadel returns no `authUrl` → 502 + event (with `reason=missing_auth_url`).

### 4.5 `idp_callback` (`GET /api/auth/idp-callback`)

- **Code**: `auth.py:769-894` — most complex endpoint. Creates session
  from intent, fetches user identity, looks up portal_users, handles
  multi-org case (Redis pending session → /select-workspace), auto-provisions
  via allowed-domain match, finalizes auth request, sets cookie, emits
  `emit_event("login", method="idp")`.
- **Audit log**: NONE — only `emit_event` (analytics, not audit).
- **structlog**: PARTIAL — uses `_slog.info`, `_slog.exception` for
  auto-provision; uses stdlib `logger.exception` for Zitadel-call failures.
- **Tests**: PRESENT — `tests/test_idp_callback_provision.py` covers
  auto-provision flow. Does NOT cover Zitadel/session failure paths.
- **Concerns**:
  - On every Zitadel failure path the response is 302 → failure_url. This
    is correct UX (user sees the login form again). But there is NO
    `audit.log_event` and NO structured failure event — every failure
    is invisible.
  - `_validate_callback_url(callback_url)` runs after a successful
    `finalize_auth_request` — defends against Zitadel returning a hostile
    callback URL.
  - The auto-provision INSERT writes the email plaintext to `portal_users.email`. That's a DB-storage concern, not log-related — out of scope here.
- **Recommendations**:
  - Emit `audit.log_event(action="auth.login.idp", details={"method":"idp"})` on success.
  - Emit `_emit_auth_event("idp_callback_failed", reason="session_creation_5xx"|"missing_session"|"finalize_5xx"|"get_session_details_failed", …)` on each failure leg.
  - Migrate remaining stdlib `logger.exception` calls to `_slog.exception`.
  - Tests: 6 scenarios:
    - Happy path single-org → 302 to callback_url + cookie.
    - Multi-org (existing test scope) → 302 to /select-workspace.
    - Session-creation 5xx → 302 to failure_url + event.
    - Session details fetch fails → continues (existing fail-open), event emitted.
    - Finalize 5xx → 302 to failure_url + event.
    - Auto-provision DB failure → user gets `org_found=false` (existing fail-open), `_slog.exception` fired (existing).

### 4.6 `password_reset` (`POST /api/auth/password/reset`)

- **Code**: `auth.py:317-335` — looks up user by email, sends reset.
  Always returns 204 (anti-enumeration).
- **Audit log**: NONE on success, NONE on failure.
- **Tests**: NONE.
- **Concerns**:
  - **Anti-enumeration is the security property.** Every failure path
    must return 204 too. Verified by reading the code — it does.
  - Currently silently swallows all errors. Without a structured event,
    a Zitadel outage is invisible AND the user gets no email. Adding the
    event is a pure observability win (no UX regression).
  - Email plaintext is the input; cannot be hashed in the request. But
    the `audit.log_event` and `_emit_auth_event` calls MUST hash via
    sha256 (the same `email_hash` field used in mfa_check_failed).
- **Recommendations**:
  - Emit `audit.log_event(action="auth.password.reset", actor="anonymous", details={"email_hash": <sha256>})` on every call (success or fail).
  - Emit `_emit_auth_event("password_reset_failed", reason="zitadel_5xx"|"unknown_email", outcome="204", …)` on failure paths (still returns 204!).
  - Tests: 4 scenarios:
    - Known email → 204 + audit log + (no failed event).
    - Unknown email → 204 + audit log + event with reason=unknown_email.
    - Zitadel 5xx during find_user_id_by_email → 204 + event.
    - Zitadel 5xx during send_password_reset → 204 + event.

### 4.7 `password_set` (`POST /api/auth/password/set`)

- **Code**: `auth.py:338-355` — `zitadel.set_password_with_code(user_id, code, new_password)`. 4xx (400/404/410) → "link expired"; 5xx → 502.
- **Audit log**: NONE.
- **Tests**: NONE.
- **Concerns**:
  - The `# nosemgrep: python-logger-credential-disclosure` is on the
    `logger.exception` call — Semgrep was flagging because `exc.response.text` could contain the new password if Zitadel echoes it. The current code only logs `status=` so the suppression is defensive. Migrating to structlog removes the need.
  - Brute-force attempts against the confirm code (4-6 digit?) should be
    monitored — `_emit_auth_event` makes this trivially queryable.
- **Recommendations**:
  - Emit `audit.log_event(action="auth.password.set", actor=user_id, details={"reason": "code_invalid"|"set"})`.
  - Emit `_emit_auth_event("password_set_failed", reason=…, …)` on failure.
  - Tests: 4 scenarios:
    - Valid code + valid password → 204 + audit log.
    - Expired code (410) → 400 + event.
    - Wrong code (400) → 400 + event.
    - Zitadel 5xx → 502 + event.

### 4.8 `sso_complete` (`POST /api/auth/sso-complete`)

- **Code**: `auth.py:535-567` — reads `klai_sso` cookie, decrypts,
  finalizes auth request. Three failure modes: no cookie → 401;
  decrypt fail → 401; finalize fail → 401 ("session no longer valid").
- **Audit log**: NONE.
- **Tests**: NONE.
- **Concerns**:
  - Pre-existing finding #16 (Fernet key regen) means decrypt CAN fail
    spuriously after a portal-api restart with empty `SSO_COOKIE_KEY`.
    Tracked under SPEC-SEC-SESSION-001. Out of scope here, BUT the test
    suite added under this SPEC must include "decrypt fail returns 401
    cleanly" so the SEC-SESSION-001 fix is verifiable.
- **Recommendations**:
  - Emit `_emit_auth_event("sso_complete_failed", reason="no_cookie"|"cookie_invalid"|"session_expired", outcome="401", …)`.
  - Tests: 4 scenarios:
    - Valid cookie → 200 + callback_url.
    - No cookie → 401 + event.
    - Tampered/invalid cookie → 401 + event.
    - Finalize 5xx → 401 + event.

---

## 5. Test infrastructure already in place

`tests/test_auth_mfa_fail_closed.py` (landed via SPEC-SEC-MFA-001)
established the test harness this SPEC reuses:

- **respx fixture** mounted on `settings.zitadel_base_url`.
- **`structlog.testing.capture_logs()`** for asserting on event emission.
- **`AsyncMock(spec=AsyncSession)`** + `MagicMock` for DB session and
  Response.
- **`_audit_emit_patches()`** helper that suppresses `audit.log_event`
  + `emit_event` side effects.
- **`_make_login_body()` / `_make_db_mock()`** factory helpers.

This SPEC should add:

- **`_make_totp_body()`** for `TOTPLoginRequest`, `TOTPConfirmRequest`,
  `TOTPSetupResponse` shapes.
- **`_make_idp_intent_body()`** + **`_make_idp_callback_query()`**.
- **`_make_password_reset_body()`** + **`_make_password_set_body()`**.
- **`_make_sso_cookie()`** — produces a valid `klai_sso` Fernet-encrypted
  cookie for the happy-path test.

These belong in a **new shared module** `tests/auth_test_helpers.py`
imported by both `test_auth_mfa_fail_closed.py` (refactor to use
the shared module) and the new test files this SPEC creates.

Test file plan:

- `tests/test_auth_totp_endpoints.py` — totp_setup, totp_confirm, totp_login.
- `tests/test_auth_idp_endpoints.py` — idp_intent, idp_callback (extend
  test_idp_callback_provision.py? No — keep that focused; this is the
  failure-path module.).
- `tests/test_auth_password_endpoints.py` — password_reset, password_set.
- `tests/test_auth_sso_endpoints.py` — sso_complete.

Total estimated new tests: ~30. Plus refactor of existing test files to
use the shared helpers module.

---

## 6. Coverage projection

Current `app.api.auth` coverage (measured 2026-04-27 with
`pytest --cov=app.api.auth --cov-branch`):

- 537 statements, 100 branches.
- 64% line coverage, 89% branch coverage.

Per-endpoint missing coverage (estimated from line ranges):

| Endpoint | Lines | Currently covered? |
|---|---|---|
| `_finalize_and_set_cookie` (179-219) | 41 lines | partial via login + idp_callback tests |
| `password_reset` (317-335) | 19 lines | 0% |
| `password_set` (338-355) | 18 lines | 0% |
| `totp_login` (458-532) | 75 lines | 0% |
| `sso_complete` (535-567) | 33 lines | 0% |
| `totp_setup` (570-584) | 15 lines | 0% |
| `totp_confirm` (611-629) | 19 lines | 0% |
| `idp_intent` (739-766) | 28 lines | 0% |
| `idp_callback` (769-894) | 126 lines | partial via test_idp_callback_provision.py |

Estimated lines added to coverage by this SPEC: ~280 of the 537 total.
**Projected coverage: ~85-90%** depending on how many fallback / edge-case
branches get tested.

This achieves SPEC-SEC-MFA-001 REQ-5.6's deferred 85% target.

---

## 7. Cross-references

- **Tracker**: [SPEC-SEC-AUDIT-2026-04](../SPEC-SEC-AUDIT-2026-04/spec.md)
- **Predecessor**: [SPEC-SEC-MFA-001](../SPEC-SEC-MFA-001/spec.md) —
  established the structlog + structured event + @MX:ANCHOR pattern and
  the test infrastructure this SPEC reuses. REQ-5.6 was deferred from
  there to here.
- **Sibling SPECs (out of scope here)**:
  - [SPEC-SEC-SESSION-001](../SPEC-SEC-SESSION-001/spec.md) — TOTP counter,
    cookie binding, Fernet key regen (findings #13, #15, #16).
  - [SPEC-SEC-INTERNAL-001](../SPEC-SEC-INTERNAL-001/spec.md) —
    `exc.response.text` log reflection (finding A4).
- **Source under change** (read-only research targets, modification scope
  per spec.md):
  - [klai-portal/backend/app/api/auth.py](../../../klai-portal/backend/app/api/auth.py)
  - [klai-portal/backend/app/services/zitadel.py](../../../klai-portal/backend/app/services/zitadel.py)
    (read-only — no changes needed)
  - [klai-portal/backend/tests/test_auth_mfa_fail_closed.py](../../../klai-portal/backend/tests/test_auth_mfa_fail_closed.py)
    (refactor to import shared helpers)
  - [klai-portal/backend/tests/test_idp_callback_provision.py](../../../klai-portal/backend/tests/test_idp_callback_provision.py)
    (potentially extend, not strictly required)
- **Logging rules**: [.claude/rules/klai/projects/portal-logging-py.md](../../../.claude/rules/klai/projects/portal-logging-py.md)
- **MX tag protocol**: [.claude/rules/moai/workflow/mx-tag-protocol.md](../../../.claude/rules/moai/workflow/mx-tag-protocol.md)
