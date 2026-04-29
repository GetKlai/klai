---
id: SPEC-SEC-SESSION-001
version: 0.3.0
status: done
created: 2026-04-24
updated: 2026-04-29
author: Mark Vletter
priority: medium
tracker: SPEC-SEC-AUDIT-2026-04
---

# SPEC-SEC-SESSION-001: Session and Cookie Robustness

## HISTORY

### v0.3.0 (2026-04-29) — shipped
- Implementation merged via PR #197 (squash `298195aa`) and deployed to
  core-01 the same day. Container `klai-core-portal-api-1` rolled over
  cleanly: lifespan startup emitted no `sso_cookie_key_missing_startup_abort`
  event, public health endpoint returned 200, zero error events on
  `service:portal-api` in the 20-minute post-deploy scan.
- All six REQs landed:
  - REQ-1: in-memory `_pending_totp = TTLCache(...)` replaced with Redis
    keys `totp_pending:<token>` (HASH) + `totp_pending_failures:<token>`
    (STRING via atomic `INCR`). 5-failure ceiling now cross-replica
    consistent. Fail-CLOSED on Redis unavailability (HTTP 503).
  - REQ-2: `klai_idp_pending` Fernet payload extended with `ua_hash` +
    `ip_subnet` (`/24` IPv4 / `/48` IPv6). `_verify_idp_pending_binding`
    rejects mismatch with HTTP 403 + structlog event; cookie preserved
    for legitimate retry within TTL.
  - REQ-3: `_fernet = Fernet(... else generate_key())` replaced with
    `@lru_cache _get_sso_fernet()` that raises `RuntimeError` on empty
    key. Three callsites migrated.
  - REQ-4: lifespan startup-guard runs BEFORE the dev/prod branch in
    `app.main`, so `is_auth_dev_mode` no longer bypasses the SSO key
    check. Empty key emits structlog `critical` event then re-raises.
  - REQ-5: four structured events live in VictoriaLogs:
    `totp_pending_lockout`, `totp_pending_redis_unavailable`,
    `idp_pending_binding_mismatch`, `sso_cookie_key_missing_startup_abort`.
    All carry prefix-only PII (8-hex hash prefixes, `/24`/`/48` subnets,
    8-char token prefixes) — no raw UA, IP, or session credentials.
  - REQ-6: 22 new tests across six files (acceptance scenarios 1-8). Two
    new Grafana alerts on the SESSION-specific events (this close-out PR).
- Caller-IP / subnet helper extracted to `app/services/request_ip.py` once
  the third callsite (auth IDP-pending + signup binding-check) joined the
  existing `app/api/internal.py` consumer; `internal.py` aliases the
  legacy `_resolve_caller_ip` name to keep the test patch surface intact.
- Known limitation (filed for follow-up, not blocking): `_totp_pending_create`
  uses sequential `HSET` + `EXPIRE` rather than a Redis pipeline, so a
  portal-api crash in the microsecond window between the two calls would
  leak one orphan hash without TTL. Real-world impact: <1 hash per crash;
  `maxmemory` policy on Redis caps long-term growth.

### v0.2.0 (2026-04-24)
- Expanded from stub via `/moai plan SPEC-SEC-SESSION-001`
- Added EARS requirements REQ-1..REQ-6 covering Redis-backed TOTP counter,
  atomic increments, cross-replica lockout, cookie binding via UA-hash +
  IP-subnet, fail-closed `_fernet` initialisation, and startup guard for
  empty `SSO_COOKIE_KEY`
- Added threat model, environment, acceptance references, and cross-links

### v0.1.0 (2026-04-24)
- Stub created from SPEC-SEC-AUDIT-2026-04 (Cornelis audit 2026-04-22)
- Priority P2 — some findings become critical only when portal scales
  horizontally

---

## Findings addressed

