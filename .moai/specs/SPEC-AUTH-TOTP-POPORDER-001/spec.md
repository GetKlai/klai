---
id: SPEC-AUTH-TOTP-POPORDER-001
version: "0.1.0"
status: draft
created: "2026-04-30"
updated: "2026-04-30"
author: MoAI
priority: medium
issue_number: 0
---

## HISTORY

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1.0 | 2026-04-30 | MoAI | Stub created from SPEC-SEC-HYGIENE-001 v0.7.1 follow-up — `_pending_totp.pop()` ordering UX-fidelity bug uncovered during the 2026-04-29 callback-allowlist incident. |

# SPEC-AUTH-TOTP-POPORDER-001: TOTP pending-token pop ordering on finalize failure

## Overview

Fix a UX-fidelity bug in `klai-portal/backend/app/api/auth.py::totp_login`. When Zitadel's `update_session_with_totp` succeeds but the subsequent `_finalize_and_set_cookie` raises (e.g. transient infra failure, a system bug like the 2026-04-29 REQ-20 callback-allowlist regression), the `_pending_totp.pop(body.temp_token)` call has already wiped the temp token. The user's retry then hits the "Session expired" branch (HTTP 400) — even though the actual problem is a 5xx system error, not user input.

This is not a security/correctness bug — Zitadel's session is the authority — but it produces a misleading error message that pushes the user to "log in again from scratch" when in fact the system was wrong, not the user.

## Environment

- **Service:** klai-portal-api (FastAPI)
- **Module:** [klai-portal/backend/app/api/auth.py](../../../klai-portal/backend/app/api/auth.py) — `totp_login` handler
- **Affected lines:** approx. 680-755 (handler body)
- **Related SPECs:** SPEC-AUTH-001 (login lifecycle), SPEC-SEC-HYGIENE-001 (REQ-20 callback validator) — the 502 failure mode that exposed this bug.

## Assumptions

- A1: `_pending_totp` is an in-memory dict keyed by `temp_token`. Single-instance only — when scaled horizontally this becomes a Redis-backed cache (separate concern).
- A2: A successful `update_session_with_totp` call is idempotent on the (session_id, session_token) pair, BUT the TOTP code itself is one-time-use. So a retry with the same code WITHOUT first calling Zitadel again will fail at Zitadel.
- A3: The user-visible failure mode today (`HTTP 400 "Session expired, please log in again"`) is acceptable in the strict sense — re-login is always safe — but is a poor UX signal when the actual fault is server-side.

## Requirements

### R1 — Ubiquitous: temp-token lifetime tied to terminal handler outcomes

The `_pending_totp` entry SHALL persist for the entire `totp_login` handler lifetime and SHALL be popped only on a TERMINAL outcome:

- **Success** — `_finalize_and_set_cookie` returns and the response is set.
- **User error** — TOTP code rejected by Zitadel (`update_session_with_totp` raised `httpx.HTTPStatusError` with status 400 or 401).
- **Lockout** — `_TOTP_MAX_FAILURES` reached.

The temp-token entry SHALL NOT be popped when the handler raises a `5xx`-class HTTPException after a successful Zitadel TOTP verification, because the user has nothing to fix and a retry within the temp-token TTL is the natural recovery path.

### R2 — Event-driven: idempotent retry after 5xx finalize failure

WHEN the user retries `POST /api/auth/totp-login` with the same `temp_token` after a previous 5xx response THEN the handler SHALL detect the "post-Zitadel-success, pre-finalize" state stored on the pending entry, SHALL skip the (already-completed) Zitadel TOTP verification, and SHALL retry only the finalize step.

### R3 — State-driven: TTL-bounded retry window

IF a temp-token entry is in the post-Zitadel-success state THEN it SHALL expire 60 seconds after the original successful Zitadel call. After expiry, retries return `HTTP 400 "Session expired, please log in again"` (current behaviour) so an abandoned session does not leak Zitadel-session tokens indefinitely.

### R4 — Unwanted Behavior: no security regression

The following invariants MUST hold:

