# SPEC-SEC-MAILER-INJECTION-001 Acceptance Criteria

Verifiable scenarios for each requirement. Every AC maps to at least one REQ and to a
pytest test case under `klai-mailer/tests/`. The full set must pass before this SPEC is
marked `status: done`.

Legend: AC-N (Acceptance Criterion) -> REQ-N.M (spec requirement). All scenarios assume
a test harness with a fake Redis (fakeredis), a respx-mocked portal-api, and a
StubSMTPSender that captures outbound messages.

---

## AC-1: `str.format` introspection payload is rejected

**Covers:** REQ-1.1, REQ-1.2, REQ-2.1, REQ-2.3

**Setup:** klai-mailer running with valid `WEBHOOK_SECRET`, `INTERNAL_SECRET`. Redis up.

**Action:** POST to `/internal/send` with:
```http
POST /internal/send
X-Internal-Secret: <correct-secret>
Content-Type: application/json

{
  "template": "join_request_admin",
  "to": "admin@example.com",
  "locale": "nl",
  "variables": {
    "name": "{__class__.__mro__[1].__subclasses__}",
    "email": "alice@example.com",
    "org_id": 42
  }
}
```

**Expected:**
- HTTP 400 (one of two acceptable bodies):
  - `{"detail": "unexpected placeholder"}` IF the sandbox raises `SecurityError` on
    the rendered placeholder, OR
  - Rendered as literal string (the `__class__...` text appears unchanged in the email
    body because the sandbox treats it as a plain string, not a template expression).
- The StubSMTPSender MUST NOT receive any message (the request is rejected before
  SMTP dispatch for both acceptable outcomes -- the literal-string rendering path
  still sends an email, so the test asserts that either (a) the request is 400, OR
  (b) the sent body contains the attacker's string LITERALLY with zero `object` /
  `subprocess` / `settings` content).
- Structured log event `mailer_template_sandbox_violation` (if 400) emitted with
  `reason="sandbox_error"`; attacker-supplied placeholder SHALL NOT appear in the log
  payload.

**Test:** `tests/test_internal_send_sandbox.py::test_str_format_payload_rejected`

---

## AC-2: `to_address` outside template allowlist is rejected

**Covers:** REQ-3.1, REQ-3.2

**Setup:** portal-api respx-mocked to respond `{"admin_email": "admin@example.com"}`
for `GET /internal/org/42/admin-email`.

**Action:** POST to `/internal/send` with:
```json
{
  "template": "join_request_admin",
  "to": "attacker@evil.com",
  "locale": "nl",
  "variables": {
    "name": "Alice",
    "email": "alice@example.com",
    "org_id": 42
  }
}
```

**Expected:**
- HTTP 400 with body `{"detail": "recipient mismatch"}`.
- StubSMTPSender MUST NOT receive any message.
- Structured log event `mailer_recipient_mismatch` emitted with
  `template="join_request_admin"`, `expected_hash=<sha256-of-admin-email>`,
  `supplied_hash=<sha256-of-attacker-email>`. Cleartext emails SHALL NOT appear in the
  log.

**Secondary scenario for REQ-3.2:** POST to `/internal/send` with
`template="join_request_approved"`, `to="attacker@evil.com"`,
`variables={"name": "Bob", "workspace_url": "https://ws.klai.example"}`. Because
`JoinRequestApprovedVars` does not include an explicit `email` field, the handler
MUST either:
- Treat `to` as the recipient and validate it against `variables.email` (requires the
  schema to carry `email` -- if chosen, add it to `JoinRequestApprovedVars`), OR
- Derive the recipient from `variables.email` alone (ignore `to`).

Both acceptable per REQ-3.2. The test asserts the final SMTP recipient equals
`variables.email` OR the request is rejected with 400.

**Test:** `tests/test_internal_send_recipient.py::test_recipient_mismatch_rejected`

---

## AC-3: 11th send to same recipient in 24h returns 429

**Covers:** REQ-4.1, REQ-4.2, REQ-4.4

**Setup:** fakeredis empty at test start. portal-api mocked to return
`admin@example.com`. Valid INTERNAL_SECRET. Default ceiling of 10/24h.

**Action:**
1. POST `/internal/send` 10 times with `to=admin@example.com` (all valid payloads).
   Each returns HTTP 200.
2. POST an 11th time with the same recipient.

**Expected on 11th request:**
- HTTP 429 with body `{"detail": "recipient rate limit exceeded"}`.
- Response MUST include `Retry-After: <N>` header where N is a positive integer
  (seconds until the oldest counted send falls out of the 24h window).
- StubSMTPSender has received exactly 10 messages (the 11th is blocked before
  dispatch).
- Structured log event `mailer_recipient_rate_limited` emitted.

