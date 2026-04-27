---
id: SPEC-SEC-MFA-001
version: 0.3.0
status: completed
created: 2026-04-24
updated: 2026-04-27
author: Mark Vletter
priority: high
tracker: SPEC-SEC-AUDIT-2026-04
lifecycle: spec-first
---

# SPEC-SEC-MFA-001: MFA Fail-Closed in Login Flow

## HISTORY

### v0.3.0 (2026-04-27)
- Implementation completed via `/moai run` + `/moai sync` cycle on branch
  `feature/SPEC-SEC-MFA-001` (commits 623e4aa6 + b08f26d1 + sync commit).
- Status: draft → completed (Level 1 spec-first lifecycle).
- See § Implementation Notes for divergences, deferred items and the
  Cornelis audit closure mapping.

### v0.2.0 (2026-04-24)
- Expanded stub to full EARS-format SPEC via `/moai plan SPEC-SEC-MFA-001`
- Added REQ-1..REQ-5 with seven testable sub-requirements each
- Added research.md (login-flow analysis, mfa_policy resolution, Zitadel SLA,
  fail-open path catalogue) and acceptance.md (respx-backed scenarios that
  invert the current `TestMFAPolicyEnforcement` suite)
- Regression note: `klai-portal/backend/tests/test_auth_security.py` already
  contains `test_mfa_check_failure_defaults_to_pass` and
  `test_mfa_policy_lookup_failure_defaults_to_optional` which DOCUMENT the
  current fail-open behaviour. Both tests MUST be rewritten (not deleted) to
  assert the new fail-closed behaviour when `mfa_policy="required"`, and
  both MUST keep the `optional` branch asserting fail-open.

### v0.1.0 (2026-04-24)
- Stub created from SPEC-SEC-AUDIT-2026-04 (Cornelis audit 2026-04-22)
- Priority P1 — MFA is bypassed under plausible failure conditions

---

## Findings addressed

| # | Finding | Severity | Verdict |
|---|---|---|---|
| 11 | MFA check fails open on Zitadel HTTPStatusError (`user_has_mfa = True`) | HIGH | VERIFIED |
| 12 | Pre-auth `find_user_by_email` failure leaves `zitadel_user_id = None` → MFA block skipped | HIGH | VERIFIED |

Source file references:
- `klai-portal/backend/app/api/auth.py:363-422` (`login` handler)
- `klai-portal/backend/app/services/zitadel.py:362-395` (`find_user_by_email`, `has_any_mfa`, `has_totp`)

---

## Goal

Convert MFA enforcement in the login flow to **fail-closed**: any failure to
determine whether a user has MFA configured — whether that failure is an HTTP
5xx from Zitadel, a connection error, or a DB-level lookup failure against
`portal_users` / `portal_orgs` — must result in a login rejection (HTTP 503)
when the resolved `mfa_policy == "required"`, not an implicit bypass.

When `mfa_policy == "optional"`, fail-open behaviour is preserved and
explicitly documented as acceptable — because no MFA enforcement is expected
for those orgs, a failure to determine MFA state cannot weaken a contract
that does not exist.

The fix also closes finding #12: when the pre-auth `find_user_by_email`
lookup raises, the current code swallows the exception, leaves
`zitadel_user_id = None`, and the MFA block at `auth.py:409-422` is skipped
entirely because of the `if zitadel_user_id:` guard. After this SPEC, a
`find_user_by_email` 5xx results in a 503 before `create_session_with_password`
is even called — preserving the invariant that MFA enforcement cannot be
silently skipped.

---

## Environment

