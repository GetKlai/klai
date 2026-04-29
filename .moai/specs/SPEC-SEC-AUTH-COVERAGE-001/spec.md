---
id: SPEC-SEC-AUTH-COVERAGE-001
version: 0.3.0
status: completed
created: 2026-04-27
updated: 2026-04-28
author: Mark Vletter
priority: medium
issue_number: 0
tracker: SPEC-SEC-AUDIT-2026-04
lifecycle: spec-first
predecessor: SPEC-SEC-MFA-001
---

# SPEC-SEC-AUTH-COVERAGE-001: auth.py coverage + observability hardening

## HISTORY

### v0.2.0 (2026-04-27, later same day)
- **Scope expanded from 8 to 14 endpoints.** The original v0.1.0 deferred
  passkey × 2, email_otp × 3, verify_email, and idp_signup × 2 with
  hand-wave reasons ("separate follow-up", "same flow class"). Self-review
  surfaced that the deferral arguments did not survive scrutiny: the same
  Cornelis-audit context that drives MFA-001 / this SPEC applies equally
  to those 6 endpoints. Specifically: passkey-confirm and email-otp-confirm
  have the SAME brute-force surface as totp-confirm; verify_email is a
  one-shot security gate; idp_signup callback is structurally identical
  to idp_callback — testing one without the other leaves a coverage island.
- Endpoints added in scope: `passkey_setup`, `passkey_confirm`,
  `email_otp_setup`, `email_otp_confirm`, `email_otp_resend`,
  `verify_email`, `idp_intent_signup`, `idp_signup_callback`.
- REQ structure restructured (still 5 REQs) so all 14 endpoints fit:
  REQ-1 expanded to "MFA enrollment family" (TOTP + passkey + email_otp =
  7 endpoints); REQ-2 expanded to "session-reuse + TOTP login";
  REQ-3 expanded to "IDP login + signup flows"; REQ-4 expanded to
  "password reset/set + verify_email"; REQ-5 (cross-cutting) unchanged.
- Test scenarios projection: ~70 (was ~42).

### v0.1.0 (2026-04-27)
- Initial draft created via `/moai plan SPEC-SEC-AUTH-COVERAGE-001`.
- Closes the deferred REQ-5.6 from
  [SPEC-SEC-MFA-001](../SPEC-SEC-MFA-001/spec.md): bring overall coverage
  on `klai-portal/backend/app/api/auth.py` to ≥85%.
- Propagates the structlog + structured-event + @MX:ANCHOR patterns
  established by SPEC-SEC-MFA-001 to eight initial in-scope auth
  endpoints. Six additional endpoints were initially deferred — see v0.2.0
  for the rationale why that deferral did not hold.
