# SPEC-SEC-MFA-001 Acceptance Scenarios

Testable scenarios derived from REQ-1..REQ-5. All scenarios use
`pytest`, `pytest-asyncio`, and `respx` mounted against the real
`ZitadelClient` instance in `app.services.zitadel`. The target test
module is `klai-portal/backend/tests/test_auth_mfa_fail_closed.py`.

Each scenario specifies:
- the REQ id(s) it satisfies
- the respx mocks to mount
- the DB setup (SQLAlchemy fixture)
- the expected HTTP status, headers, response body
- the expected structured-log event

Common fixtures (assumed present, created if missing at Run phase):

```
# conftest additions for this module

@pytest_asyncio.fixture
async def respx_zitadel(respx_mock):
    # Base URL matches ZitadelClient._http.base_url (e.g. https://auth.getklai.com)
    return respx_mock

@pytest.fixture
def portal_org_required(db_session):
    org = PortalOrg(id=10, name="acme", mfa_policy="required")
    db_session.add(org); return org

@pytest.fixture
def portal_org_optional(db_session):
    org = PortalOrg(id=11, name="beta", mfa_policy="optional")
    db_session.add(org); return org

@pytest.fixture
def portal_user_in_required_org(db_session, portal_org_required):
    u = PortalUser(zitadel_user_id="uid-req", org_id=10)
    db_session.add(u); return u
```

---

## Scenario 1 — mfa_policy=required + has_any_mfa 500 → 503

**REQ:** REQ-1.1, REQ-1.3, REQ-1.4, REQ-1.5, REQ-4.1, REQ-4.2, REQ-5.2(a)

**Arrange:**

```
respx_zitadel.post("/v2/users").mock(
    return_value=httpx.Response(200, json={"result": [
        {"userId": "uid-req", "details": {"resourceOwner": "zorg-req"}}
    ]})
)
respx_zitadel.get("/v2/users/uid-req/authentication_methods").mock(
    return_value=httpx.Response(500, json={"error": "internal"})
)
respx_zitadel.post("/v2/sessions").mock(
    return_value=httpx.Response(200, json=_session_ok())
)
# portal_user + portal_org seeded via fixtures
```

**Act:**

```
resp = await client.post("/api/auth/login",
    json={"email": "alice@acme.com", "password": "correct-horse",
          "auth_request_id": "ar-1"})
```

**Assert:**

- `resp.status_code == 503`
- `resp.headers["Retry-After"] == "5"`
- `resp.json() == {"detail": "Authentication service temporarily unavailable, please retry in a moment"}`
- `caplog` contains one entry with:
  - `event == "mfa_check_failed"`
  - `reason == "has_any_mfa_5xx"`
  - `mfa_policy == "required"`
  - `zitadel_status == 500`
  - `outcome == "503"`
  - `level == "error"`
- No `Set-Cookie` header on the response (REQ-1.5).

---

## Scenario 2 — find_user_by_email 500 → 503

**REQ:** REQ-2.1, REQ-2.4(b), REQ-2.5, REQ-4.1, REQ-5.2(b)

**Arrange:**

```
respx_zitadel.post("/v2/users").mock(
    return_value=httpx.Response(500, json={"error": "internal"})
)
# has_any_mfa route not registered — if the SPEC fails, test would
# hit an unmocked request and surface the bug loudly.
```

**Act:** same login payload as Scenario 1.

**Assert:**

- `resp.status_code == 503`
- `resp.headers["Retry-After"] == "5"`
- `caplog` contains one entry with `event == "mfa_check_failed"` AND
  `reason == "find_user_by_email_5xx"` AND `outcome == "503"`.
- `create_session_with_password` NOT called: assert respx that
  `POST /v2/sessions` received 0 calls.
- `mfa_policy` field in the log entry is `"unresolved"` (REQ-4.1) —
  resolution never ran.

---

## Scenario 3 — mfa_policy=optional + has_any_mfa 500 → 200 (documented fail-open)

**REQ:** REQ-3.1, REQ-3.6, REQ-4.1, REQ-4.2, REQ-5.2(c)

**Arrange:**

```
respx_zitadel.post("/v2/users").mock(
    return_value=httpx.Response(200, json={"result": [
        {"userId": "uid-opt", "details": {"resourceOwner": "zorg-opt"}}
    ]})
)
respx_zitadel.get("/v2/users/uid-opt/authentication_methods").mock(
    return_value=httpx.Response(500)
)
respx_zitadel.post("/v2/sessions").mock(
    return_value=httpx.Response(200, json=_session_ok())
)
# Seed: PortalOrg(id=11, mfa_policy="optional") + PortalUser(zitadel_user_id="uid-opt", org_id=11)
```

**Act:** same login payload.

**Assert:**

- `resp.status_code == 200`
- Response body is a successful `LoginResponse` (either `ok` or
  `totp_required` depending on has_totp outcome — Scenario uses
  `has_totp=False` so expect `ok` + cookie).