**Secondary scenarios:**
- Sends to a DIFFERENT recipient within the same test run succeed normally (budgets
  are per-recipient, not global).
- Case-insensitive collision: `ADMIN@example.com` and `admin@example.com` share a
  budget (REQ-4.2 specifies `lowercase-recipient-email` in the hash key).
- Redis unreachable: fail-open (REQ-4.3) -- the test substitutes a Redis client that
  raises ConnectionError on every op and asserts the 11th send ALSO returns 200 with
  log event `mailer_rate_limit_redis_unavailable`.

**Test:** `tests/test_internal_send_rate_limit.py::test_eleventh_send_returns_429`

---

## AC-4: `/debug` returns 404 when `PORTAL_ENV=production`

**Covers:** REQ-5.1, REQ-5.2, REQ-5.4, REQ-5.5

**Setup:** Instantiate `app` with `PORTAL_ENV=production`, `DEBUG=true`. Both values
are set; the double-gate MUST still fire.

**Action:** POST to `/debug` with a valid-signed Zitadel payload.

**Expected:**
- HTTP 404 with body `{"detail": "Not found"}`.
- NO structured log event is emitted for this request (REQ-5.5).
- The `_verify_zitadel_signature` function is NOT called (the 404 short-circuits
  before signature work).

**Secondary scenarios:**
- `PORTAL_ENV=development`, `DEBUG=true` -> the endpoint works normally, logs the
  parsed payload, returns 200.
- `PORTAL_ENV=development`, `DEBUG=false` -> 404 (current behaviour, REQ-5.2
  preserved).
- `PORTAL_ENV=staging`, `DEBUG=true` -> the endpoint works (staging is not
  production; the gate only blocks production).

**Test:** `tests/test_debug_gate.py::test_production_env_returns_404_even_with_debug_true`

---

## AC-5: Zitadel webhook replay within 5-min window is rejected

**Covers:** REQ-6.1, REQ-6.2, REQ-7.1

**Setup:** fakeredis empty. Known `WEBHOOK_SECRET`. Generate a valid signed webhook
request at `t=now`.

**Action:**
1. POST the request to `/notify`. Expect HTTP 200 (first call, recorded nonce).
2. POST the IDENTICAL request (same `ZITADEL-Signature` header, same body) a second
   time within 60 seconds.

**Expected on second call:**
- HTTP 401 with body `{"detail": "invalid signature"}` (uniform per REQ-7.1; "replay"
  string NOT leaked in the response).
- Structured log event `mailer_signature_invalid` emitted with
  `reason="replay"`.
- StubSMTPSender received exactly 1 message (from the first call).

**Secondary scenario (REQ-6.3 Redis unreachable):** Substitute a Redis client that
raises on all ops. POST a legitimate webhook. Expect HTTP 503 with body
`{"detail": "Service unavailable"}` and log event
`mailer_nonce_redis_unavailable`. Fail-closed -- the webhook is NOT delivered.

**Test:** `tests/test_notify_replay.py::test_replay_within_window_rejected`

---

## AC-5.1 (v0.3.1): Redis URL with reserved-character password connects successfully

**Covers:** REQ-6.5

**Background:** Operators commonly omit URL-encoding when copying a
generated Redis password into SOPS. Before v0.3.1, `redis_asyncio.from_url`
would raise `ValueError("Port could not be cast")` on first webhook,
returning HTTP 500 instead of REQ-6.3's contracted 503.

**Setup:** Set `REDIS_URL=redis://:p:hPKBf@redis:6379/0` (password contains
unescaped `:`). Reset the lazy redis singleton via
`app.nonce.reset_redis_client()`.

**Action:** Call `app.nonce.get_redis()`.

**Expected:**
- A `redis.asyncio.Redis` client instance is returned (no exception).
- The instance was constructed via `redis_asyncio.Redis(host=..., port=...,
  password='p:hPKBf', db=0, ...)` — i.e. the password kwarg matches the
  raw password from the URL, byte-for-byte.

**Negative scenario — structurally broken URL:**
- Set `REDIS_URL=memcached://host:11211` (wrong scheme).
- Call `get_redis()`.
- Expect: `RedisUnavailableError` raised with message
  `"REDIS_URL is malformed: REDIS_URL unsupported scheme: 'memcached'"`.
- Log line `mailer_redis_url_invalid` emitted at ERROR level.
- The `_verify_zitadel_signature` handler catches the
  `RedisUnavailableError` and returns HTTP 503 (REQ-6.3 contract holds
  for config errors as well as runtime outages).

**Test:** `tests/test_redis_url.py` (17 cases) — happy path, every
reserved-char regression (`:`, `/`, `+`, `@`, all-combined), structural
errors, and empty-component normalisation. The single most important
case is `test_password_with_colon_does_not_become_port` which
reproduces the 2026-04-29 prod outage shape.