- **Service:** `klai-portal/backend` (Python 3.13, FastAPI, SQLAlchemy async)
- **Files in scope:**
  - `klai-portal/backend/app/api/auth.py` — `login` and `totp_login` handlers
    (lines 359-456; MFA block lines 395-422)
  - `klai-portal/backend/app/services/zitadel.py` — Zitadel client wrapper
    (`find_user_by_email` line 362, `has_any_mfa` line 382, `has_totp` line 375)
  - `klai-portal/backend/app/models/portal.py` — `PortalOrg.mfa_policy`
    enum column (`optional | recommended | required`), default `optional`
  - `klai-portal/backend/tests/test_auth_security.py` — existing regression
    suite; `TestMFAPolicyEnforcement` tests (lines 294-465) must be updated
- **Identity provider:** Zitadel (auth.getklai.com, service account
  `portal-api` SA ID `362780577813757958` — see `.claude/rules/klai/platform/zitadel.md`)
- **Observability:** structlog JSON → Alloy → VictoriaLogs; Grafana for alerts
  (see `.claude/rules/klai/infra/observability.md`)

---

## Assumptions

- `mfa_policy` is configured **per-org** in `portal_orgs.mfa_policy` and
  defaults to `"optional"`. Values: `optional`, `recommended`, `required`.
  `recommended` behaves identically to `optional` at login time (no hard
  block); only `required` triggers enforcement.
- Zitadel's availability SLA (operated by Klai on core-01) is high enough
  that a transient 503 response to a Zitadel outage is a strictly better
  user experience than an undetected MFA bypass. Users can retry within
  seconds; an attacker cannot force a bypass by DoSing Zitadel.
- The decision to fail-open on `mfa_policy="optional"` is a deliberate
  trade-off: orgs that have not opted into MFA accept lower security for
  higher availability. This is explicitly scoped in REQ-3.
- DB-level lookup failures against `portal_users` / `portal_orgs` are rare
  and almost always infrastructure problems (RLS GUC leak per
  `portal-backend.md`, connection exhaustion, migration window). Under
  `mfa_policy="required"` we prefer 503 over proceeding with
  `mfa_policy="optional"` as the default (current code behaviour).
- `request_id` is available in the request context via
  `LoggingContextMiddleware` and will be included in every
  `mfa_check_failed` log entry for cross-service correlation.

---

## Out of Scope

- Migration to WebAuthn / passkeys (separate strategic SPEC).
- MFA policy UI / org-admin settings UX (separate product SPEC; the
  admin API at `app/api/admin/settings.py:60-77` is unchanged).
- Fallback MFA flows (backup codes, SMS) — existing `totp_login` handler
  is unchanged.
- Global default hardening (changing `PortalOrg.mfa_policy` default from
  `optional` to `required`) — roadmap item, tracked separately.
- Retry automation on the client side (user-retry via UI is sufficient;
  no backoff logic is added to the browser).
- Zitadel client-side circuit breaker. `httpx` connection pooling and the
  standard `Retry-After` header suffice. A full circuit breaker is a
  future infra concern.

---

## Threat Model

**Primary threat:** a silent MFA bypass under any Zitadel- or DB-level
failure. An attacker who can trigger either condition — whether by
partially DoSing Zitadel, by causing DB load (RLS GUC leak, lock
contention), or by exploiting a rare 5xx window — currently has a
plausible path to logging in **without** MFA on an account that has
`mfa_policy="required"`.

**Adversary scenarios:**

1. **Opportunistic bypass via Zitadel flap.** Attacker with a valid
   password for a user whose org enforces MFA. Attacker times login
   attempts during a Zitadel restart / flap (monitoring auth.getklai.com
   externally). Currently: `has_any_mfa` raises `HTTPStatusError(500)`
   → code catches it and sets `user_has_mfa = True` → login proceeds
   without MFA. After this SPEC: 503, attacker must retry and cannot
   bypass.

2. **Bypass via DB-level failure.** Attacker triggers a load pattern
   that causes `db.scalar(select(PortalUser)...)` to fail (pool
   exhaustion, RLS guard raising on a leaked GUC). Currently: the
   `except Exception` branch logs a warning and defaults
   `mfa_policy = "optional"` — MFA enforcement never runs. After this
   SPEC: the default remains `optional` only when the org is genuinely
   `optional`; if the portal-user row exists but the org lookup fails,
   we escalate to 503 rather than silently downgrading.

