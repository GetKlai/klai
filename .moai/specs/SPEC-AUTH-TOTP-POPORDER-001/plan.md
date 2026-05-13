# SPEC-AUTH-TOTP-POPORDER-001 — Implementation Plan

## Approach

Refactor `_pending_totp` from a flat dict-of-tokens into a small state
machine. Each entry is a dict with an explicit ``state`` field
(``awaiting-totp`` | ``finalize-pending``) plus a ``finalize_expiry``
timestamp set when state transitions to ``finalize-pending``.

The handler ``totp_login`` becomes a state-aware dispatcher:

1. Look up entry by ``temp_token``. If absent OR expired → 400 "Session
   expired" (current behaviour preserved for the no-pending case).
2. If state is ``awaiting-totp``: existing flow — call Zitadel TOTP
   verify, on success transition to ``finalize-pending`` and call
   ``_finalize_and_set_cookie``.
3. If state is ``finalize-pending``: skip Zitadel TOTP verify, just
   re-call ``_finalize_and_set_cookie``. Idempotent.
4. Pop entry only on terminal outcomes (success, lockout, user-error).

## Task Decomposition

| # | Task | File | Risk |
|---|---|---|---|
| 1 | Hoist ``_pending_totp`` schema into a TypedDict; add ``state`` + ``finalize_expiry`` fields with backward-compatible defaults | `app/api/auth.py` | Low — additive change to in-memory dict shape |
| 2 | Introduce ``_PendingTOTP`` state-machine helper class with `put`, `get`, `transition_to_finalize_pending`, `pop`, `cleanup_expired` methods | `app/api/auth.py` | Medium — concentration of state handling logic; test before refactoring callers |
| 3 | Update ``login`` (creating awaiting-totp entry) and ``totp_login`` (state-aware dispatch) to use the helper | `app/api/auth.py` | Medium — touches the auth happy path; full integration tests required |
| 4 | Add expiry sweep — best-effort cleanup of expired ``finalize-pending`` entries on any ``totp_login`` request | `app/api/auth.py` | Low — opportunistic cleanup, not a hard invariant |
| 5 | Tests for all 5 acceptance scenarios in `acceptance.md` | new `tests/test_auth_totp_finalize_retry.py` | Low — pure unit tests with mocked Zitadel |
| 6 | Update `@MX:ANCHOR` on `_pending_totp` to reflect the state-machine invariants | `app/api/auth.py` | Low — annotation only |

## Files Affected

- `klai-portal/backend/app/api/auth.py` — `_pending_totp` schema, `login`, `totp_login`, `_PendingTOTP` helper class
- `klai-portal/backend/tests/test_auth_totp_finalize_retry.py` (new) — 5 G/W/T scenarios + expiry-sweep coverage
- `klai-portal/backend/tests/test_auth_totp_endpoints.py` — extend existing TOTP tests if helper-class refactor affects them (read-before-edit)

## Technology Choices

- **No external dependency.** ``_pending_totp`` stays an in-memory dict — Redis migration is a separate concern (cross-service horizontal-scaling SPEC, see Out of Scope).
- **TypedDict** over dataclass to keep the structure JSON-serialisable for any future Redis port (forward-compatible).
- **Explicit state field** instead of "implicit state via field presence" (e.g. checking if ``finalize_expiry`` exists) — explicit is auditable, presence-checks are not.
- **Time source via ``time.monotonic()``** for the 60s expiry — wall-clock skew during NTP correction would otherwise cause early expiry.

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| The ``_PendingTOTP`` helper masks a subtle bug (e.g. `get` returning a copy when callers expect a reference) | Helper exposes only string-keyed primitives + opaque transitions; callers cannot reach into entry internals |
| Idempotent retry leaks the SSO cookie (one user, two cookies) | Retry path explicitly checks for an existing cookie via the request and refuses to issue a second; test in AC-5 |
| Expiry sweep amplifies the cost of a high-volume login storm | Sweep is opportunistic (one entry max per request) and bounded by the dict size; in production the dict rarely exceeds ~10 entries (TOTP retry window is short) |
| Behaviour change breaks existing integration tests | Run full `pytest -q` after each task above, not only at the end |

## Success Criteria

- All 5 acceptance scenarios in `acceptance.md` pass.
- The full portal-api test suite stays green (currently 1399+, post-FU#1 1407).
- No regression in TOTP-related metric / event names — `totp_login_failed` reasons stay enumerable.
- The pop-ordering bug is unreproducible: triggering a 5xx in `_finalize_and_set_cookie` followed by a retry within 60s yields a successful login, NOT a "Session expired" 400.

## Out of Scope

- Replacing the in-memory dict with Redis. Single-instance limitation persists; horizontal scaling is a separate SPEC (cross-cuts auth + retrieval).
- Changing TOTP code-validation semantics (one-time-use, ``_TOTP_MAX_FAILURES`` accounting).
- Changing the response message wording for the existing failure modes.

## Ordering & Branch Strategy

Single PR. Tasks 1-2 (schema + helper) committed first as a no-op
refactor; tasks 3-4 (handler) on top; tasks 5-6 (tests + MX) last.
PR body must include the AC matrix and the rollback command.