---

## AC-6: Uniform 401 body for every signature-verification failure

**Covers:** REQ-7.1, REQ-7.2, REQ-10.1

**Action:** Issue 5 POSTs to `/notify`, each triggering a different verification
failure mode:

| Scenario | Request modification |
|---|---|
| Missing header | No `ZITADEL-Signature` header |
| Malformed header | `ZITADEL-Signature: garbage` |
| Timestamp out of window | `t=<now-400>,v1=<correct-hmac>` |
| HMAC mismatch | `t=<now>,v1=deadbeef` |
| Unknown `vN` field | `t=<now>,v1=<correct>,v2=extra` |

**Expected for all 5:**
- HTTP 401 with body EXACTLY `{"detail": "invalid signature"}` (byte-identical).
- No `WWW-Authenticate` or any other header distinguishing the failure phase.
- Each emits a structured log event `mailer_signature_invalid` with distinct `reason`
  sub-field: `missing_header`, `malformed_header`, `timestamp_out_of_window`,
  `hmac_mismatch`, `unknown_vN_field`.
- Test asserts:
  - `response.text` is byte-identical across all 5 calls.
  - Each log `reason` value is distinct and matches expectation.

**Test:** `tests/test_notify_error_taxonomy.py::test_uniform_401_body_across_failure_modes`

---

## AC-7: Empty `WEBHOOK_SECRET` refuses startup

**Covers:** REQ-9.1

**Action:** Invoke `Settings()` construction with environment:
- `WEBHOOK_SECRET=""` (empty)
- `INTERNAL_SECRET="valid-secret"`
- All other fields populated.

**Expected:**
- `ValidationError` raised from pydantic-settings, with a message including
  `Missing required: WEBHOOK_SECRET`.
- The uvicorn entrypoint (test invocation with CliRunner or subprocess) exits with
  non-zero status before binding any port.

**Secondary scenarios (REQ-9.2):**
- `WEBHOOK_SECRET="valid"`, `INTERNAL_SECRET=""` -> `ValidationError` with
  `Missing required: INTERNAL_SECRET`.
- `WEBHOOK_SECRET="   "` (whitespace only) -> same error as empty.
- Both valid -> `Settings()` constructs successfully.

**Test:** `tests/test_config_fail_closed.py::test_empty_webhook_secret_refuses_startup`

---

## AC-8: `ZITADEL-Signature` with extra `v2=` field is rejected

**Covers:** REQ-10.1, REQ-10.2, REQ-7.1

**Action:** POST to `/notify` with header
`ZITADEL-Signature: t=<now>,v1=<valid-hmac>,v2=unexpected`.

The `v1` value IS a correct HMAC over the body; only the extra `v2` field is the
anomaly. Without REQ-10, the current parser at `main.py:61` silently accepts this
because the dict comprehension includes all `k=v` pairs.

**Expected:**
- HTTP 401 with body `{"detail": "invalid signature"}` (REQ-7 uniform).
- Structured log event `mailer_signature_unknown_field` OR
  `mailer_signature_invalid` with `reason="unknown_vN_field"` and
  `unknown_fields=["v2"]`.
- StubSMTPSender receives no message.

**Secondary scenarios:**
- 5-token header is accepted (just under the defence threshold).
- 6-token header is rejected with `reason="unknown_vN_field"` (REQ-10.3).
- Header with unknown `ver=1` (non-`v` prefix) is rejected.

**Test:** `tests/test_notify_signature_parser.py::test_extra_v2_field_rejected`

---

## AC-9: Legitimate emails still render correctly (golden-output regression)

**Covers:** REQ-1.5, REQ-2.1, REQ-2.4, REQ-3.1, REQ-3.2

**Setup:** portal-api mocked to return `admin@example.com` for org 42. Valid
INTERNAL_SECRET. fakeredis fresh.

**Golden fixtures:** `tests/fixtures/golden/join_request_admin.nl.html`,
`join_request_admin.en.html`, `join_request_approved.nl.html`,
`join_request_approved.en.html`. Each is the current (pre-REQ-1 migration) output
rendered via `str.format` with canonical input values. These fixtures are committed
alongside this SPEC.

**Canonical inputs:**
- `join_request_admin`: `{"name": "Alice Example", "email": "alice@example.com", "org_id": 42}`
- `join_request_approved`: `{"name": "Bob Requester", "workspace_url": "https://app.klai.example"}`

**Action:** For each `(template, locale)` pair, POST to `/internal/send` with the
canonical input. Capture the rendered HTML from StubSMTPSender.