- `caplog` contains one `mfa_check_failed` entry with:
  - `reason == "has_any_mfa_5xx"`
  - `mfa_policy == "optional"`
  - `outcome == "fail-open"`
  - `level == "warning"`
- `has_any_mfa` SHALL NOT have been called under a stricter reading
  of REQ-3.1, BUT for this scenario `mfa_policy == "optional"` means
  the `if mfa_policy == "required":` guard short-circuits — so the
  log entry only fires when the call is genuinely attempted. This
  scenario therefore asserts the log entry fires only via the
  expanded catch on the pre-auth `find_user_by_email` / DB path when
  those fail; the `has_any_mfa` route mock is present but uncalled.
  (Clarification: the respx mock asserts this — it should record
  zero calls.)

---

## Scenario 4 — Happy path MFA login (regression)

**REQ:** REQ-5.2(d)

**Arrange:**

```
respx_zitadel.post("/v2/users").mock(
    return_value=httpx.Response(200, json={"result": [
        {"userId": "uid-req", "details": {"resourceOwner": "zorg-req"}}
    ]})
)
respx_zitadel.get("/v2/users/uid-req/authentication_methods").mock(
    return_value=httpx.Response(200, json={
        "authMethodTypes": ["AUTHENTICATION_METHOD_TYPE_TOTP"]
    })
)
respx_zitadel.post("/v2/sessions").mock(
    return_value=httpx.Response(200, json=_session_ok())
)
# PortalOrg(id=10, mfa_policy="required") + PortalUser(zitadel_user_id="uid-req", org_id=10)
```

**Act:** same login payload.

**Assert:**

- `resp.status_code == 200`
- `resp.json()["status"] == "totp_required"`
- `resp.json()["temp_token"]` is a non-empty string.
- No `mfa_check_failed` event emitted.
- No `Set-Cookie` header yet (cookie is set on `totp-login` completion).

---

## Scenario 5 — Happy path no-MFA login under optional (regression)

**REQ:** REQ-5.2(e)

**Arrange:**

```
respx_zitadel.post("/v2/users").mock(
    return_value=httpx.Response(200, json={"result": [
        {"userId": "uid-opt", "details": {"resourceOwner": "zorg-opt"}}
    ]})
)
respx_zitadel.get("/v2/users/uid-opt/authentication_methods").mock(
    return_value=httpx.Response(200, json={"authMethodTypes": []})
)
respx_zitadel.post("/v2/sessions").mock(
    return_value=httpx.Response(200, json=_session_ok())
)
# PortalOrg(id=11, mfa_policy="optional") + PortalUser(zitadel_user_id="uid-opt", org_id=11)
```

**Act:** same login payload.

**Assert:**

- `resp.status_code == 200`
- `resp.json()["status"] == "ok"` (or whatever the current successful
  non-TOTP status string is — confirmed during Run phase)
- `Set-Cookie` header present (session cookie minted by
  `_finalize_and_set_cookie`).
- No `mfa_check_failed` event emitted.
- Assert respx that `/v2/users/uid-opt/authentication_methods` was
  called once (for `has_totp`) but that `has_any_mfa` was NOT called
  a second time — because `mfa_policy == "optional"` short-circuits.

---

## Scenario 6 — find_user_by_email 404 → continues to 401

**REQ:** REQ-2.3, REQ-5.2(f)

**Arrange:**

```
respx_zitadel.post("/v2/users").mock(
    return_value=httpx.Response(200, json={"result": []})
)
respx_zitadel.post("/v2/sessions").mock(
    return_value=httpx.Response(401, json={"error": "invalid credentials"})
)
```

**Note:** Zitadel returns `result: []` rather than 404 for "no user
found"; documenting this as the "4xx is well-formed" path. If any
other 4xx surfaces in practice (e.g. 429 from rate-limiting), a
follow-up variant will be added during Run phase.

**Act:** same login payload with unknown email.

**Assert:**

- `resp.status_code == 401`
- `resp.json() == {"detail": "Email address or password is incorrect"}`
- No `mfa_check_failed` event (this is a valid not-found path, not an
  MFA failure).
- Audit log written via `audit.log_event(action="auth.login.failed",
  reason="invalid_credentials")`.

---

## Scenario 7 — portal_user found + org fetch raises → 503

**REQ:** REQ-3.2, REQ-5.2(g), REQ-5.4

**Arrange:**

```
respx_zitadel.post("/v2/users").mock(
    return_value=httpx.Response(200, json={"result": [
        {"userId": "uid-req", "details": {"resourceOwner": "zorg-req"}}
    ]})
)
respx_zitadel.get("/v2/users/uid-req/authentication_methods").mock(
    return_value=httpx.Response(200, json={"authMethodTypes": []})
)
respx_zitadel.post("/v2/sessions").mock(
    return_value=httpx.Response(200, json=_session_ok())
)
# PortalUser exists (org_id=10); monkeypatch db.get(PortalOrg, 10)
# to raise InsufficientPrivilegeError — simulates RLS GUC leak
```