- Cornelis-audit findings on these endpoints (#13, #15, #16, A4) are
  out of scope — tracked by SPEC-SEC-SESSION-001 and SPEC-SEC-INTERNAL-001.

---

## Goal

Two goals, in priority order:

1. **Coverage** — Reach ≥85% line coverage and ≥95% branch coverage on
   `klai-portal/backend/app/api/auth.py` measured via `pytest --cov`. This
   closes SPEC-SEC-MFA-001 REQ-5.6 (deferred there per `minimal-changes`).
2. **Observability hardening** — Apply the SPEC-SEC-MFA-001 pattern set
   (structlog `_slog` for new log statements, structured `mfa_check_failed`-style
   events on every failure leg, sha256-hashed email PII, audit log presence
   on every state-changing endpoint, @MX:ANCHOR on shared helpers with
   fan_in ≥3) to the remaining eight in-scope auth endpoints. The
   underlying behaviour is preserved — this SPEC adds visibility and tests,
   it does NOT change response shapes or status codes for any endpoint.

---

## Findings addressed

This SPEC does NOT close audit findings — those are tracked in their own
SPECs. It addresses the deferred coverage target and the observability
asymmetry between `login` (covered by SPEC-SEC-MFA-001) and the eight
sibling endpoints in the same file.

| Source | Item | Resolution |
|---|---|---|
| SPEC-SEC-MFA-001 REQ-5.6 | "Overall coverage on `app.api.auth` SHALL be ≥85%" — deferred | Closed via REQ-5 of this SPEC. |
| Internal observability gap | login / totp_login emit `audit.log_event`; six other endpoints do not | Closed via REQ-1..REQ-4 audit-log additions. |
| Internal observability gap | login emits structured `mfa_check_failed` events; other endpoints emit nothing structured | Closed via REQ-1..REQ-4 `_emit_auth_event` additions. |
| portal-logging-py.md rule | "Always use structlog for new log statements" | Closed via REQ-5 stdlib→structlog migration on the 16 logger.* call sites. |
| MX tag protocol | `_finalize_and_set_cookie` (fan_in=3), `_validate_callback_url` (fan_in=3), `_emit_auth_event` (fan_in≥15) lack @MX:ANCHOR | Closed via REQ-5 anchor additions. |

---

## Environment

- **Service**: `klai-portal/backend` (Python 3.13, FastAPI, SQLAlchemy async).
- **Files in scope (v0.2.0 — 14 endpoints)**:
  - `klai-portal/backend/app/api/auth.py` — 14 endpoint handlers, ~22 log-statement migrations, helper extraction.
  - `klai-portal/backend/tests/test_auth_totp_endpoints.py` — NEW (totp_setup, totp_confirm, totp_login).
  - `klai-portal/backend/tests/test_auth_passkey_endpoints.py` — NEW (passkey_setup, passkey_confirm).
  - `klai-portal/backend/tests/test_auth_email_otp_endpoints.py` — NEW (email_otp_setup, email_otp_confirm, email_otp_resend).
  - `klai-portal/backend/tests/test_auth_idp_endpoints.py` — NEW (idp_intent, idp_callback, idp_intent_signup, idp_signup_callback).
  - `klai-portal/backend/tests/test_auth_password_endpoints.py` — NEW (password_reset, password_set, verify_email).
  - `klai-portal/backend/tests/test_auth_sso_endpoints.py` — NEW (sso_complete).
  - `klai-portal/backend/tests/auth_test_helpers.py` — NEW shared module (request-body factories, db-mock factories, respx fixture, `_audit_emit_patches`, `_capture_events` filter).
  - `klai-portal/backend/tests/test_auth_mfa_fail_closed.py` — REFACTOR to import shared helpers.
  - `klai-portal/backend/tests/test_idp_callback_provision.py` — UNCHANGED (existing focus on auto-provision happy path; complemented by new `test_auth_idp_endpoints.py` failure-path scenarios).
- **Out of scope** (explicit non-goals):
  - Cornelis-audit findings #13 (TOTP counter per-instance), #15 (idp cookie binding), #16 (Fernet key regen), A4 (`exc.response.text` log reflection) — tracked by SPEC-SEC-SESSION-001 and SPEC-SEC-INTERNAL-001 respectively.
  - Behavioural changes to any of the 14 endpoints (this SPEC is purely additive: tests + observability + audit log + helper extraction).
  - Frontend changes — failure events are server-side observability only.
  - Grafana alert definitions for the new events — added later if rate justifies.
  - Storage-side PII (email plaintext in `portal_users.email`) — separate column-level encryption SPEC.
- **Identity provider**: Zitadel (auth.getklai.com), service account `portal-api`.
- **Observability**: structlog JSON → Alloy → VictoriaLogs; Grafana for alerts.

---

## Assumptions

- The endpoints' current response semantics are correct (Cornelis-audit
  did not flag them). This SPEC adds tests + observability around the
  existing behaviour — it does NOT change behaviour beyond what the
  observability hardening adds.