- A retry with the same `temp_token` MUST NOT bypass `_TOTP_MAX_FAILURES` accounting.
- A retry MUST NOT re-issue the SSO cookie if a previous successful retry already issued one (one-token, one-cookie).
- The TOTP code itself MUST NOT be cached — only the post-Zitadel session_id and session_token are stored on the pending entry.

### R5 — Optional: improved error response on finalize 5xx

Where the finalize step fails with a known transient error class, the response SHALL include a `Retry-After: <seconds>` header so the frontend can auto-retry without forcing a new password+TOTP cycle.

## Specifications

### Pending-token state machine (proposed)

```
                      +---------------+ Zitadel 200 + finalize 200
   POST /login OK --->| awaiting-totp |---------------------------------> COMPLETE (popped)
                      +---------------+
                              |
                              | Zitadel 400/401 < _TOTP_MAX_FAILURES
                              v
                      +---------------+
                      | retry-allowed | --- Zitadel 400/401 = max ---> POPPED (lockout 429)
                      +---------------+
                              |
                              | Zitadel 200, finalize 5xx
                              v
                      +-------------------+
                      | finalize-pending  | --- retry & finalize 200 ---> COMPLETE (popped)
                      | (60s TTL)         |
                      +-------------------+   --- 60s elapsed ---> POPPED (400 Session expired)
```

### Acceptance scenarios (preview — full set in `acceptance.md` post-`/moai plan`)

- **AC-1 happy path:** unchanged, retains current behavior; one TOTP submit → SSO cookie → `_pending_totp` popped.
- **AC-2 wrong code:** `Zitadel 400` → `_pending_totp.failures += 1` → user sees "Invalid code, please try again" (current).
- **AC-3 finalize 5xx then retry:** Zitadel 200, finalize raises `HTTPException(502)` → `_pending_totp` retains entry in `finalize-pending` state → user retries with same `temp_token` → handler detects state, skips Zitadel call, retries finalize → SSO cookie issued → `_pending_totp` popped. NO "Session expired" misleading error.
- **AC-4 finalize 5xx then 60s timeout:** as AC-3 but user waits >60s → entry expired → next retry returns `HTTP 400 "Session expired"` (current behaviour preserved for stale sessions).
- **AC-5 retry after success is no-op:** as AC-3 but user retries AFTER first successful retry → entry already popped → `HTTP 400 "Session expired"` (one-token-one-cookie invariant).

## Files Affected

- `klai-portal/backend/app/api/auth.py` — `totp_login` handler refactor: introduce `_pending_totp` state field, hoist finalize into a separate `_retry_finalize_only` branch.
- `klai-portal/backend/tests/test_auth_totp_endpoints.py` (or new `test_auth_totp_finalize_retry.py`) — 5 scenarios covering the state machine.

## MX Tag Plan

- `_pending_totp` global is currently `# @MX:NOTE` quality. The state-machine refactor will hoist it to `# @MX:ANCHOR` because fan_in increases (handler reads BOTH on initial submit and on retry path) and the lifetime invariants become safety-critical.
- `_finalize_and_set_cookie` is `@MX:ANCHOR` already; this SPEC adds a "may be retried after 5xx" note.

## Exclusions

- Replacing `_pending_totp` with a Redis-backed store. Single-instance limitation persists; horizontal scaling is a separate SPEC.
- Changing the TOTP code-validation semantics or rate-limit policy.
- Changing the `_finalize_and_set_cookie` failure modes themselves — this SPEC accepts that 5xx exists and makes the user experience graceful.

## Implementation Notes (for `/moai run`)

- Reproduction test: temporarily patch `_validate_callback_url` to raise `HTTPException(502)` and confirm current code wipes `_pending_totp`. The fix should make the same test pass with retry.
- Be careful with the `_TOTP_MAX_FAILURES` counter: only increment on Zitadel-side failures (400/401), NOT on finalize-side 5xx.
- Idempotent SSO cookie re-issue: if `klai_sso` already exists on the request, do NOT issue a fresh one on retry — preserve the original-issuer semantics.