Patch:

```
monkeypatch.setattr(db, "get", AsyncMock(
    side_effect=asyncpg.exceptions.InsufficientPrivilegeError(
        "RLS: app.current_org_id is not set")))
```

**Act:** same login payload.

**Assert:**

- `resp.status_code == 503`
- `resp.headers["Retry-After"] == "5"`
- `caplog` contains `mfa_check_failed` with:
  - `reason == "db_lookup_failed"`
  - `mfa_policy == "unresolved"`
  - `outcome == "503"`
- No `Set-Cookie` header.

Variant 7a (fail-open path — portal_user NOT found):

```
# db.scalar(select(PortalUser)...) returns None
```

**Assert:**

- `resp.status_code == 200` (fail-open — cannot know policy without
  the portal_user row; user may belong to an org not yet provisioned
  in portal-api; blocking all such logins would break provisioning).
- `caplog` contains `mfa_check_failed` with `outcome == "fail-open"`
  AND `reason == "db_lookup_failed"` AND `level == "warning"`.

---

## Scenario 8 — has_any_mfa RequestError (connection refused) → 503

**REQ:** REQ-1.2, REQ-5.2(a)-variant

**Arrange:**

```
respx_zitadel.post("/v2/users").mock(return_value=httpx.Response(200, ...))
respx_zitadel.get("/v2/users/uid-req/authentication_methods").mock(
    side_effect=httpx.ConnectError("Connection refused")
)
respx_zitadel.post("/v2/sessions").mock(return_value=httpx.Response(200, json=_session_ok()))
```

**Act:** same login payload.

**Assert:**

- `resp.status_code == 503`
- `resp.headers["Retry-After"] == "5"`
- `caplog` contains `mfa_check_failed` with `reason == "has_any_mfa_5xx"`
  AND `zitadel_status` is `null` (no response body available) AND
  `outcome == "503"`.

---

## Coverage summary

| REQ | Scenario(s) |
|---|---|
| REQ-1.1 | 1 |
| REQ-1.2 | 8 |
| REQ-1.3 | 1, 2, 7, 8 |
| REQ-1.4 | 1 (no `user_has_mfa=True` fallback) |
| REQ-1.5 | 1, 2, 7 (no cookie) |
| REQ-1.6 | Run-phase addition — Scenario 8 variant with generic Exception |
| REQ-1.7 | Replaces deleted test |
| REQ-2.1 | 2 |
| REQ-2.2 | Run-phase addition — variant of Scenario 2 with RequestError |
| REQ-2.3 | 6 |
| REQ-2.4 | 2, 6 |
| REQ-2.5 | 2 (assert `create_session_with_password` not called) |
| REQ-2.6 | Integration-side verification in Run phase |
| REQ-2.7 | 2 |
| REQ-3.1 | 3 |
| REQ-3.2 | 7 + 7a |
| REQ-3.3 | 6 |
| REQ-3.4 | Run-phase addition — mfa_policy="recommended" variant |
| REQ-3.5 | Existing test kept |
| REQ-3.6 | 3 |
| REQ-3.7 | Code-review verification |
| REQ-4.1 | 1, 2, 3, 7, 7a, 8 |
| REQ-4.2 | 1 (error), 3 + 7a (warning) |
| REQ-4.3 | Scenario-5 negative: assert log entries never contain
          plaintext email or zitadel_user_id for pre-auth path |
| REQ-4.4 | All scenarios: assert `request_id` field present |
| REQ-4.5 | Grafana alert YAML review + alertmanager dry-run |
| REQ-4.6 | Same as REQ-4.5 |
| REQ-4.7 | Runbook file exists at Sync phase |
| REQ-5.1 | Test-module-exists check |
| REQ-5.2 | Scenarios 1-7 map directly |
| REQ-5.3 | Deleted-test check in CI (grep fails if name resurfaces) |
| REQ-5.4 | 7 + 7a |
| REQ-5.5 | caplog assertions on every scenario above |
| REQ-5.6 | `pytest --cov=app.api.auth --cov-fail-under=85` + branch cov |
| REQ-5.7 | Uses real `ZitadelClient` via respx — no mock of module attr |

---

## Out-of-test verification

Some acceptance criteria cannot be covered by unit tests and are
verified at Sync phase:

- **Grafana alert rules load** — apply YAML, verify alert appears in
  Grafana UI under Alerting → mfa-check-failed.
- **LogsQL query returns expected schema** — run
  `service:portal-api AND event:mfa_check_failed` against VictoriaLogs
  staging; confirm fields visible in decoded JSON.
- **Runbook file reachable** — `docs/runbooks/mfa-check-failed.md`
  linked from Grafana alert `runbook_url` annotation.
- **No fail-open path remaining** — manual code review of the final
  `auth.py::login` handler against the catalogue in research.md §4.
