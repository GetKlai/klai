---
id: SPEC-SEC-SESSION-001-acceptance
version: 0.1.0
created: 2026-04-24
updated: 2026-04-24
author: Mark Vletter
---

# SPEC-SEC-SESSION-001 — Acceptance Criteria

Testable scenarios that MUST pass before this SPEC is marked done.
Each scenario maps to specific REQ IDs in `spec.md`. Tests use
`fakeredis` for Redis-backed paths and FastAPI's `TestClient` for
the HTTP surface unless noted.

---

## Scenario 1 — Cross-replica TOTP lockout

**Requirement:** REQ-1.1, REQ-1.4, REQ-1.5, REQ-6.1

**Given**
- A single `fakeredis` instance shared between two portal-api
  `TestClient` instances (call them `client_a` and `client_b`).
- A user has completed password auth and received `temp_token=T`.
- Zitadel's `update_session_with_totp` is mocked to raise
  `HTTPStatusError(response.status_code=400)` for every call (i.e.
  every TOTP code is wrong).

**When**
- The attacker submits 3 wrong TOTP codes against `client_a` (calls
  `POST /api/auth/totp-login` with `temp_token=T`).
- Then submits 2 wrong TOTP codes against `client_b` with the same
  `temp_token=T`.

**Then**
- The first 4 calls (1 through 4 across both clients) return HTTP
  400 with detail `"Invalid code, please try again"`.
- The 5th call (regardless of which client serves it) returns HTTP
  429 with detail `"Too many failed attempts, please log in again"`.
- A 6th attempt would return HTTP 400 with detail `"Session expired,
  please log in again"` because both Redis keys have been deleted.
- The Redis key `totp_pending_failures:T` reached exactly 5 before
  deletion.
- A structlog event `totp_pending_lockout` was emitted once at
  level `warning` with `failures=5` and `token_prefix=<first-8>`.

**Why this is the finding-#13 regression:** without Redis-backed
atomic counters, 3 failures on A + 3 failures on B would yield
lockout at attempt 6, not attempt 5, because each replica kept its
own in-memory counter.

---

## Scenario 2 — IDP pending cookie replayed from different User-Agent

**Requirement:** REQ-2.1, REQ-2.2, REQ-6.2

**Given**
- The IDP signup callback has set `klai_idp_pending` cookie with the
  request carrying `User-Agent: Mozilla/5.0 (Macintosh; ...)` and
  `X-Forwarded-For: 203.0.113.10`.
- The Fernet-decrypted payload contains `ua_hash=sha256("Mozilla/...")`,
  `ip_subnet="203.0.113.0"`.

**When**
- The attacker replays the cookie against `POST /api/signup/social`
  with `User-Agent: curl/8.5.0` and `X-Forwarded-For: 203.0.113.10`.

**Then**
- Response is HTTP 403 with detail
  `"Signup session binding mismatch, please start over"`.
- A structlog event `idp_pending_binding_mismatch` is emitted at
  level `warning` with `stored_ua_hash_prefix` ≠
  `current_ua_hash_prefix` and `stored_ip_subnet ==
  current_ip_subnet`.
- No Zitadel org was created, no `klai_sso` cookie was set.
- The `klai_idp_pending` cookie remains (not cleared) — the
  original user can still complete signup from their real browser
  within the TTL.

---

## Scenario 3 — Service refuses to start with empty `SSO_COOKIE_KEY`

**Requirement:** REQ-3.1, REQ-3.2, REQ-4.1, REQ-4.3, REQ-6.4

**Given**
- Environment variable `SSO_COOKIE_KEY=""` (empty string) or unset.
- A fresh FastAPI app instance is being constructed for testing.

**When**
- The FastAPI lifespan startup is entered (e.g. via
  `TestClient(app).__enter__()` or `app.router.lifespan_context`).

**Then**
- Startup aborts with a `RuntimeError` whose message contains the
  string `SSO_COOKIE_KEY`.
- A structlog event `sso_cookie_key_missing_startup_abort` is
  emitted at level `critical` before the raise propagates.