3. **Bypass via pre-auth Zitadel 5xx (finding #12).** `find_user_by_email`
   raises before the MFA block is entered. `zitadel_user_id` stays
   `None`. The `if zitadel_user_id:` guard at line 398 skips the whole
   MFA block. Login proceeds. After this SPEC: the outer `try` block
   at line 364 catches `HTTPStatusError` and re-raises as 503 before
   `create_session_with_password` is called.

**Explicit non-goals:**
- Defeating an attacker with a valid Zitadel session token already
  issued (that is `/auth/me` and cookie/session territory — SPEC-SEC-SESSION-001).
- Protecting users whose org has `mfa_policy="optional"`. Those orgs
  have opted out; no enforcement is attempted.

---

## Requirements

### REQ-1: Fail-Closed on has_any_mfa Zitadel Failure

WHEN `mfa_policy == "required"` AND `zitadel.has_any_mfa(zitadel_user_id)`
raises `httpx.HTTPStatusError` or `httpx.RequestError`, THE login handler
SHALL reject the request with HTTP 503 and a stable error body.

- **REQ-1.1:** WHEN `has_any_mfa` raises `httpx.HTTPStatusError` AND the
  resolved `mfa_policy == "required"`, THE login handler SHALL raise
  `HTTPException(status_code=503, detail="Authentication service
  temporarily unavailable, please retry in a moment")`.
- **REQ-1.2:** WHEN `has_any_mfa` raises `httpx.RequestError` (connection
  error, timeout) AND the resolved `mfa_policy == "required"`, THE
  login handler SHALL raise the same 503 as REQ-1.1.
- **REQ-1.3:** THE 503 response SHALL include a `Retry-After: 5` header
  so browsers and API consumers have a documented minimum back-off.
- **REQ-1.4:** THE handler SHALL NOT set `user_has_mfa = True` as a
  fallback. The existing line `auth.py:417 user_has_mfa = True` SHALL
  be deleted. No code path under `mfa_policy="required"` may treat an
  unknown MFA state as "has MFA".
- **REQ-1.5:** THE 503 SHALL be raised BEFORE any cookie is set and
  BEFORE `_finalize_and_set_cookie` is called. The caller receives no
  authenticated session artefact on this path.
- **REQ-1.6:** IF `has_any_mfa` raises a non-HTTP exception that is
  neither `HTTPStatusError` nor `RequestError` (genuinely unexpected),
  THE handler SHALL still fail closed via a bare
  `except Exception` fallback that raises the same 503 and logs with
  `event="mfa_check_failed"`, `reason="unexpected"`.
- **REQ-1.7:** The regression test at
  `test_auth_security.py::test_mfa_check_failure_defaults_to_pass`
  SHALL be renamed to
  `test_mfa_check_failure_returns_503_when_required` AND its assertion
  SHALL be inverted (expect `HTTPException(status_code=503)`, not
  `result is not None`).

### REQ-2: Fail-Closed on find_user_by_email Failure

WHEN the pre-auth `zitadel.find_user_by_email(body.email)` call raises
because of a Zitadel 5xx or connection error, THE login handler SHALL
reject the request with HTTP 503 BEFORE calling
`create_session_with_password`.

- **REQ-2.1:** WHEN `find_user_by_email` raises `httpx.HTTPStatusError`
  with status code >= 500, THE handler SHALL raise `HTTPException(503,
  "Authentication service temporarily unavailable, please retry in a
  moment")` with `Retry-After: 5`.
- **REQ-2.2:** WHEN `find_user_by_email` raises `httpx.RequestError`,
  THE handler SHALL raise the same 503 as REQ-2.1.
- **REQ-2.3:** IF `find_user_by_email` raises `HTTPStatusError` with a
  4xx status code (Zitadel explicitly says "no such user" via 404), THE
  handler SHALL treat this as `user_info = None` and continue the
  password-check path. 4xx is a well-formed "not found" response, not
  an infrastructure failure.
- **REQ-2.4:** THE existing catch-all `except httpx.HTTPStatusError` at
  `auth.py:369-370` SHALL be split into:
  (a) 4xx / "not found" path → set `user_info = None`, continue
  (b) 5xx / RequestError path → log `mfa_check_failed` with reason
      `find_user_by_email_5xx` and raise 503
- **REQ-2.5:** No code path SHALL allow `zitadel_user_id` to remain
  `None` after a 5xx from `find_user_by_email` AND then enter the MFA
  block. This closes finding #12.
- **REQ-2.6:** The `has_totp` call that runs inside the same try block
  (`auth.py:368`) SHALL be moved OUT of the pre-auth try. After
  finding (or not finding) the user, `has_totp` runs independently and
  is allowed to fail open (TOTP state affects only UX flow, not
  enforcement) — its failure surface is documented in research.md.
- **REQ-2.7:** A new regression test
  `test_find_user_by_email_5xx_returns_503` SHALL assert that a 500
  response from Zitadel on `/v2/users` rejects the login with HTTP 503.

### REQ-3: Fail-Open on mfa_policy=optional Is Preserved

WHEN `mfa_policy == "optional"` (or `"recommended"`), THE login handler
SHALL preserve the existing fail-open behaviour on Zitadel or DB
failures. The 503 escalation in REQ-1 and REQ-2 is gated on
`mfa_policy == "required"` ONLY.

- **REQ-3.1:** WHEN `mfa_policy == "optional"` AND `has_any_mfa` raises,
  THE handler SHALL log `mfa_check_failed` with `reason="optional_policy"`
  at level `warning` AND proceed with the login (no MFA required, no
  session rejection).
- **REQ-3.2:** WHEN the portal-user or portal-org DB lookup fails AND the
  caller's email cannot be mapped to a portal-org, THE handler SHALL
  default `mfa_policy = "optional"` (current behaviour) BUT SHALL emit
  `mfa_check_failed` with `reason="db_lookup_failed"` at level
  `warning` so the fail-open path is monitorable.
- **REQ-3.3:** WHEN `find_user_by_email` returns `None` (user genuinely
  not found in Zitadel), THE handler SHALL continue to the password
  check which will fail with 401 — this is unchanged.
- **REQ-3.4:** WHEN `mfa_policy == "recommended"`, behaviour SHALL be
  identical to `optional` at login time. `recommended` is a UI-only
  hint that is surfaced in `/api/me` and the admin console; it does
  not gate login.
- **REQ-3.5:** The regression test
  `test_mfa_optional_no_enforcement` (currently at
  `test_auth_security.py:368`) SHALL remain unchanged — it still
  asserts fail-open behaviour under `optional`.
- **REQ-3.6:** A new regression test
  `test_mfa_optional_5xx_proceeds_documented_fail_open` SHALL assert
  that `has_any_mfa` raising 500 under `mfa_policy="optional"` does
  NOT return 503 and DOES emit the `mfa_check_failed` warning log.
- **REQ-3.7:** The SPEC commentary SHALL explicitly document this
  trade-off: `optional` orgs accept availability over enforcement.
  The comment block at `auth.py:395-396` SHALL be updated to reference
  SPEC-SEC-MFA-001.

### REQ-4: Structured mfa_check_failed Events and Grafana Alert

THE login handler SHALL emit a structured log event for every MFA check
failure (whether fail-closed or fail-open) so that the rate of such
failures is visible in Grafana and alertable.

- **REQ-4.1:** WHEN `has_any_mfa` raises OR `find_user_by_email` raises
  with 5xx OR the DB lookup for portal-user/portal-org raises, THE
  handler SHALL emit a structlog entry with `event="mfa_check_failed"`,
  including the fields: `reason` (one of `has_any_mfa_5xx`,
  `find_user_by_email_5xx`, `db_lookup_failed`, `unexpected`),
  `mfa_policy` (as resolved, or `"unresolved"` when the lookup itself
  failed), `zitadel_status` (integer HTTP status when available, else
  `null`), `email_hash` (sha256 hex digest of the lower-cased email,
  NOT the plaintext), `outcome` (`503` or `fail-open`).
- **REQ-4.2:** THE log entry SHALL be emitted at level `warning` when
  `outcome="fail-open"` AND at level `error` when `outcome="503"`.
- **REQ-4.3:** THE log entry SHALL NEVER contain the plaintext email,
  the password, the Zitadel user_id of an unrelated user, or any
  cookie / session token content. `email_hash` is the only permitted
  user-identifying field.
- **REQ-4.4:** THE log entry SHALL include `request_id` via the existing
  structlog contextvars bound by `LoggingContextMiddleware` (automatic,
  no manual propagation).
- **REQ-4.5:** A Grafana alert SHALL be defined in
  `klai-infra/deploy/grafana/alerts/mfa-check-failed.yaml` that fires
  WHEN the rate of `mfa_check_failed` events exceeds 1 per minute
  sustained over a 5-minute window (LogsQL query:
  `service:portal-api AND event:mfa_check_failed`). The alert SHALL
  name a recipient and link to this SPEC in its description.
- **REQ-4.6:** A second, higher-severity Grafana alert SHALL fire WHEN
  the rate of `mfa_check_failed` events with `outcome="fail-open"`
  exceeds 10 per minute — this would indicate either a Zitadel-wide
  outage affecting many optional-policy orgs OR an active bypass
  attempt combined with a policy mis-configuration.
- **REQ-4.7:** THE alert definitions SHALL include a runbook link to a
  new file `docs/runbooks/mfa-check-failed.md` describing triage
  steps (check Zitadel health, check DB pool stats, check for recent
  policy changes) — file creation deferred to Run phase.

### REQ-5: Regression Coverage for Fail-Closed Paths

THE test suite SHALL contain pytest + respx regression tests for every
fail-closed branch introduced by this SPEC, and the existing tests
that document the OLD fail-open behaviour SHALL be rewritten to assert
the NEW behaviour.

- **REQ-5.1:** A new test module `tests/test_auth_mfa_fail_closed.py`
  SHALL use `respx` (already a dev dependency of
  `klai-portal/backend`) to mock the Zitadel HTTP surface: `/v2/users`
  (for `find_user_by_email`), `/v2/users/{id}/authentication_methods`
  (for `has_any_mfa` / `has_totp`), and `/v2/sessions` (for
  `create_session_with_password`). Each test mounts a minimal
  respx router that returns 500 on the URL under test.
- **REQ-5.2:** The suite SHALL cover every scenario in acceptance.md:
  (a) `has_any_mfa` 500 + `mfa_policy="required"` → 503
  (b) `find_user_by_email` 500 → 503
  (c) `has_any_mfa` 500 + `mfa_policy="optional"` → 200 with warning log
  (d) Happy path MFA login (regression)
  (e) Happy path no-MFA login under `optional` (regression)
  (f) `find_user_by_email` 404 → 200 path with password-check 401
      (documents REQ-2.3 4xx-is-not-5xx handling)
  (g) DB lookup raises + `mfa_policy` cannot be resolved → 503 when the
      portal-user row exists but the org fetch failed; fail-open only
      when the portal-user row itself cannot be loaded.
- **REQ-5.3:** The existing
  `TestMFAPolicyEnforcement::test_mfa_check_failure_defaults_to_pass`
  test SHALL be deleted and replaced by REQ-5.2(a). Leaving the old
  test named as-is would preserve the dangerous pattern in the test
  corpus.
- **REQ-5.4:** The existing
  `TestMFAPolicyEnforcement::test_mfa_policy_lookup_failure_defaults_to_optional`
  SHALL be narrowed: the "portal_user lookup fails" path remains
  fail-open (cannot know whether the email belongs to a required-MFA
  org), BUT the "portal_user found + org lookup fails" path SHALL be
  converted to expect 503.
- **REQ-5.5:** Each test SHALL assert on the structured log output
  using `caplog` + `structlog` test fixtures, verifying that the
  `mfa_check_failed` event fires with the expected `reason` and
  `outcome` fields (per REQ-4.1).
- **REQ-5.6:** Overall coverage for `klai-portal/backend/app/api/auth.py`
  SHALL be >= 85% (TRUST 5 gate). The login handler specifically
  SHALL have 100% branch coverage for the MFA enforcement block
  (lines 395-422 in the current file, line numbers will shift
  post-refactor).
- **REQ-5.7:** Tests SHALL NOT patch `app.api.auth.zitadel` as a
  MagicMock (the style of the current `test_auth_security.py`
  `TestMFAPolicyEnforcement` suite). Tests SHALL instead use respx
  against the real `ZitadelClient` instance — this catches regressions
  in the client wrapper itself (e.g. if a future refactor swallows
  `HTTPStatusError` inside `has_any_mfa`).

---

## Non-Functional Requirements

- **Performance:** The additional log emission and the splitting of the
  try/except block SHALL add less than 1 ms p95 overhead to the login
  path. No new DB queries and no new Zitadel round-trips are
  introduced.
- **Observability:** Every fail-closed outcome SHALL be visible in
  VictoriaLogs via `service:portal-api AND event:mfa_check_failed` AND
  cross-correlatable with the Caddy access log via `request_id`.
- **Privacy:** `email_hash` (sha256 of the lowercased email) is the
  only user-identifying field. Zitadel user_id appears only when it
  has already been resolved — never as part of an authentication
  failure response that could leak membership.
- **Backward compatibility:** The `LoginResponse` schema is unchanged.
  Client-side code that already handles the generic 502 path continues
  to work; 503 with `Retry-After` is a strictly more informative
  subset.
- **Fail modes:** REQ-3 fail-open is deliberate and documented.
  `recommended` behaves like `optional` at login time. No other
  fail-open paths remain in the MFA enforcement block after this SPEC
  lands.

---

## Success Criteria

- `zitadel.has_any_mfa()` raising `HTTPStatusError` under
  `mfa_policy="required"` causes the login request to be rejected with
  a 503 response carrying `Retry-After: 5` and a stable error body.
- `find_user_by_email` raising 5xx before `create_session_with_password`
  causes a 503 with `Retry-After: 5`; a 4xx response continues the
  password-check path (treated as "user not found").
- When `mfa_policy == "required"`, no code path allows the login to
  succeed without a proven positive MFA check (manual inspection +
  test REQ-5.6 branch coverage).
- A structured log event `mfa_check_failed` is emitted for every
  MFA-check failure with `reason`, `mfa_policy`, `zitadel_status`,
  `email_hash`, `outcome`, and auto-bound `request_id`.
- Grafana alert `mfa-check-failed` fires on >1 event/min sustained for
  5 minutes; higher-severity alert fires on >10 fail-open events/min.
- Regression tests using `respx`-mocked Zitadel 5xx responses:
  - `mfa_policy="required"` + `has_any_mfa` 5xx → 503
  - `find_user_by_email` 5xx → 503
  - `mfa_policy="optional"` + `has_any_mfa` 5xx → 200 (documented
    fail-open)
  - Happy path MFA login → `totp_required`
  - Happy path no-MFA login under `optional` → 200 + cookie
- `test_auth_security.py::test_mfa_check_failure_defaults_to_pass` is
  removed; its replacement asserts 503.
- Code coverage for `auth.py` MFA block is 100% branch coverage.

---

## Cross-references

- Tracker: [SPEC-SEC-AUDIT-2026-04](../SPEC-SEC-AUDIT-2026-04/spec.md)
- Research: [research.md](./research.md)
- Acceptance: [acceptance.md](./acceptance.md)
- Platform rules: [.claude/rules/klai/platform/zitadel.md](../../../.claude/rules/klai/platform/zitadel.md)
- Observability: [.claude/rules/klai/infra/observability.md](../../../.claude/rules/klai/infra/observability.md)
- Source under change:
  - [klai-portal/backend/app/api/auth.py](../../../klai-portal/backend/app/api/auth.py)
  - [klai-portal/backend/app/services/zitadel.py](../../../klai-portal/backend/app/services/zitadel.py)

---

## Implementation Notes (v0.3.0 — 2026-04-27)

This section is appended at the close of the Level 1 (spec-first) lifecycle
to record what was actually built versus what was planned. It is the
canonical answer to "did this SPEC ship?" and to "where did it diverge
from the plan?".

### Status

- Branch: `feature/SPEC-SEC-MFA-001`
- Commits:
  - `623e4aa6` — fix(portal-api,sec): MFA fail-closed enforcement
  - `b08f26d1` — fix(portal-api,sec): MFA polish — structlog + orphan-org visibility
  - sync commit (this commit) — docs(sync): SPEC status + MX anchors + CHANGELOG
- Verification at sync time: pytest 1160 passed (23 auth-specific), ruff
  clean, pyright 0/0/0.

### Coverage of REQ-1..REQ-5

| REQ | Outcome |
|---|---|
| REQ-1.1..1.7 | Implemented in `_resolve_and_enforce_mfa` + login pre-auth split. Verified in `test_auth_mfa_fail_closed.py` scenarios 1, 4, 8, plus REQ-1.6 generic-Exception variant. |
| REQ-2.1..2.7 | Implemented via split pre-auth try in `login()`. 4xx-as-not-found preserved (scenario 6 + comment). REQ-2.2 RequestError variant added as Run-phase test. |
| REQ-3.1..3.7 | Implemented per the SPEC's clarified short-circuit reading: `has_any_mfa` is not called under `mfa_policy in {"optional", "recommended"}`, so the fail-open warning fires only when the lookup is actually attempted (DB path). REQ-3.4 recommended variant covered. REQ-3.7 SPEC-cross-reference comment added. |
| REQ-4.1..4.4 | `_emit_mfa_check_failed` helper emits structlog event with all fields. Email is sha256-hashed; `request_id` is auto-bound by `LoggingContextMiddleware`. |
| REQ-4.5..4.7 | Grafana alerts and runbook delivered (see Divergences below for path correction). |
| REQ-5.1..5.5 | New module `tests/test_auth_mfa_fail_closed.py` with 13 respx-backed scenarios. caplog/`structlog.testing.capture_logs()` assertions on every fail-closed scenario. |
| REQ-5.6 | **Partial**. The MFA enforcement block (helpers and login refactor) has full branch coverage. The "overall ≥85% on `app.api.auth`" target is **not met** (current 64%, pre-existing) because other endpoints in the same file (TOTP setup, IDP intent, password reset, sso_complete) lack tests. Closing this gap requires adding tests for endpoints unrelated to MFA — out of scope per the `minimal-changes` pitfall. **Recommended follow-up SPEC**: `auth.py` coverage hardening for the remaining endpoints. |
| REQ-5.7 | The new test module uses `respx` against the real `ZitadelClient` instance — no `MagicMock` on `app.api.auth.zitadel`. Existing `TestMFAPolicyEnforcement` retains the legacy `patch(...)` style for the three regression tests that survived REQ-5.3. |

### Divergences from spec.md plan

1. **Grafana alert path correction.** spec.md REQ-4.5/4.7 specified
   `klai-infra/deploy/grafana/alerts/mfa-check-failed.yaml`. Actual path:
   `deploy/grafana/provisioning/alerting/portal-mfa-rules.yaml` in the
   superproject. Reason: Grafana provisioning is owned by the superproject's
   `deploy/grafana/` tree, not by the `klai-infra` submodule (the submodule
   only contains host-level deploy scripts). No submodule pin bump was
   needed. The runbook
   [docs/runbooks/mfa-check-failed.md](../../../docs/runbooks/mfa-check-failed.md)
   was created in the superproject `docs/runbooks/` directory matching the
   existing convention.

2. **respx as new dev dependency.** spec.md REQ-5.1 said respx was already
   a dev dependency of `klai-portal/backend`. It was not. Added
   `respx>=0.22` to both `[project.optional-dependencies] dev` and
   `[dependency-groups] dev` in `klai-portal/backend/pyproject.toml`.
   `uv.lock` regenerated.

3. **Run-phase test additions.** Acceptance scenarios noted three "Run-phase
   addition" cases that were not in the original 8-scenario block: REQ-1.6
   (generic Exception during `has_any_mfa`), REQ-2.2 (`find_user_by_email`
   `RequestError`), REQ-3.4 (`mfa_policy="recommended"` behaves like
   optional). All three were added during Run.

4. **Orphan PortalOrg FK observability** (post-initial polish). Self-review
   surfaced an edge case the SPEC did not explicitly cover: `db.scalar`
   returns a `PortalUser`, but `db.get(PortalOrg, ...)` returns `None`
   (deleted/soft-deleted org while the FK still points at it). Pre-existing
   behaviour silently fell back to `mfa_policy="optional"` with no signal.
   Added an explicit branch in `_resolve_and_enforce_mfa` that keeps
   fail-open semantics (the user logs in) but emits `mfa_check_failed`
   warning so the orphan is visible in Grafana. New test:
   `test_portal_user_orphan_org_proceeds_documented_fail_open`.

5. **Logger choice for `has_totp` fail-open warning.** Switched from stdlib
   `logger.warning` to `_slog.warning` to honour
   `.claude/rules/klai/projects/portal-logging-py.md`'s "structlog for new
   log statements" rule.

### Out-of-test verification (deferred to deploy / post-merge)

These items from acceptance.md "Out-of-test verification" cannot be
discharged by unit tests; they will be confirmed during the post-merge
deploy on core-01:

- Grafana alert rules load on production Grafana (after PR merge triggers
  `alerting-check.yml` workflow; manual visual check in Grafana UI under
  Alerting → mfa-check-failed).
- LogsQL query
  (`service:portal-api event:mfa_check_failed`) returns the expected
  schema in production VictoriaLogs.
- Runbook reachable from alert annotation (Grafana alert detail view shows
  the `runbook_url` link).
- Manual code-review of the final `auth.py::login` handler against the
  fail-open path catalogue in research.md §4 — performed during this sync
  phase: only FO-1-4xx (well-formed not-found), FO-2 (has_totp UI flag) and
  FO-4 (portal_user not in portal — provisioning grace) remain as
  documented fail-open paths under `mfa_policy="optional"` orgs. All other
  fail-open holes are closed.

### Cornelis audit closure

This SPEC closes the following entries in
[SPEC-SEC-AUDIT-2026-04](../SPEC-SEC-AUDIT-2026-04/spec.md):

- **#11** — `has_any_mfa` HTTPStatusError fail-open (`user_has_mfa = True`).
  Resolved: 503 + `Retry-After: 5`.
- **#12** — `find_user_by_email` failure leaving `zitadel_user_id = None`
  → MFA block skipped. Resolved: pre-auth try split; 5xx ≥500 escalates
  to 503 BEFORE `create_session_with_password` runs.