- The `_pending_totp` in-memory cache (finding #13) will be replaced by
  Redis under SPEC-SEC-SESSION-001. Tests written here express invariants
  ("after 5 failures the token is invalidated") not implementation
  details ("the in-memory dict has 0 entries"), so they survive that
  migration.
- `respx>=0.22` (added in SPEC-SEC-MFA-001) is available; `structlog.testing.capture_logs` is used as the assertion surface.
- The shared `_emit_auth_event` helper produces events queryable in
  VictoriaLogs as `service:portal-api event:<name>_failed`. Grafana alerts
  for these events are NOT in scope for this SPEC — they will follow in
  a separate observability rollup if the rate is non-trivial.

---

## Out of Scope

- **Audit findings on these endpoints** — see Findings table above. SEC-SESSION-001 and SEC-INTERNAL-001 own those.
- **Behaviour changes** — no status code shifts, no response body shape changes, no new failure modes introduced. This is purely additive (audit logs, structured events, tests, anchors).
- **Frontend changes** — the failure events are server-side observability; the existing frontend handlers for 400/401/502 remain unchanged.
- **Grafana alerts** — defining alerts for the new events is not in scope. They are query-able in VictoriaLogs immediately on deploy. If sustained rates indicate alert is justified, follow-up SPEC.
- **Rate-limiting at the API layer** — TOTP confirm and password set are rate-limited by Zitadel server-side; portal-api adds no per-request rate limit. This SPEC keeps that posture.
- **passkey_*, email_otp_*, verify_email, idp_*_signup endpoints** — deferred (research.md §1).
- **DB-storage PII (email plaintext in `portal_users.email`)** — out of scope; that is a column-level encryption SPEC.

---

## Threat Model

The auth endpoints have already been audited by Cornelis (2026-04-22).
Exploitable findings are tracked in their own SPECs. The threat surface
this SPEC addresses is **observability-blind regressions**:

1. **Silent Zitadel degradation.** A Zitadel 5xx flap during, e.g.,
   `password_set` or `idp_intent` is currently logged with
   `logger.exception` (a non-queryable string). On-call has no way to
   distinguish a genuine "user typed wrong code" 400 from a 5xx
   degradation that took us out. After this SPEC, every 5xx leg emits
   a structured `*_failed` event with `zitadel_status`, queryable
   independently of the human-readable log message.

2. **Brute-force enumeration.** TOTP confirm and password reset endpoints
   are currently rate-limited only at Zitadel. A coordinated brute-force
   from a single IP would show in Caddy access logs but not in any
   business-event log. After this SPEC, `totp_confirm_failed` /
   `password_set_failed` events with `reason=invalid_code` enable rate
   queries in VictoriaLogs without needing Caddy log correlation.

3. **Audit-trail gaps.** Today's audit log only captures `auth.login*`
   actions. A successful TOTP enrolment, a successful password reset,
   or an IDP-callback session completion has no audit row. After this
   SPEC, every state-changing auth action emits `audit.log_event` with
   the correct `actor` (zitadel_user_id when known, sha256(email) when
   anonymous, `"unknown"` when neither).

4. **Test-coverage regression.** `auth.py` is 537 statements; today only
   ~64% are covered. A future refactor of, e.g., `idp_callback` could
   silently break the auto-provision flow without any test failing
   (only the existing `test_idp_callback_provision.py` covers a happy
   path). After this SPEC, ≥85% line coverage with respx-mocked
   regression scenarios protects against silent regressions.

**Explicit non-threats**:

- This SPEC does NOT defend against new attacker-controlled inputs —
  Cornelis-audit confirmed the existing input handling is sound.
- This SPEC does NOT change anti-enumeration behaviour on
  `password_reset` (always returns 204).

---

## Requirements

### REQ-1: TOTP endpoints — audit log + structured events + tests

WHEN a request reaches `totp_setup`, `totp_confirm`, or `totp_login`,
THE handler SHALL emit an `audit.log_event` for state changes and a
structured `*_failed` event for every failure leg, AND new pytest
regression tests SHALL exist that exercise every documented branch.

- **REQ-1.1**: WHEN `totp_setup` succeeds, THE handler SHALL emit
  `audit.log_event(action="auth.totp.setup", actor=user_id, resource_type="user", resource_id=user_id, details={"reason": "initiated"})`.
- **REQ-1.2**: WHEN `totp_setup` fails (5xx), THE handler SHALL emit
  `_emit_auth_event("totp_setup_failed", reason="zitadel_5xx", zitadel_status=<int>, actor_user_id=<id>, outcome="502", level="error")` BEFORE raising the existing 502.
- **REQ-1.3**: WHEN `totp_confirm` succeeds, THE handler SHALL emit
  `audit.log_event(action="auth.totp.confirmed", actor=user_id, …)`.
- **REQ-1.4**: WHEN `totp_confirm` fails 4xx (wrong code), THE handler
  SHALL emit `_emit_auth_event("totp_confirm_failed", reason="invalid_code", zitadel_status=<int>, actor_user_id=<id>, outcome="400", level="warning")` BEFORE raising the existing 400.
- **REQ-1.5**: WHEN `totp_confirm` fails 5xx, THE handler SHALL emit the same event with `reason="zitadel_5xx"`, `outcome="502"`, `level="error"`.
- **REQ-1.6**: WHEN `totp_login` fails 4xx (wrong code) AND `failures<MAX`, THE handler SHALL emit `_emit_auth_event("totp_login_failed", reason="invalid_code", failures=<int>, outcome="400", level="warning")` IN ADDITION TO the existing `audit.log_event(action="auth.totp.failed", …)`.
- **REQ-1.7**: WHEN `totp_login` reaches the lockout threshold, THE handler SHALL emit `_emit_auth_event("totp_login_failed", reason="lockout", failures=<MAX>, outcome="429", level="error")` BEFORE invalidating the temp token.
- **REQ-1.8**: A new test file `tests/test_auth_totp_endpoints.py` SHALL contain at least 13 scenarios covering: setup happy + 5xx; confirm happy + invalid + 5xx; login happy + 1st-fail + 5th-fail-lockout + already-locked + expired-token + finalize-fail.

### REQ-2: IDP endpoints — audit log + structured events + tests

WHEN a request reaches `idp_intent` or `idp_callback`, THE handler SHALL
emit an `audit.log_event` for state changes and a structured event for
every failure leg.

- **REQ-2.1**: WHEN `idp_intent` succeeds, THE handler SHALL emit
  `audit.log_event(action="auth.idp.intent", actor="anonymous", details={"idp_id": <hashed>, "auth_request_id": <id>})`.
  The `idp_id` is a stable Zitadel ID (not PII) so it MAY be logged
  unhashed; preference is to log it as-is so on-call can identify the
  IDP without needing a lookup table.
- **REQ-2.2**: WHEN `idp_intent` fails (unknown IDP, Zitadel 5xx, missing
  authUrl), THE handler SHALL emit
  `_emit_auth_event("idp_intent_failed", reason="unknown_idp"|"zitadel_5xx"|"missing_auth_url", …, outcome="400"|"502", level=<warning|error>)`.
- **REQ-2.3**: WHEN `idp_callback` succeeds (single-org or multi-org
  branch), THE handler SHALL emit
  `audit.log_event(action="auth.login.idp", actor=zitadel_user_id, details={"method": "idp", "auto_provisioned": <bool>})`.
- **REQ-2.4**: WHEN `idp_callback` fails on any leg
  (session_creation_5xx, missing_session, finalize_5xx, get_session_details_failed),
  THE handler SHALL emit
  `_emit_auth_event("idp_callback_failed", reason=<leg>, …, outcome="302→failure_url", level="error")`
  AND continue with the existing 302-to-failure_url behaviour
  (no status code change).
- **REQ-2.5**: A new test file `tests/test_auth_idp_endpoints.py` SHALL
  contain at least 8 scenarios covering: intent happy + unknown_idp + 5xx + missing_authUrl; callback session-creation-5xx + finalize-5xx + missing-session + auto-provision-DB-fail.

### REQ-3: Password endpoints — audit log + structured events + tests

WHEN a request reaches `password_reset` or `password_set`, THE handler
SHALL emit `audit.log_event` for every call (anti-enumeration is
preserved by always returning 204) and a structured event for every
failure leg.

- **REQ-3.1**: WHEN `password_reset` is called (regardless of outcome),
  THE handler SHALL emit
  `audit.log_event(action="auth.password.reset", actor="anonymous", details={"email_hash": <sha256>})`.
- **REQ-3.2**: WHEN `password_reset` cannot find a user (unknown email),
  THE handler SHALL emit
  `_emit_auth_event("password_reset_failed", reason="unknown_email", email_hash=<sha256>, outcome="204", level="warning")`.
  The HTTP response remains 204 (anti-enumeration).
- **REQ-3.3**: WHEN `password_reset` fails on Zitadel 5xx, THE handler
  SHALL emit `_emit_auth_event("password_reset_failed", reason="zitadel_5xx", zitadel_status=<int>, email_hash=<sha256>, outcome="204", level="error")`.
- **REQ-3.4**: WHEN `password_set` succeeds, THE handler SHALL emit
  `audit.log_event(action="auth.password.set", actor=user_id, details={"reason": "set"})`.
- **REQ-3.5**: WHEN `password_set` fails 4xx (expired/invalid link), THE handler
  SHALL emit `_emit_auth_event("password_set_failed", reason="invalid_code"|"expired_link", actor_user_id=<id>, outcome="400", level="warning")`.
- **REQ-3.6**: WHEN `password_set` fails 5xx, THE handler SHALL emit
  `_emit_auth_event("password_set_failed", reason="zitadel_5xx", zitadel_status=<int>, actor_user_id=<id>, outcome="502", level="error")`.
- **REQ-3.7**: A new test file `tests/test_auth_password_endpoints.py` SHALL contain at least 8 scenarios covering: reset happy + unknown_email + find_user_5xx + send_reset_5xx; set happy + expired-link + 5xx + audit log presence.

### REQ-4: SSO endpoint — structured events + tests

WHEN a request reaches `sso_complete`, THE handler SHALL emit a structured
event for every failure leg.

- **REQ-4.1**: WHEN `sso_complete` is missing the `klai_sso` cookie, THE handler SHALL emit `_emit_auth_event("sso_complete_failed", reason="no_cookie", outcome="401", level="warning")` BEFORE raising the existing 401.
- **REQ-4.2**: WHEN the cookie is present but decryption fails, THE handler SHALL emit `_emit_auth_event("sso_complete_failed", reason="cookie_invalid", outcome="401", level="warning")` BEFORE raising the existing 401.
- **REQ-4.3**: WHEN `finalize_auth_request` raises HTTPStatusError on a valid cookie, THE handler SHALL emit `_emit_auth_event("sso_complete_failed", reason="session_expired", zitadel_status=<int>, outcome="401", level="warning")` BEFORE raising the existing 401.
- **REQ-4.4**: `sso_complete` SHALL NOT emit `audit.log_event` for success — the cookie reuse is silent UX (the user did not interact). Failures are observability-only.
- **REQ-4.5**: A new test file `tests/test_auth_sso_endpoints.py` SHALL contain at least 4 scenarios covering: happy path + no cookie + tampered cookie + finalize 5xx.

### REQ-5: Cross-cutting hardening (helpers, logger migration, coverage gate)

The following cross-cutting changes apply across the in-scope endpoints
to honour the SPEC-SEC-MFA-001 pattern set:

- **REQ-5.1**: A new helper `_emit_auth_event(event: str, *, reason: str, outcome: str, level: str = "warning", **fields: Any) -> None` SHALL be added to `auth.py`. It is a generalisation of `_emit_mfa_check_failed` — instead of a fixed event name, it takes one as a parameter. `_emit_mfa_check_failed` SHALL be reimplemented as a thin wrapper that calls `_emit_auth_event("mfa_check_failed", …)`.
- **REQ-5.2**: Email values passed via the `email` keyword SHALL be sha256-hashed inside the helper into an `email_hash` field; raw emails MUST NEVER appear in the emitted event. The helper SHALL accept `email_hash` directly as an alternative for callers who already hashed.
- **REQ-5.3**: Every stdlib `logger.*` call site in ALL 14 in-scope endpoints SHALL be migrated to `_slog.*` per `portal-logging-py.md`. The `# nosemgrep: python-logger-credential-disclosure` annotations on `password_reset` and `password_set` SHALL be REMOVED post-migration (structlog removes the false-positive trigger).
- **REQ-5.4**: `@MX:ANCHOR` tags SHALL be added to `_finalize_and_set_cookie` (fan_in=3), `_validate_callback_url` (fan_in=3), and the new `_emit_auth_event` (projected fan_in≥20 after v0.2.0 scope expansion). Each MUST include `@MX:REASON` and `@MX:SPEC: SPEC-SEC-AUTH-COVERAGE-001` sub-lines.
- **REQ-5.5**: `pytest --cov=app.api.auth --cov-branch --cov-fail-under=85` SHALL pass on this SPEC's branch. Branch coverage SHALL be ≥95%.
- **REQ-5.6**: A shared test-helper module `tests/auth_test_helpers.py` SHALL be created exposing the request-body factories, DB mock factory, respx fixture, audit-emit patches, and event-filter helper. `tests/test_auth_mfa_fail_closed.py` SHALL be refactored to import from this module (no behaviour change, only de-duplication).
- **REQ-5.7**: All new tests SHALL use respx against the real `ZitadelClient` (REQ-5.7 from SPEC-SEC-MFA-001) — no `MagicMock` on `app.api.auth.zitadel`. Existing legacy tests in `test_auth_security.py` keep their patch-based pattern unchanged.

---

## Scope expansion v0.2.0 — additional sub-requirements

The 6 endpoints added in scope at v0.2.0 (see HISTORY) extend REQs 1–3 with
the following sub-requirements. The naming convention, schema, and gate
criteria remain identical to those in the parent REQ.

### REQ-1 extension — Passkey + Email OTP enrollment

- **REQ-1.9** (`passkey_setup`): WHEN `passkey_setup` succeeds, emit
  `audit.log_event(action="auth.passkey.setup", actor=user_id, …)`.
  WHEN it fails 5xx, emit `_emit_auth_event("passkey_setup_failed", reason="zitadel_5xx", actor_user_id=<id>, outcome="502", level="error")`.
- **REQ-1.10** (`passkey_confirm`): WHEN it succeeds, emit
  `audit.log_event(action="auth.passkey.confirmed", actor=user_id, …)`.
  WHEN it fails 4xx (invalid attestation), emit
  `_emit_auth_event("passkey_confirm_failed", reason="invalid_attestation", outcome="400", level="warning")`.
  WHEN 5xx, `reason="zitadel_5xx"`, `outcome="502"`, `level="error"`.
- **REQ-1.11** (`email_otp_setup`): WHEN it succeeds, emit
  `audit.log_event(action="auth.email-otp.setup", actor=user_id, …)`.
  WHEN it fails 5xx, emit `_emit_auth_event("email_otp_setup_failed", reason="zitadel_5xx", outcome="502", level="error")`.
- **REQ-1.12** (`email_otp_confirm`): WHEN it succeeds, emit
  `audit.log_event(action="auth.email-otp.confirmed", actor=user_id, …)`.
  WHEN it fails 4xx (wrong code), emit
  `_emit_auth_event("email_otp_confirm_failed", reason="invalid_code", actor_user_id=<id>, outcome="400", level="warning")`.
  WHEN 5xx, `reason="zitadel_5xx"`, `outcome="502"`, `level="error"`.
- **REQ-1.13** (`email_otp_resend`): WHEN it succeeds, emit
  `audit.log_event(action="auth.email-otp.resent", actor=user_id, …)`.
  WHEN it fails 5xx, emit `_emit_auth_event("email_otp_resend_failed", reason="zitadel_5xx", outcome="502", level="error")`.
- **REQ-1.14**: New test files
  `tests/test_auth_passkey_endpoints.py` (≥4 scenarios: setup happy/5xx, confirm happy/invalid/5xx) and
  `tests/test_auth_email_otp_endpoints.py` (≥6 scenarios: setup happy/5xx, confirm happy/invalid/5xx, resend happy/5xx) SHALL be created.

### REQ-2 extension — IDP signup flows

- **REQ-2.6** (`idp_intent_signup`): WHEN it succeeds, emit
  `audit.log_event(action="auth.idp.intent_signup", actor="anonymous", details={"idp_id": <id>, "auth_request_id": <id>})`.
  WHEN it fails (unknown IDP, Zitadel 5xx), emit
  `_emit_auth_event("idp_intent_signup_failed", reason="unknown_idp"|"zitadel_5xx", …, outcome="400"|"502", level=<warning|error>)`.
- **REQ-2.7** (`idp_signup_callback`): WHEN session creation succeeds AND the new portal_users row is provisioned, emit
  `audit.log_event(action="auth.signup.idp", actor=zitadel_user_id, details={"method": "idp", "auto_provisioned": true})`.
  WHEN any leg fails, emit
  `_emit_auth_event("idp_signup_callback_failed", reason=<leg>, …, outcome="302→failure_url", level="error")`.
- **REQ-2.8**: `tests/test_auth_idp_endpoints.py` (extending REQ-2.5 scope) SHALL contain ≥6 scenarios for signup flows: intent-signup happy + unknown_idp + 5xx; callback-signup happy (auto-provision) + session_5xx + finalize_5xx.

### REQ-3 extension — Email verification

- **REQ-3.8** (`verify_email`): WHEN it succeeds, emit
  `audit.log_event(action="auth.email.verified", actor=user_id, details={"reason": "verified"})`.
  WHEN it fails 4xx (invalid/expired code), emit
  `_emit_auth_event("verify_email_failed", reason="invalid_code"|"expired_link", actor_user_id=<id>, outcome="400", level="warning")`.
  WHEN 5xx, emit `_emit_auth_event("verify_email_failed", reason="zitadel_5xx", zitadel_status=<int>, outcome="502", level="error")`.
- **REQ-3.9**: `tests/test_auth_password_endpoints.py` (extending REQ-3.7 scope) SHALL contain ≥4 scenarios for verify_email: happy + invalid + expired + 5xx.

### REQ-5.5 amendment

The coverage threshold of ≥85% line and ≥95% branch applies to the full
14-endpoint scope. With the additional ~28 scenarios, projected coverage
is ~88-92% line, ~96-98% branch.

---

## Non-Functional Requirements

- **Performance**: The added `_emit_auth_event` and `audit.log_event` calls add ≤2 calls per request × ~1 ms each = ≤2 ms p95 overhead per auth call. Anti-enumeration on `password_reset` keeps the same ≤200 ms total. No new Zitadel round-trips, no new DB queries.
- **Observability**: Every fail-closed outcome SHALL be visible in VictoriaLogs via `service:portal-api AND event:<endpoint>_<action>_failed`. Cross-correlatable with Caddy access logs via `request_id`.
- **Privacy**: `email_hash` (sha256 lowercased) is the only user-identifying field for unauthenticated endpoints; `actor_user_id` (zitadel id, an opaque uuid) is used for authenticated endpoints. Plaintext emails MUST NEVER appear in the emitted events.
- **Backward compatibility**: All response shapes, status codes, and headers remain unchanged. Audit log writes are fire-and-forget (existing pattern); a write failure does NOT block the response.
- **Test discipline**: Existing test files unchanged in semantics. Refactoring of `test_auth_mfa_fail_closed.py` is import-only (no test logic changes). Coverage measured via `pytest-cov`; reported in CHANGELOG.

---

## Success Criteria

- `pytest --cov=app.api.auth --cov-branch` reports ≥85% line coverage and ≥95% branch coverage.
- `tests/test_auth_totp_endpoints.py`, `tests/test_auth_idp_endpoints.py`, `tests/test_auth_password_endpoints.py`, `tests/test_auth_sso_endpoints.py` exist with ≥33 total new scenarios and pass via `pytest`.
- `tests/auth_test_helpers.py` exists; `tests/test_auth_mfa_fail_closed.py` imports from it; both files share a single source of truth for request/db factories.
- `_emit_auth_event` exists and is documented with `@MX:ANCHOR`. `_emit_mfa_check_failed` is now a 1-line wrapper.
- All eight in-scope endpoints emit at least one structured `*_failed` event for every failure leg, verifiable via `capture_logs()` assertions.
- All eight in-scope endpoints emit `audit.log_event` for every state-changing success path (sso_complete excluded per REQ-4.4).
- All stdlib `logger.*` call sites in the eight in-scope endpoints are migrated to `_slog.*`. `grep "logger\." auth.py` returns at most the existing pre-scope `logger = logging.getLogger(__name__)` declaration plus any out-of-scope endpoints' calls.
- `# nosemgrep: python-logger-credential-disclosure` annotations on `password_reset` and `password_set` are removed.
- `@MX:ANCHOR` tags on `_finalize_and_set_cookie`, `_validate_callback_url`, and `_emit_auth_event` are present and pass `verify-alert-runbooks`-equivalent MX validator (P1 unblocked).
- `ruff check app/api/auth.py tests/test_auth_*.py` clean.
- `ruff format --check app/api/auth.py tests/test_auth_*.py` clean.
- `uv run --with pyright pyright app/api/auth.py` reports 0/0/0.
- Full backend `pytest` reports zero regressions vs the pre-SPEC baseline.

---

## Cross-references

- Tracker: [SPEC-SEC-AUDIT-2026-04](../SPEC-SEC-AUDIT-2026-04/spec.md)
- Predecessor: [SPEC-SEC-MFA-001](../SPEC-SEC-MFA-001/spec.md)
- Sibling SPECs (out of scope): [SPEC-SEC-SESSION-001](../SPEC-SEC-SESSION-001/spec.md), [SPEC-SEC-INTERNAL-001](../SPEC-SEC-INTERNAL-001/spec.md)
- Research: [research.md](./research.md)
- Acceptance: [acceptance.md](./acceptance.md)
- Plan: [plan.md](./plan.md)
- Source under change:
  - [klai-portal/backend/app/api/auth.py](../../../klai-portal/backend/app/api/auth.py)
  - [klai-portal/backend/tests/test_auth_mfa_fail_closed.py](../../../klai-portal/backend/tests/test_auth_mfa_fail_closed.py) (refactor only)
- Logging rules: [portal-logging-py.md](../../../.claude/rules/klai/projects/portal-logging-py.md)
- MX tag protocol: [mx-tag-protocol.md](../../../.claude/rules/moai/workflow/mx-tag-protocol.md)