- No HTTP server is bound (the process would exit non-zero in
  production).

**Companion (positive path)**

**Given** `SSO_COOKIE_KEY` set to a valid 32-byte urlsafe-base64
string.

**When** the app starts.

**Then** startup succeeds. `_get_sso_fernet()` returns a cached
`Fernet` instance on subsequent calls within the same process
(verify via `id()` equality or a counter on a monkeypatched
`Fernet.__init__`).

---

## Scenario 4 — Mobile network switch within same subnet

**Requirement:** REQ-2.1, REQ-2.3, REQ-6.3

**Given**
- The IDP signup callback issued `klai_idp_pending` with the
  request at `X-Forwarded-For: 198.51.100.10` and
  `User-Agent: Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) ...`.
- Decrypted payload therefore contains
  `ip_subnet="198.51.100.0"` (the /24 network address).

**When**
- The user (same iPhone, same UA) finishes the signup form on a
  cellular network and the request arrives at
  `POST /api/signup/social` with
  `X-Forwarded-For: 198.51.100.214` (different last octet, same
  /24) and the same `User-Agent`.

**Then**
- The binding check passes (both `ua_hash` and `ip_subnet` match).
- The social signup flow continues normally (org creation, role
  grant, DB insert, `klai_sso` cookie set) assuming the rest of the
  flow is wired.
- No `idp_pending_binding_mismatch` event is emitted.

**IPv6 companion**

**Given** issue IP `2001:db8:1234:5678::1` and consume IP
`2001:db8:1234:5678:89ab::42`. Both resolve to the `/48` subnet
`2001:db8:1234::`. The binding SHALL pass.

---

## Scenario 5 — Regression: normal password + TOTP login still works

**Requirement:** REQ-6.5, REQ-1.3, REQ-1.6

**Given**
- Redis is reachable (fakeredis in tests).
- A user with TOTP registered.
- Zitadel mocks: `find_user_by_email` returns a user id,
  `has_totp` returns `True`, `create_session` returns valid
  `sessionId` + `sessionToken`, `update_session_with_totp` returns
  an updated session for the correct TOTP code.

**When**
1. `POST /api/auth/login` with correct email + password.
2. `POST /api/auth/totp-login` with the returned `temp_token` and
   the correct code.

**Then**
- Step 1 returns HTTP 200 with
  `{"status": "totp_required", "temp_token": "<urlsafe>"}`.
- The Redis hash `totp_pending:<token>` exists with all four fields
  (`session_id`, `session_token`, `ua_hash`, `ip_subnet`).
- The Redis counter `totp_pending_failures:<token>` exists with
  value `0`.
- Step 2 returns HTTP 200 with `{"status": "success", ...}` and
  sets the `klai_sso` cookie with `httponly=True`, `secure=True`,
  `samesite="lax"`, `domain=".<settings.domain>"`.
- Both Redis keys are deleted after step 2.
- No `totp_pending_lockout` or `totp_pending_redis_unavailable`
  events were emitted.

---

## Scenario 6 — Regression: normal social signup still works

**Requirement:** REQ-6.5, REQ-2.1, REQ-2.2, REQ-2.3

**Given**
- Zitadel mocks as in Scenario 5 plus the IDP-callback session
  factors containing `user.id=<zitadel_user_id>`.
- DB mocks: `PortalUser` by `zitadel_user_id` returns `None` (new
  user).
- The test issues the IDP-pending cookie via
  `GET /api/auth/idp-signup-callback` with
  `User-Agent: Mozilla/5.0 ...` and
  `X-Forwarded-For: 192.0.2.10`.

**When**
- `POST /api/signup/social` is invoked with the same UA and
  `X-Forwarded-For: 192.0.2.10` and a valid `company_name`.

**Then**
- The Fernet decrypt succeeds (no UA/IP mismatch).
- The Zitadel org is created, role grant is applied, the DB row is
  committed, and `klai_sso` is set on the response.
- The `klai_idp_pending` cookie is cleared on the response
  (`response.delete_cookie(...)`).
- No `idp_pending_binding_mismatch` event.

