# SPEC-SEC-MFA-001 Research

Deep codebase analysis backing spec.md. Captures the current login flow
end-to-end, the `mfa_policy` resolution path, the Zitadel availability
assumption, and every fail-open path that currently exists in the MFA
enforcement block.

---

## 1. Current login flow end-to-end

Entry point: `POST /api/auth/login` handled by `login()` in
`klai-portal/backend/app/api/auth.py:359-456`.

Execution order as of commit on branch `feat/restore-knowledge-upload`:

### Step 1 — TOTP-detection probe (lines 362-370)

```
has_totp = False
zitadel_user_id: str | None = None
try:
    user_info = await zitadel.find_user_by_email(body.email)
    if user_info:
        zitadel_user_id, org_id = user_info
        has_totp = await zitadel.has_totp(zitadel_user_id, org_id)
except httpx.HTTPStatusError as exc:
    logger.warning("TOTP check failed %s — continuing without 2FA check",
                   exc.response.status_code)
```

Two Zitadel round-trips inside one try block:
- `find_user_by_email` — `POST /v2/users` with a `loginNameQuery` body
  (`zitadel.py:362-373`)
- `has_totp` — `GET /v2/users/{id}/authentication_methods`
  (`zitadel.py:375-380`)

**Fail-open hole #1 (finding #12):** any `HTTPStatusError` on
`find_user_by_email` is logged at `warning` and swallowed.
`zitadel_user_id` stays `None`. Execution continues to Step 2 with a
`None` user_id — the `if zitadel_user_id:` guard at line 398 then
skips the ENTIRE MFA enforcement block.

**Fail-open hole #2:** the same `except` branch also catches
`has_totp` failures. `has_totp` is semantically different — it only
decides whether the UI prompts for TOTP. A failure there does not need
to fail closed. But merging the two calls into one try block means a
`find_user_by_email` failure is indistinguishable from a `has_totp`
failure in the current code, which is why finding #12 exists.

### Step 2 — password check (lines 373-393)

```
session = await zitadel.create_session_with_password(body.email, body.password)
```

`HTTPStatusError` with 400/401/404/412 → 401 "Email or password
incorrect" (plus audit log of the failure).

Any other status (500, 502, 503, connection error converted to
HTTPStatusError) → 502 "Login failed, please try again later".

**No MFA concerns here** — failing this step returns 401/502 before
MFA is considered.

### Step 3 — MFA enforcement (lines 395-422)

```
portal_user_for_mfa: PortalUser | None = None
mfa_policy = "optional"
if zitadel_user_id:
    try:
        portal_user_for_mfa = await db.scalar(
            select(PortalUser).where(PortalUser.zitadel_user_id == zitadel_user_id)
        )
        if portal_user_for_mfa:
            org_for_mfa = await db.get(PortalOrg, portal_user_for_mfa.org_id)
            mfa_policy = org_for_mfa.mfa_policy if org_for_mfa else "optional"
    except Exception:
        logger.warning("MFA policy lookup failed -- defaulting to optional (fail-open)",
                       exc_info=True)

    if mfa_policy == "required":
        try:
            user_has_mfa = await zitadel.has_any_mfa(zitadel_user_id)
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "has_any_mfa check failed %s -- defaulting to pass (fail-open)",
                exc.response.status_code,
            )
            user_has_mfa = True           # <-- finding #11
        if not user_has_mfa:
            raise HTTPException(403, "MFA required by your organization. ...")
```

**Three fail-open holes in this block:**

- **Hole #3 (finding #11):** `has_any_mfa` HTTPStatusError → `user_has_mfa = True`.
  The attacker's account is treated as having MFA and the 403 is
  skipped.
- **Hole #4:** `has_any_mfa` `RequestError` (connection refused, DNS
  failure, timeout) is NOT caught at all. It propagates up, becomes a
  500, which is the correct outcome — but that is accidental, not
  designed. A future refactor that broadens the except to
  `except Exception` would silently re-introduce finding #11.
- **Hole #5:** The outer `except Exception` on the DB lookup
  unconditionally sets `mfa_policy = "optional"`. If the portal-user
  row exists but the `PortalOrg` fetch fails (RLS GUC leak, FK cache
  miss), we downgrade to `optional` regardless of whether the org
  actually requires MFA.

### Step 4 — event / audit / finalize (lines 424-456)

Login event emission, audit log write (fire-and-forget), and either:
- `totp_required` response with a temp token (if `has_totp=True`), or
- `_finalize_and_set_cookie` to mint the session cookie.

No MFA-relevant failure paths remain in Step 4.

---

## 2. MFA policy resolution

### 2.1 Column and default

`PortalOrg.mfa_policy` is defined at `klai-portal/backend/app/models/portal.py:62`:

```
mfa_policy: Mapped[Literal["optional", "recommended", "required"]] = mapped_column(
    String(16), nullable=False, server_default="optional"
)
```

Migration: `alembic/versions/k1l2m3n4o5p6_add_mfa_policy_to_portal_orgs.py`
(adds the column with `server_default="optional"`).

### 2.2 Resolution path at login time

1. `PortalUser` row is looked up by `zitadel_user_id` against
   `portal_users` (category-A RLS table — permissive on missing GUC,
   see `portal-security.md`). This is safe to query before
   `set_tenant` fires because the RLS policy has the `OR
   current_setting IS NULL` branch.
2. If the row exists, `db.get(PortalOrg, portal_user.org_id)` fetches
   the org. `portal_orgs` is NOT listed in category-D tables; it has
   a permissive SELECT policy by convention (PortalOrg is effectively
   a cross-org lookup table from portal-api's perspective).
3. `mfa_policy` is read straight off the fetched `PortalOrg` instance.
   Missing org → default `"optional"`.

### 2.3 Admin-controlled

The admin endpoint `PATCH /api/admin/settings/org` at
`klai-portal/backend/app/api/admin/settings.py:73-74` is the only
mutation path:

```
if body.mfa_policy is not None:
    org.mfa_policy = body.mfa_policy
```

Accepted values: `"optional" | "recommended" | "required"` (pydantic
`Literal`). No default is changed here.

### 2.4 Per-user vs per-org

There is NO per-user override. MFA policy is strictly per-org. A user
whose personal account is in an org with `mfa_policy="required"` must
have MFA configured; a user in an org with `optional` has no
enforcement. The spec preserves this model.

### 2.5 "recommended" semantics

`recommended` is currently treated identically to `optional` at login
time — the check at `auth.py:409` is `if mfa_policy == "required":`,
so `recommended` falls through. This is a UI-surfaced hint only. SPEC
REQ-3.4 preserves this.

---

## 3. Zitadel availability SLA assumptions

### 3.1 Deployment

- Zitadel runs on `core-01` as a Docker service, reached via Caddy at
  `auth.getklai.com`.
- Service account `portal-api` (SA ID `362780577813757958`, PAT
  `PORTAL_API_ZITADEL_PAT`) calls the v2 / management APIs directly.
- Zitadel version v4.12+ (per `zitadel.md`).

### 3.2 Observed failure modes (from VictoriaLogs and operational notes)

- **Restart flap window:** every Zitadel restart (rolling deploy or
  container restart) produces a 5-60 second window where `/v2/users`
  returns 503 or connection-refused. Observed in `service:portal-api
  AND level:error AND "find_user_id_by_email failed"` queries.
- **PAT invalidation:** documented symptom
  `Errors.Token.Invalid (AUTH-7fs1e)` — turns every call into 401.
  This is NOT a 5xx condition but would be handled by REQ-1/2 in the
  current codebase because the 401 catch at line 377 pre-exists.
  (Not in scope here — PAT rotation is `zitadel.md` #Rotation.)
- **Login V2 misconfig:** documented CRIT in `zitadel.md` — turns
  Zitadel-authored redirects into bad URLs but does not cause 5xx on
  the management / v2 APIs.

### 3.3 SLA assumption for this SPEC

Zitadel availability is >= 99.5% measured against auth.getklai.com.
Expected 5xx rate under steady state: < 0.1/minute. A transient 503
in response to a Zitadel flap is preferable to:
- An extended outage (not affected — login just returns 503 until
  Zitadel recovers), OR
- A silent MFA bypass (the current behaviour on finding #11).

Users can retry within seconds; an attacker cannot force a bypass by
DoSing Zitadel because the `Retry-After` path loops forever at 503.

### 3.4 Client-side timeouts

`zitadel.py` uses a module-level `httpx.AsyncClient(_http)` with the
default 5-second timeout (confirmed via `grep "Timeout\|timeout=" on
zitadel.py`). A Zitadel hang past 5 seconds converts to a `ReadTimeout`
which is a subclass of `httpx.RequestError` — caught by REQ-1.2 and
REQ-2.2.

---

## 4. Fail-open paths in the current code

Catalogue of every fail-open path in the login flow's MFA enforcement
block, in the order execution would hit them:

| # | Location | Condition | Current behaviour | After SPEC |
|---|---|---|---|---|
| FO-1 | `auth.py:369-370` | `find_user_by_email` raises any HTTPStatusError | Warning log, continue with `zitadel_user_id = None` → MFA block skipped | 5xx → 503 (REQ-2.1). 4xx → continue (REQ-2.3). |
| FO-2 | `auth.py:369-370` | `has_totp` raises HTTPStatusError | Warning log, `has_totp = False` | Unchanged. `has_totp` is UI-only; failure → user goes through password-only login screen. REQ-2.6 moves it out of the pre-auth try. |
| FO-3 | `auth.py:369-370` | `find_user_by_email` raises `RequestError` (connection) | NOT CAUGHT — propagates to 500. | Caught at the same site; 5xx → 503 (REQ-2.2). |
| FO-4 | `auth.py:406-407` | DB `select(PortalUser)` raises | Warning log, `mfa_policy` stays `"optional"` | Split: if portal_user not found → fail-open. If portal_user found but org fetch raises → 503 (REQ-3.2 / REQ-5.4). |
| FO-5 | `auth.py:412-417` | `has_any_mfa` raises `HTTPStatusError` | Warning log, `user_has_mfa = True`, login proceeds | 503 under `required`; fail-open under `optional` (REQ-1.1, REQ-3.1). |
| FO-6 | `auth.py:412` | `has_any_mfa` raises `RequestError` | NOT CAUGHT — propagates to 500 (accidentally correct). | Caught; 503 under `required` (REQ-1.2). |
| FO-7 | `auth.py:412` | `has_any_mfa` raises any other `Exception` | NOT CAUGHT — propagates to 500. | Caught via fallback; 503 under `required` (REQ-1.6). |

After the SPEC lands, the only fail-open paths remaining are FO-1
(4xx only — genuine "not found"), FO-2 (has_totp — UI-only flag), FO-4
(portal_user row missing — cannot determine policy), and
FO-5/FO-6/FO-7 under `mfa_policy="optional"` (documented trade-off).

---

## 5. Test-surface analysis

### 5.1 Existing tests (to rewrite)

`klai-portal/backend/tests/test_auth_security.py::TestMFAPolicyEnforcement`
contains five tests:

| Test | Current assertion | SPEC disposition |
|---|---|---|
| `test_mfa_required_no_mfa_enrolled_returns_403` | 403 | Keep (regression for happy-sad path — policy=required + no MFA) |
| `test_mfa_required_with_mfa_enrolled_proceeds` | `totp_required` | Keep (regression for happy path) |
| `test_mfa_optional_no_enforcement` | no-403 + `has_any_mfa` not called | Keep (REQ-3.5) |
| `test_mfa_policy_lookup_failure_defaults_to_optional` | no-raise (fail-open) | Narrow (REQ-5.4) — keep fail-open on portal_user miss only |
| `test_mfa_check_failure_defaults_to_pass` | no-raise (fail-open) | **DELETE** and replace with 503 assertion (REQ-5.3) |

### 5.2 Existing test style (to change)

All tests patch `app.api.auth.zitadel` as a `MagicMock` and stub each
method. This misses regressions where the `ZitadelClient` wrapper
itself silently swallows an error.

REQ-5.7 requires the new tests in
`tests/test_auth_mfa_fail_closed.py` to use `respx` against the real
`ZitadelClient`. `respx` is already a transitive dev dependency via
`httpx-mock` in `klai-portal/backend/pyproject.toml` (verified to be a
standard Klai pattern from other test files, e.g. Zitadel client
tests).

### 5.3 Structured-log assertions

Tests SHALL assert on log output using `caplog` + a structlog-JSON
processor fixture. The pattern is already used in other Klai test
modules (e.g. `test_auth_security.py` uses `caplog` for audit-failure
coverage).

---

## 6. Deployment / rollout considerations

### 6.1 Risk of user-visible 503s

Under steady state, `mfa_check_failed` events are expected to be < 1
per minute for the entire tenant base. Under a Zitadel restart flap
window, a burst of 503s is the intended, documented behaviour.

### 6.2 Backward compat for the frontend

The portal frontend's login form at
`klai-portal/frontend/src/routes/login.tsx` (approximate path — will
be verified at Run phase) already handles 502 with a generic "try
again later" message. 503 with `Retry-After: 5` will be surfaced
identically. No frontend changes are required in this SPEC.

### 6.3 Canary

Land behind the existing `PORTAL_RLS_GUARD_STRICT=1` pattern is NOT
applicable — MFA enforcement has no feature flag. This is a
behavioural change that goes to all tenants at once. Mitigation:

- The Grafana alerts (REQ-4.5, REQ-4.6) provide real-time visibility.
- The `optional` default on `PortalOrg.mfa_policy` means only orgs
  that have explicitly opted into `required` see behavioural change.
- In-code comment at the new 503 sites cross-references SPEC-SEC-MFA-001
  for auditability.

### 6.4 Tracker closure

SPEC-SEC-AUDIT-2026-04 rows for findings #11 and #12 close when:
- Code change merged AND
- `test_mfa_check_failure_defaults_to_pass` no longer exists in the
  test suite AND
- Grafana alert `mfa-check-failed` is active (visible in Grafana
  Alerting dashboard).