| # | Finding | Severity | File reference |
|---|---|---|---|
| 13 | `_pending_totp = TTLCache(300)` in process memory — multi-replica = 5×N attempts | MEDIUM | [auth.py:73-97, 128-130, 441-525](../../../klai-portal/backend/app/api/auth.py#L73) |
| 15 | `klai_idp_pending` Fernet cookie has no browser / IP binding | MEDIUM | [auth.py:1070-1095](../../../klai-portal/backend/app/api/auth.py#L1070), [signup.py:243-391](../../../klai-portal/backend/app/api/signup.py#L243) |
| 16 | `klai_sso` cookie key regenerated on empty `SSO_COOKIE_KEY` env var | MEDIUM | [auth.py:106](../../../klai-portal/backend/app/api/auth.py#L106) |

---

## Goal

Make session- and cookie-related state resilient to horizontal scaling,
cookie exfiltration, and configuration drift. Specifically:

1. Externalise `_pending_totp` state from in-process memory to Redis so
   the 5-failure TOTP lockout is enforced across all portal-api replicas
   with atomic increments.
2. Bind the short-lived `klai_idp_pending` Fernet cookie to a hash of the
   issuing browser (user-agent) and client IP subnet, so a stolen cookie
   replayed from a different origin context is rejected.
3. Refactor `_fernet` initialisation at module import time in `auth.py`
   to the same fail-closed pattern that `signup.py::_get_fernet` already
   implements: an empty `SSO_COOKIE_KEY` MUST refuse to start the
   service, never silently fall through to a process-local
   `Fernet.generate_key()` which invalidates every previously issued
   cookie on each restart and produces divergent keys across replicas.

This SPEC does NOT migrate the session store or replace TOTP. It closes
three specific audit findings.

---

## Success Criteria

- `_pending_totp` state lives in Redis; failure counter is incremented
  atomically via `INCR`; the 5-failure lockout triggers at the 5th
  failed attempt across any mix of replicas.
- `klai_idp_pending` Fernet payload includes `ua_hash` and `ip_subnet`
  fields; consumption verifies both and returns HTTP 403 when either
  fails.
- `_fernet` (module-level in `auth.py`) is wrapped in a function that
  raises at startup (not at first request) when `settings.sso_cookie_key`
  is empty. The fallback `Fernet.generate_key()` branch is removed.
- The service refuses to boot (process exits non-zero) when
  `SSO_COOKIE_KEY=""` or `SSO_COOKIE_KEY` is unset.
- Cookie binding uses a `/24` IPv4 subnet (or `/48` IPv6 subnet) mask so
  mobile users switching between cells inside the same carrier prefix
  still validate.
- All acceptance tests in `acceptance.md` pass using `fakeredis` for the
  Redis-backed paths.
- No regression in the happy-path login flow (password → TOTP → SSO
  cookie) or in the happy-path social signup flow (IDP intent → pending
  cookie → `/api/signup/social`).

---

## Environment

- **Service:** `klai-portal/backend`
- **Python:** 3.13
- **Files in scope:**
  - [klai-portal/backend/app/api/auth.py](../../../klai-portal/backend/app/api/auth.py) —
    `TTLCache` class (lines 72-97), `_pending_totp` module global
    (line 130), `_fernet` module global (line 106), TOTP consumer
    (`totp_login`, lines 459-533), IDP pending cookie writer (lines
    1070-1095)
  - [klai-portal/backend/app/api/signup.py](../../../klai-portal/backend/app/api/signup.py) —
    `_get_fernet` reference pattern (lines 233-240), IDP pending cookie
    reader (lines 243-391)
- **Infra coupling:** Redis via `get_redis_pool()`; sliding-window
  reference implementation in
  [partner_rate_limit.py](../../../klai-portal/backend/app/services/partner_rate_limit.py)
  (`check_rate_limit`, 21-61)
- **Config:** `settings.sso_cookie_key` (pydantic-settings); new field
  `settings.totp_pending_ttl_seconds` (default 300)
- **Observability:** structlog JSON; new stable event keys
  `totp_pending_lockout`, `totp_pending_binding_mismatch`,
  `idp_pending_binding_mismatch`, `sso_cookie_key_missing_startup_abort`

## Assumptions

- Redis is already available to portal-api and used for session state +
  rate limiting (partner API, templates, internal endpoints). This SPEC
  adds one more Redis namespace (`totp_pending:<token>`), it does not
  introduce Redis as a new dependency.
- Portal-api will scale horizontally before the audit's next rotation
  (otherwise Finding #13 is low-priority but the other two findings
  still apply on a single replica).
- Binding cookies to `/24` (IPv4) or `/48` (IPv6) subnets preserves the
  mobile user experience when switching between cells on the same
  carrier. Users switching from home Wi-Fi to 4G will be prompted to
  reauthenticate; that is an accepted trade-off.
- The existing Fernet `ttl=_IDP_PENDING_MAX_AGE` TTL continues to enforce
  the 10-minute pending-cookie window; the binding is additive.
- Redis unavailability during TOTP verification is rare. When Redis is
  unavailable, the system fails CLOSED (TOTP verification rejected),
  matching the MFA fail-closed direction of SPEC-SEC-MFA-001. This
  differs from the partner rate-limiter which fails open — the two have
  different threat models.

---

## Out of Scope

- Full migration of session store to a different backend (Redis →
  PostgreSQL or similar). If Redis becomes a SPOF concern, file a
  separate SPEC.
- WebAuthn / passkey replacement of TOTP — strategic SPEC, not a fix.
- Rotating `SSO_COOKIE_KEY`. Rotation invalidates every outstanding SSO
  cookie; the procedure belongs in `klai-infra` runbooks alongside
  `INTERNAL_SECRET_ROTATION.md`.
- Binding the long-lived `klai_sso` cookie to UA/IP. Long-lived sessions
  tolerate network switches by design; binding would degrade UX
  without meaningful gain because Zitadel is the authority for session
  validity (see comment at auth.py:101-104).
- Rate-limiting the TOTP endpoint by caller IP (that is inside
  SPEC-SEC-HYGIENE-001 scope if prioritised). This SPEC only closes the
  per-token counter gap.

---

## Threat Model

Three adversary scenarios drive the requirements:

1. **Horizontal-scale TOTP brute-force.** Two portal-api replicas
   behind a round-robin proxy each hold their own `TTLCache` copy. An
   attacker who stole a username+password fans TOTP guesses across
   replicas: 4 wrong guesses per replica → 8 attempts before either
   replica's in-memory counter reaches the 5-failure lockout. With more
   replicas the effective ceiling scales linearly. A Redis-backed
   counter incremented atomically with `INCR` makes the ceiling
   cross-replica consistent.

2. **IDP-pending cookie theft.** The `klai_idp_pending` Fernet cookie
   carries the attacker's target session token (see auth.py:1070-1095).
   With `samesite=lax` + `secure` + `httponly` the cookie is not
   trivially exfiltratable, but a malicious browser extension, a
   same-site XSS on any `*.getklai.com` subdomain, or a misconfigured
   shared device can leak it within the 10-minute window. Replayed on
   a different UA or from a non-adjacent IP, the cookie currently
   completes the signup and mints a full SSO cookie on the attacker's
   browser. Binding to `ua_hash` + `/24` (or `/48`) subnet makes the
   stolen cookie useless without also hijacking the original browser.

3. **Empty-key fall-through on restart.** `auth.py:106` currently falls
   back to `Fernet.generate_key()` when `settings.sso_cookie_key` is
   empty. Each replica generates its OWN key, independently, on each
   restart. A deployment that ships with an unset or empty
   `SSO_COOKIE_KEY` silently: (a) produces cookies decryptable only on
   the replica that issued them; (b) rotates the key on every restart,
   invalidating every outstanding cookie without operator awareness;
   (c) hides the misconfiguration behind what looks like a working
   login flow on a single-replica dev box. `signup.py::_get_fernet`
   already refuses the empty-key case with a 503; the same check must
   run at startup in auth.py so the deployment FAILS VISIBLY rather
   than silently diverges.

Explicit non-goals:

- Defeating an attacker who has hijacked the user's actual browser AND
  network. Cookie-binding is a replay defence, not a session-integrity
  defence.
- Defeating an attacker with direct Redis write access. That requires
  separate infra controls (Redis ACLs, network segmentation, SPEC-INFRA
  scope).

---

## Requirements

### REQ-1: Redis-Backed TOTP Pending State

The system SHALL store TOTP pending-login state in Redis, not in
per-process memory, so that the 5-failure lockout is enforced
consistently across all portal-api replicas.

- **REQ-1.1:** WHEN a user passes password authentication AND has TOTP
  registered, THE service SHALL store the pending-login state
  (`session_id`, `session_token`, `failures=0`, `ua_hash`, `ip_subnet`)
  under a Redis key `totp_pending:<token>` with TTL
  `settings.totp_pending_ttl_seconds` (default 300 seconds).
- **REQ-1.2:** The opaque `<token>` returned to the client SHALL be a
  cryptographically random URL-safe string of at least 256 bits of
  entropy (e.g. `secrets.token_urlsafe(32)`), matching the existing
  `TTLCache.put` contract.
- **REQ-1.3:** WHEN `/api/auth/totp-login` is called with a token, THE
  service SHALL look up the state by `totp_pending:<token>` in Redis.
  IF the key is absent or expired, THE service SHALL respond with
  HTTP 400 and detail `"Session expired, please log in again"`,
  preserving the existing client contract.
- **REQ-1.4:** WHEN the TOTP code is rejected by Zitadel (HTTP 400 or
  401 from `update_session_with_totp`), THE service SHALL atomically
  increment the `failures` counter in Redis using a single `INCR` on
  a companion key `totp_pending_failures:<token>`. The result of
  `INCR` SHALL be the authoritative failure count used for the
  lockout decision.
- **REQ-1.5:** WHEN the atomic `INCR` result is greater than or equal
  to 5, THE service SHALL delete both `totp_pending:<token>` and
  `totp_pending_failures:<token>` AND respond with HTTP 429 and detail
  `"Too many failed attempts, please log in again"`.
- **REQ-1.6:** WHEN the TOTP code is accepted, THE service SHALL
  delete both `totp_pending:<token>` and `totp_pending_failures:<token>`
  before returning the SSO cookie.
- **REQ-1.7:** WHEN Redis is unreachable during a TOTP verification,
  THE service SHALL fail CLOSED: return HTTP 503 with detail
  `"Authentication unavailable, please retry"` AND emit a structlog
  event `totp_pending_redis_unavailable` at level `error`. Failing
  open here is unacceptable because a Redis outage would otherwise
  lift the brute-force ceiling entirely.
- **REQ-1.8:** The in-memory `TTLCache` class SHALL remain available
  as a general utility but SHALL NOT be used for TOTP pending state.
  The module-level `_pending_totp` global SHALL be removed.

### REQ-2: `klai_idp_pending` Cookie Binding

The system SHALL bind the short-lived IDP-pending Fernet cookie to a
hash of the issuing user-agent and the client IP subnet, so that a
stolen cookie replayed from a different origin context is rejected.

- **REQ-2.1:** WHEN `idp_signup_callback` issues the `klai_idp_pending`
  cookie (auth.py:1070-1095), THE service SHALL include in the
  Fernet-encrypted payload two additional fields: `ua_hash` (SHA-256
  hex of the `User-Agent` header, or `""` if absent) and `ip_subnet`
  (the `/24` prefix for IPv4 or `/48` prefix for IPv6 derived from
  the resolved caller IP).
- **REQ-2.2:** WHEN `/api/signup/social` decrypts the
  `klai_idp_pending` cookie (signup.py:264), THE service SHALL compute
  the `ua_hash` and `ip_subnet` for the current request using the same
  algorithm AND SHALL compare them to the decrypted payload values.
  IF either value does not match, THE service SHALL respond with HTTP
  403 and detail `"Signup session binding mismatch, please start over"`
  AND SHALL emit a structlog event `idp_pending_binding_mismatch` at
  level `warning` including `stored_ua_hash_prefix`,
  `current_ua_hash_prefix`, `stored_ip_subnet`, `current_ip_subnet`
  (no raw UA strings, no raw IPs, to avoid PII leakage into logs).
- **REQ-2.3:** The caller IP SHALL be resolved with the same priority
  order used elsewhere in portal-api: the right-most entry of
  `X-Forwarded-For` from Caddy when present, else `request.client.host`.
  Subnet derivation SHALL use the `ipaddress` stdlib module
  (`ipaddress.ip_network(f"{ip}/24", strict=False).network_address`
  for IPv4; `/48` for IPv6).
- **REQ-2.4:** WHERE the `User-Agent` header is absent on either the
  issue or consume request, THE `ua_hash` comparison SHALL still run
  with `""` as the hashed input. This preserves the binding check
  for API-client edge cases without crashing on None headers.
- **REQ-2.5:** The binding check SHALL run AFTER the existing Fernet
  TTL decrypt (`ttl=_IDP_PENDING_MAX_AGE`) succeeds. A binding
  mismatch SHALL NOT expose any information about whether the cookie
  was otherwise valid beyond the 403 status.

### REQ-3: Fail-Closed `_fernet` Initialisation

The system SHALL refuse to initialise the SSO Fernet cipher when
`SSO_COOKIE_KEY` is empty or unset, matching the pattern already used
by `signup.py::_get_fernet`.

- **REQ-3.1:** The module-level `_fernet = Fernet(... generate_key())`
  expression at `auth.py:106` SHALL be replaced with a
  `_get_sso_fernet()` function that raises `RuntimeError` with a
  descriptive message when `settings.sso_cookie_key` is empty. All
  call-sites (`_encrypt_sso`, `_decrypt_sso`, and the IDP-pending
  cookie encrypt site in `idp_signup_callback`) SHALL call
  `_get_sso_fernet()` instead of the module global.
- **REQ-3.2:** The `Fernet.generate_key()` fallback branch SHALL be
  removed. There is no case in which the process should issue cookies
  signed with an ephemeral key.
- **REQ-3.3:** The replacement function SHALL cache the constructed
  `Fernet` instance on first successful call (module-level
  `functools.lru_cache(maxsize=1)` or a simple module global set at
  first call) so repeated use does not re-construct the cipher per
  request.

### REQ-4: Startup Guard for Missing `SSO_COOKIE_KEY`

The system SHALL fail to start (process exits non-zero) when
`SSO_COOKIE_KEY` is empty or unset, so that a misconfigured deployment
is caught at deploy time, not after users hit the login flow.

- **REQ-4.1:** WHEN the portal-api process starts, THE service SHALL
  validate `settings.sso_cookie_key` during the FastAPI lifespan
  startup phase. IF the value is empty, THE service SHALL log a
  structured error `sso_cookie_key_missing_startup_abort` AND raise
  a fatal exception that aborts startup.
- **REQ-4.2:** The validation SHALL call `_get_sso_fernet()` once
  during startup so that the same code path is exercised that will
  later handle cookie issue/verify. A construction failure at startup
  SHALL also abort startup.
- **REQ-4.3:** The startup error message SHALL explicitly name the
  env var (`SSO_COOKIE_KEY`) and point at the SOPS-encrypted
  `klai-infra/.env` source so the operator knows exactly which secret
  file to fix.
- **REQ-4.4:** In the `is_auth_dev_mode` branch (see auth.py:166-167)
  an empty key SHALL still abort startup. Dev mode relaxes WHICH user
  is returned, not WHETHER cookies can be signed. There is no
  legitimate dev scenario where the SSO cookie key is empty.

### REQ-5: Observability

The system SHALL emit structured log events for every material
security decision introduced by this SPEC.

- **REQ-5.1:** WHEN a TOTP lockout triggers (REQ-1.5), THE service
  SHALL emit a structlog event `totp_pending_lockout` at level
  `warning` including `failures` count and `token_prefix` (first 8
  chars of the token, never the whole token). No session_id or
  session_token SHALL be logged.
- **REQ-5.2:** WHEN an IDP-pending binding mismatch triggers
  (REQ-2.2), THE service SHALL emit
  `idp_pending_binding_mismatch` at level `warning` with the prefix
  and subnet fields described in REQ-2.2.
- **REQ-5.3:** WHEN Redis is unavailable during TOTP verification
  (REQ-1.7), THE service SHALL emit
  `totp_pending_redis_unavailable` at level `error`.
- **REQ-5.4:** WHEN the startup guard aborts (REQ-4.1), THE service
  SHALL emit `sso_cookie_key_missing_startup_abort` at level
  `critical` and include the env var name. The error SHALL surface
  in the container stdout BEFORE the process exits so Alloy captures
  it in VictoriaLogs.
- **REQ-5.5:** All events SHALL follow `portal-logging-py.md`:
  `structlog.get_logger()`, kwargs (never f-strings for structured
  fields), and `exc_info=True` where a traceback is relevant.

### REQ-6: Regression Coverage

The system SHALL include automated tests that would have caught the
three audit findings before they were introduced.

- **REQ-6.1:** A test SHALL simulate a two-replica TOTP brute-force
  by invoking the TOTP consumer path twice against a single
  `fakeredis` Redis, from two separate router instances. After 3
  failures on instance A and 3 failures on instance B, the 5th
  attempt (total across replicas) SHALL return HTTP 429, not HTTP
  400. This guards against regression of REQ-1.4 / REQ-1.5.
- **REQ-6.2:** A test SHALL issue an `klai_idp_pending` cookie with
  one User-Agent and one caller IP, then replay the cookie with a
  different User-Agent. The response SHALL be HTTP 403. A second
  test SHALL replay with the same UA but a `/24`-different IP and
  SHALL also return HTTP 403.
- **REQ-6.3:** A test SHALL replay the cookie with the same UA and
  an IP inside the same `/24` subnet (e.g. `1.2.3.10` → `1.2.3.200`)
  and SHALL succeed (modulo the rest of the social signup flow).
  This guards the mobile-carrier UX assumption.
- **REQ-6.4:** A test SHALL start the FastAPI app with
  `SSO_COOKIE_KEY=""` and assert startup aborts with a
  `RuntimeError`. A companion test SHALL assert startup succeeds
  with a valid 32-byte urlsafe-base64 key.
- **REQ-6.5:** The happy-path regression SHALL cover: password login
  → TOTP success → SSO cookie set; and IDP intent → valid binding
  consume → `/api/signup/social` success. Both SHALL pass after the
  changes land.

---

## Non-Functional Requirements

- **Performance:** The Redis `SET`/`INCR`/`DEL` round-trips added to
  the TOTP path SHALL add no more than 10 ms p95 to the totp-login
  endpoint. The IP/UA hashing added to the IDP-pending path is
  in-process and SHALL add <1 ms.
- **Backward compatibility:** Client contracts (request bodies,
  response fields, cookie names, cookie max-age) SHALL NOT change.
  Only the server-side storage substrate and the cookie payload
  shape change.
- **Migration:** In-flight pending-TOTP sessions from before the
  deploy SHALL be invalidated on rollout (users see "Session
  expired" and re-enter password). This is acceptable for a 5-minute
  TTL. No data migration is required.
- **Fail modes:**
  - Redis unavailable during TOTP: fail CLOSED (REQ-1.7) — aligns with
    SPEC-SEC-MFA-001 direction.
  - Redis unavailable during IDP-pending consume: unaffected (cookie
    binding is local to request).
  - Empty `SSO_COOKIE_KEY`: startup abort (REQ-4.1) — no degraded
    mode.

---

## Cross-references

- Tracker: [SPEC-SEC-AUDIT-2026-04](../SPEC-SEC-AUDIT-2026-04/spec.md)
- Related: [SPEC-SEC-MFA-001](../SPEC-SEC-MFA-001/spec.md) — the MFA
  fail-closed direction (findings #11, #12) motivates REQ-1.7's
  fail-closed choice on Redis unavailability.
- Related: [SPEC-SEC-INTERNAL-001](../SPEC-SEC-INTERNAL-001/spec.md)
  — uses the same Redis sliding-window pattern this SPEC borrows.
- Reference implementation: signup.py::`_get_fernet` pattern
  (signup.py:233-240)
- Reference implementation: `partner_rate_limit.check_rate_limit`
  (partner_rate_limit.py:21-61)
- Logging: `.claude/rules/klai/projects/portal-logging-py.md`
- Research: [research.md](./research.md)
- Acceptance: [acceptance.md](./acceptance.md)