**Expected:**
- Rendered HTML is byte-identical to the golden fixture for each `(template, locale)`.
- Acceptable diff: whitespace-only changes inside HTML tags (normalise via
  `htmlmin` or `lxml.html.tostring` before comparison). Semantic content MUST match
  exactly.
- For `join_request_admin` the SMTP `to_address` equals the portal-api-resolved admin
  email (not the caller-supplied `to`).
- StubSMTPSender `subject` equals the expected per-locale subject line.
- `{{ brand_url }}` in the template resolves to `settings.brand_url`, not any
  caller-supplied value (REQ-2.4).

**Test:** `tests/test_internal_send_golden.py::test_join_request_admin_nl_matches_golden`
(and parametrised siblings for the other 3 pairs).

---

## AC-10 (cross-requirement): `/internal/send` with wrong internal secret returns 401 in constant time

**Covers:** REQ-8.1, REQ-8.2

**Action:** POST to `/internal/send` with various wrong `X-Internal-Secret` header
values:
- Correct-length wrong secret.
- 1-char secret.
- 128-char random secret.
- Empty string.
- Header omitted.

**Expected:**
- All return HTTP 401 with body `{"detail": "Unauthorized"}`.
- A pytest microbenchmark (`@pytest.mark.slow`) calls the auth helper 10_000 times
  with a length-1 wrong secret and 10_000 times with a length-128 wrong secret;
  the mean wall-clock difference SHALL be less than 50 microseconds per call.
- This benchmark documents (not enforces) the constant-time property; CI can skip
  it with `-m 'not slow'`. Mirrors SPEC-SEC-WEBHOOK-001 REQ-5.4.

**Test:** `tests/test_internal_send_auth.py::test_compare_digest_used`

---

## Out-of-AC regression coverage

Not every sub-requirement maps to a named AC. The following are verified implicitly
by the ACs above or by unit-level tests that are required-but-unnumbered:

- REQ-1.3 (StrictUndefined) -- covered by a unit test that calls the render helper
  with `{}` variables against a template referencing `{{ foo }}`; expects
  `UndefinedError`.
- REQ-2.4 (branding injected from settings, not caller) -- covered by AC-9 (the
  golden output uses `settings.brand_url` and the caller does not supply it).
- REQ-4.5 (failed-validation sends don't deplete budget) -- covered by a unit test
  that asserts the Redis counter is unchanged after a schema-validation failure.
- REQ-4.6 (no cleartext email in rate-limit logs) -- covered by a log-capture
  assertion in AC-3's test.
- REQ-5.3 (conditional route registration) -- optional; if implemented, covered by a
  test that asserts `app.routes` does NOT contain `/debug` when
  `PORTAL_ENV=production`.
- REQ-6.4 (nonce check AFTER signature verification) -- covered by a test that sends
  a FORGED signature with a never-seen nonce; the nonce counter MUST remain at 0
  (forged sigs don't pollute the nonce cache).
- REQ-9.3 (mode="after" validator) -- validated via the signature of the validator in
  `config.py`; a code-review checklist item, not an automated test.

---

## CI integration

All ACs run in the `klai-mailer` pytest suite on every PR via
`klai-mailer/pyproject.toml` test config. Golden fixtures live under
`klai-mailer/tests/fixtures/golden/` and are versioned in git.

Ruff check MUST pass post-implementation (no new F821, no new S-series violations).
The `SandboxedEnvironment` import is whitelisted in the pre-existing
`# nosemgrep: direct-use-of-jinja2` comment-block; the sandbox constructor is the
intended safer replacement.

Coverage gate: this SPEC adds the `schemas.py`, `rate_limit.py`, `nonce.py` modules.
Target 85%+ line coverage per the Python rules (`moai/languages/python.md`). Existing
mailer coverage is below target; this SPEC does not regress it.

---

## Rollout / staging plan

1. Land REQ-9 (fail-closed validators) first -- smallest change, no runtime behaviour
   shift (since both secrets are already set in prod env).
2. Land REQ-8 (`hmac.compare_digest`) second -- bug-compat with current callers.
3. Land REQ-10 + REQ-7 (parser hardening + uniform 401) together -- both touch
   `_verify_zitadel_signature`.
4. Land REQ-6 (nonce) with REQ-10 -- same function, same test suite.
5. Land REQ-5 (/debug double-gate) -- small, independent.
6. Land REQ-1 + REQ-2 + REQ-3 + REQ-4 as one logical unit (the injection-hardening
   landing). This is the largest change and requires AC-9 golden-output verification.
   Portal-api side MUST add `org_id` to the `join_request_admin` payload in the same
   PR.
7. Deploy with the rate limit ceiling of 10/24h-per-recipient from the start; observe
   via the `mailer_recipient_rate_limited` log event. Adjust `settings.mailer_rate_limit_per_recipient`
   upward if legitimate admin flows hit the ceiling.
