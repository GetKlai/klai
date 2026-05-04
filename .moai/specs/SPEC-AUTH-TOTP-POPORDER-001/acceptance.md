# SPEC-AUTH-TOTP-POPORDER-001 — Acceptance Criteria

Five Given/When/Then scenarios derived from the state machine in
`spec.md` § Specifications. The full set must pass before merge.

## AC-1: happy path unchanged

- **Given** a user has logged in via `POST /api/auth/login` and received
  a `temp_token` for an `awaiting-totp` pending entry,
- **When** they POST to `/api/auth/totp-login` with a valid TOTP code
  AND `_finalize_and_set_cookie` returns successfully,
- **Then** the response is `200` with the SSO cookie set,
  the pending entry is removed from `_pending_totp`,
  and a single `auth.login.totp` audit event is written.

**Test:** `tests/test_auth_totp_finalize_retry.py::test_happy_path_unchanged`

---

## AC-2: invalid TOTP code retry-allowed

- **Given** an `awaiting-totp` pending entry exists,
- **When** the user submits an invalid TOTP code (Zitadel returns
  400/401 for `update_session_with_totp`),
- **Then** `_pending_totp[temp_token].failures` is incremented,
  the response is `400 "Invalid code, please try again"`,
  the pending entry remains in state `awaiting-totp` (NOT popped),
  and an `auth.totp.failed` audit event is written with reason `invalid_code`.

**Test:** `tests/test_auth_totp_finalize_retry.py::test_invalid_code_keeps_pending_in_awaiting_totp`

---

## AC-3 (regression): finalize 5xx then idempotent retry

This is the bug the SPEC is fixing. Before this commit the user sees
"Session expired" after any 5xx from finalize; after this commit the
retry succeeds without re-doing TOTP.

- **Given** an `awaiting-totp` entry exists,
- **When** the user submits a valid TOTP code (Zitadel 200) AND
  `_finalize_and_set_cookie` then raises `HTTPException(502)` (e.g. a
  transient infra issue or a `_validate_callback_url` regression),
- **Then** the pending entry transitions to state `finalize-pending`
  with `finalize_expiry = monotonic() + 60`,
  the response is `502` (or whatever finalize raised, propagated),
  and the pending entry is NOT popped.

- **When** the user retries `POST /api/auth/totp-login` with the SAME
  `temp_token` within 60 seconds,
- **Then** the handler detects state `finalize-pending`,
  SKIPS the Zitadel `update_session_with_totp` call (the TOTP code is
  one-time-use; Zitadel already accepted it),
  re-calls `_finalize_and_set_cookie` with the stored session_id +
  session_token,
  on success returns `200` with the SSO cookie set,
  and pops the pending entry.

**Test:** `tests/test_auth_totp_finalize_retry.py::test_finalize_5xx_then_retry_succeeds`

---

## AC-4: finalize 5xx then 60s timeout

- **Given** a pending entry has been in state `finalize-pending` for
  more than 60 seconds (`monotonic() > finalize_expiry`),
- **When** the user retries `POST /api/auth/totp-login` with the same
  `temp_token`,
- **Then** the handler detects the expired entry, pops it,
  and returns `400 "Session expired, please log in again"`
  (CURRENT behaviour preserved for stale sessions).

**Test:** `tests/test_auth_totp_finalize_retry.py::test_finalize_pending_60s_timeout`

---

## AC-5: retry-after-success is a no-op

- **Given** a previous retry has already succeeded — the SSO cookie
  was issued, the pending entry was popped (AC-3 happy continuation),
- **When** the user retries `POST /api/auth/totp-login` with the same
  `temp_token` again (e.g. duplicate browser submit),
- **Then** the handler finds NO pending entry (state was popped on
  first success), and returns `400 "Session expired, please log in
  again"` — the one-token-one-cookie invariant holds: NO second SSO
  cookie is issued, NO duplicate audit event is written.

**Test:** `tests/test_auth_totp_finalize_retry.py::test_retry_after_success_is_no_op`

---

## Run Acceptance Aggregate

The SPEC is **acceptable** when:

- AC-1, AC-2, AC-3, AC-4, AC-5 all pass.
- `pytest -q` over the full `klai-portal/backend/tests/` stays green
  (no regression in any other auth-flow test).
- A grep of the production logs for the new event
  `totp_finalize_pending_retry` returns ZERO occurrences before
  deploy AND non-zero occurrences in any week with at least one
  finalize 5xx (proves the new path is actually exercised).
- The pop-ordering bug is unreproducible — manual repro via patching
  `_validate_callback_url` to raise `HTTPException(502)` and triggering
  a real TOTP login + retry sequence within 60s yields a `200 SSO
  cookie set` response on the retry.