---

## Scenario 7 — Redis unavailable during TOTP verification → fail-closed

**Requirement:** REQ-1.7, REQ-5.3

**Given**
- A user completed password auth with `temp_token=T` set in Redis.
- Redis then becomes unreachable (simulate via monkeypatching
  `get_redis_pool` to return `None`, or raising
  `redis.exceptions.ConnectionError` on the first call).

**When**
- The user submits a TOTP code to `POST /api/auth/totp-login`.

**Then**
- Response is HTTP 503 with detail
  `"Authentication unavailable, please retry"`.
- A structlog event `totp_pending_redis_unavailable` is emitted at
  level `error` with `exc_info=True` (traceback preserved).
- No call to Zitadel `update_session_with_totp` is made (we
  short-circuit before Zitadel because the counter cannot be
  incremented atomically).

**Contrast with partner rate-limiter:** the partner rate-limiter
fails OPEN under the same condition (see `partner_rate_limit.py`).
The TOTP path deliberately fails CLOSED because opening the door
lifts the brute-force ceiling entirely.

---

## Scenario 8 — No PII in logs

**Requirement:** REQ-2.2, REQ-5.1, REQ-5.2

**Given** any of Scenarios 1, 2, or 4.

**When** the corresponding log events fire
(`totp_pending_lockout`, `idp_pending_binding_mismatch`).

**Then** the emitted structlog record SHALL NOT contain:
- Raw `User-Agent` strings (only `*_ua_hash_prefix` — first 8 hex).
- Raw caller IPs (only `*_ip_subnet` — the /24 or /48 network
  address as a string).
- Zitadel `session_id` or `session_token` values.
- The full `temp_token` (only `token_prefix` — first 8 chars).

**Verification:** a pytest fixture captures structlog output and
asserts none of the sensitive substrings appear in any emitted
event's kwargs.

---

## Test file layout

| Scenario | Test file | Test function |
|---|---|---|
| 1 | `tests/api/test_auth_totp_lockout.py` | `test_cross_replica_lockout_at_attempt_5` |
| 2 | `tests/api/test_idp_pending_binding.py` | `test_binding_rejects_different_ua` |
| 3 | `tests/api/test_startup_sso_key_guard.py` | `test_startup_aborts_on_empty_key`, `test_startup_succeeds_with_valid_key` |
| 4 | `tests/api/test_idp_pending_binding.py` | `test_binding_passes_same_subnet_different_ip`, `test_binding_passes_ipv6_same_48` |
| 5 | `tests/api/test_auth_login_happy_path.py` | `test_password_totp_login_happy_path` |
| 6 | `tests/api/test_signup_social_happy_path.py` | `test_social_signup_happy_path_with_binding` |
| 7 | `tests/api/test_auth_totp_lockout.py` | `test_totp_fails_closed_when_redis_unavailable` |
| 8 | `tests/api/test_session_logging_pii.py` | `test_no_pii_in_binding_mismatch_logs`, `test_no_pii_in_totp_lockout_logs` |

All tests SHALL run as part of the portal-api `pytest` suite and
SHALL pass before SPEC-SEC-SESSION-001 is marked `status: done`.

---

## Coverage target

Coverage of the changed lines in `klai-portal/backend/app/api/auth.py`
(the new `_get_sso_fernet`, Redis TOTP helpers, UA/IP derivation) and
the added binding-verify block in
`klai-portal/backend/app/api/signup.py` SHALL reach ≥ 95%. This is
a higher bar than the project-wide 85% because these are
security-critical paths.

---

## Out-of-scope verifications

Things this SPEC does NOT attempt to prove with automated tests:

- That Redis has been provisioned with appropriate network ACLs —
  infrastructure concern, covered by `klai-infra`.
- That `SSO_COOKIE_KEY` has sufficient entropy — handled by SOPS
  generation at secret creation time.
- That the user-facing error text is appropriately localised —
  copy/i18n concern, tracked separately.
- That the 5-failure ceiling is the correct value — policy
  decision, unchanged by this SPEC.
