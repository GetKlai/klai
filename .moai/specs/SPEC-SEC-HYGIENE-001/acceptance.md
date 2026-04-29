---
id: SPEC-SEC-HYGIENE-001
version: 0.3.0
created: 2026-04-24
updated: 2026-04-24
author: Mark Vletter
artifact: acceptance
---

# SPEC-SEC-HYGIENE-001 — Acceptance Criteria

One testable scenario per finding. Each scenario maps to a regression
test (or, for #23, a documentation assertion) that would have caught
the original finding before it was introduced.

Test file locations follow the existing `klai-portal/backend/tests/`
layout. All tests use `pytest-asyncio` and `httpx.AsyncClient`
fixtures per the existing portal-api test conventions.

---

## AC-19 — Signup per-email rate limit

**Scenario:** An attacker (or a buggy client) submits 4 successful
signups from the same normalised email within 24 hours.

**Test file:** `klai-portal/backend/tests/test_signup_rate_limit.py`

**Setup:**
- Fresh Redis key namespace (test fixture flushes `signup_email_rl:*`).
- Zitadel mocked to return 201 for org + user creation.
- `portal_orgs`/`portal_users` in a clean test DB.

**Steps:**
1. `POST /api/signup` with
   `{"email": "attacker@example.com", ...}` — expect 201.
2. Repeat with `{"email": "Attacker@Example.com", ...}` (case
   variant) — expect 201 (second signup, same normalised email).
3. Repeat with `{"email": "attacker+foo@example.com", ...}` (plus
   alias variant) — expect 201 (third signup, same normalised email).
4. Repeat with `{"email": "attacker+bar@example.com", ...}` — expect
   **429** with body
   `{"detail": "Too many signup attempts for this email. Please try again tomorrow."}`.

**Pass condition:**
- Step 4 returns 429.
- A structlog event `signup_email_rate_limited` is emitted with
  `email_sha256` (not the plaintext email).
- The Zitadel mock is NOT called on step 4 (the rate-limit check
  fires BEFORE org creation, per REQ-19.5).

**Covers:** REQ-19.1, REQ-19.2, REQ-19.3, REQ-19.5.

**Fail-open sub-test (REQ-19.4):**
- Monkeypatch `get_redis_pool()` to raise `ConnectionError`.
- `POST /api/signup` — expect 201 (fail open).
- A structlog warning `signup_email_rl_redis_unavailable` is emitted.

---

## AC-20 — Callback URL subdomain allowlist

**Scenario:** An OIDC callback arrives with `callback_url` pointing
to an unprovisioned subdomain of getklai.com (e.g.
`https://dangling.getklai.com/api/auth/idp-callback`).

**Test file:** `klai-portal/backend/tests/test_validate_callback_url.py`

**Setup:**
- Seed `portal_orgs` with two tenants: slugs `voys`, `getklai`.
- `settings.domain = "getklai.com"`.
- Allowlist cache pre-warmed or first-query allowed.

**Steps:**
1. Call `_validate_callback_url("https://voys.getklai.com/x")` →
   **returns the URL unchanged**.
2. Call `_validate_callback_url("https://dangling.getklai.com/x")` →
   **raises HTTPException(502)** with detail
   `"Login failed, please try again later"`.
3. Call `_validate_callback_url("https://getklai.com/x")` →
   **returns the URL unchanged** (bare domain).
4. Call `_validate_callback_url("http://localhost:3000/x")` →
   **returns the URL unchanged** (localhost escape hatch).
5. Call `_validate_callback_url("https://evil.com/x")` →
   **raises HTTPException(502)** (not even a getklai.com subdomain).

**Pass condition:**
- Step 2 raises and logs `callback_url_subdomain_not_allowlisted`
  with the hostname.
- Steps 1, 3, 4 pass through unchanged.
- Cache-miss on step 1 emits `tenant_slug_allowlist_cache_miss` exactly
  once; second call within 60s does not re-emit.

**Covers:** REQ-20.1, REQ-20.2, REQ-20.3.

---

## AC-21 — `_safe_return_to` backslash and percent-decode

**Scenario:** An attacker submits a crafted `return_to` query param
intended to redirect the user to an external site via URL-parsing
ambiguity.

**Test file:** `klai-portal/backend/tests/test_auth_bff_return_to.py`

**Steps (parametrised):**

| Input | Expected output |
|---|---|
| `/\evil.com` | `/app` |
| `/%2fevil.com` | `/app` |
| `/%2Fevil.com` | `/app` (case insensitive) |
| `/\\evil.com` | `/app` |
| `//evil.com` | `/app` |
| `https://evil.com` | `/app` |
| `javascript:alert(1)` | `/app` (no leading `/`) |
| `` (empty) | `/app` |
| `None` (via getter returning None) | `/app` |
| `/app/dashboard` | `/app/dashboard` (unchanged) |
| `/app/dashboard?foo=bar%20baz` | `/app/dashboard?foo=bar%20baz` (unchanged — original returned) |
| `/app/path%2Fsub` | `/app/path%2Fsub` (unchanged — decoded form is safe, return original) |

**Pass condition:**
- All parametrised cases return the expected value.
- The function returns the ORIGINAL (non-decoded) value on success,
  verified by the last two rows.

**Covers:** REQ-21.1, REQ-21.2, REQ-21.3, REQ-21.4.

---

## AC-22 — Password strength check

**Scenario:** A user attempts signup with a password that passes the
length check but is weak per zxcvbn scoring.

**Test file:** `klai-portal/backend/tests/test_signup_password_strength.py`

**Steps:**

1. `POST /api/signup` with password `"Short1!"` (7 chars) →
   expect 422, validation error mentions minimum length.
2. `POST /api/signup` with password `"Password1234"` (12 chars, zxcvbn
   score 1) → expect 422, validation error:
   `"Wachtwoord is te zwak. Kies een langer of minder voorspelbaar wachtwoord."`
3. `POST /api/signup` with password `"aaaaaaaaaaaa"` (12 chars, score 0)
   → expect 422, same message.
4. `POST /api/signup` with password `"Voys2026Klai"` (12 chars but
   contains company_name=`"Voys"` as user_input) → expect 422, same
   message (zxcvbn user_inputs wiring works).
5. `POST /api/signup` with password
   `"correct horse battery staple"` (28 chars, high zxcvbn score) →
   expect 201.

**Pass condition:**
- Steps 1-4 return 422 with the correct detail.
- Step 5 returns 201.
- Step 4 specifically verifies that `company_name` is passed through
  to zxcvbn `user_inputs` (REQ-22.3).

**Fallback sub-test (REQ-22.4):**
- Monkeypatch `_ZXCVBN_AVAILABLE = False`.
- `POST /api/signup` with password `"Password1234"` → expect 201
  (fallback passes the length check).
- A structlog error `zxcvbn_unavailable_falling_back_to_length_check`
  is emitted at module load time (captured via log fixture).

**Covers:** REQ-22.1, REQ-22.2, REQ-22.3, REQ-22.4.

---

## AC-23 — Widget-config Origin documentation

**Scenario:** This is a documentation-only finding. The acceptance test
asserts that the docstring and `@MX:REASON` annotation contain the
required text.

**Test file:** `klai-portal/backend/tests/test_widget_config_docs.py`

**Setup:**
- Import `from app.api.partner import widget_config`.

**Steps:**
1. Assert `widget_config.__doc__` is not None.
2. Assert `"Origin"` appears in the docstring.
3. Assert at least one of the following phrases appears in the
   docstring (case-insensitive): `"UX-only"`, `"UX only"`, `"not a
   security boundary"`, `"UX-gating"`.
4. Assert the docstring mentions `"widget_id"` as the primary
   identifier.
5. Assert the docstring mentions `"JWT"` or `"session_token"` as the
   primary security mechanism.
6. Read the source of `partner.py` and assert the `@MX:REASON` line near
   `widget_config` references the docstring clarification (e.g.
   contains the phrase `"UX-only"` or `"see docstring"`).

**Pass condition:**
All six assertions hold.

**Covers:** REQ-23.1, REQ-23.2 (docs only), REQ-23.3.

---

## AC-24 — Widget JWT per-tenant key isolation

**Scenario:** A token issued for tenant A must not validate when
decoded with tenant B's slug, even though both derive from the same
master secret.

**Test file:** `klai-portal/backend/tests/test_widget_jwt_per_tenant.py`

**Setup:**
- Two tenants: `org_a` with slug `"alpha"`, `org_b` with slug
  `"bravo"`, both with a widget each.
- `settings.widget_jwt_secret = "test-master-secret-32-bytes!!!!"` (32+ B).

**Steps:**
1. Generate token for tenant A:
   `token_a = generate_session_token(wgt_id="wgt_a", org_id=1, kb_ids=[], secret=..., tenant_slug="alpha")`.
2. Decode with tenant A's slug:
   `decode_session_token(token_a, tenant_slug="alpha", master_secret=...)` →
   returns the decoded payload.
3. Decode with tenant B's slug:
   `decode_session_token(token_a, tenant_slug="bravo", master_secret=...)` →
   **raises `jwt.InvalidSignatureError`**.
4. Generate token for tenant B and repeat in reverse — token_b
   decodes with "bravo" and fails with "alpha".

**Pass condition:**
- Step 2 succeeds and returns correct claims (`wgt_id`, `org_id`,
  `kb_ids`, `exp`).
- Step 3 raises `jwt.InvalidSignatureError` (not any other exception
  type — this confirms signature verification actually failed, not a
  schema or TTL failure).
- Step 4 mirror case passes.

**Determinism sub-test:**
- Call `_derive_tenant_key(master, "alpha")` twice — results are
  byte-equal.
- Call `_derive_tenant_key(master, "alpha")` and
  `_derive_tenant_key(master, "bravo")` — results differ.
- Call `_derive_tenant_key(master_v1, "alpha")` and
  `_derive_tenant_key(master_v2, "alpha")` — results differ
  (master-secret rotation invalidates as expected).

**Covers:** REQ-24.1, REQ-24.2, REQ-24.4, REQ-24.5.

---

## AC-27 — `tenant_matcher` cache invalidation on plan change

**Scenario:** A tenant downgrades from `professional` to `free`.
Within 60 seconds, scribe invite eligibility MUST reflect the new
plan.

**Test file:** `klai-portal/backend/tests/test_tenant_matcher_cache.py`

**Setup (Option A — TTL variant):**
- Set `CACHE_TTL = 60` for the test (patched if the module constant).
- Mock `zitadel.find_user_by_email` to return a valid user.
- Mock the plan lookup to return `"professional"` on first call, then
  `"free"` on subsequent calls.

**Steps:**
1. `await find_tenant("user@example.com")` → returns
   `(zitadel_user_id, org_id)` (plan check passes).
2. Flip the plan mock to `"free"`.
3. `await find_tenant("user@example.com")` (immediately) → returns
   same cached tuple (cache still valid).
4. Advance clock by 61 seconds (via `freezegun` or
   `datetime.now` monkeypatch).
5. `await find_tenant("user@example.com")` → returns **None**
   (plan no longer in `SCRIBE_PLANS`).

**Pass condition:**
- Step 5 returns None (cache expired, re-lookup sees new plan).
- Test runs deterministically via clock-freezing — no real-time `sleep(60)`.

**Setup (Option B — invalidation hook variant, if chosen during /run):**
- Keep `CACHE_TTL = 300`.
- Added `invalidate_cache(email)` function.

**Steps (Option B):**
1-3 as above.
4. Call `invalidate_cache("user@example.com")`.
5. `await find_tenant("user@example.com")` → returns None.

**Pass condition (Option B):** Step 5 returns None, no clock
manipulation needed.

**Covers:** REQ-27.1, REQ-27.2, REQ-27.3.

---

## AC-28 — `/docs` double-gating on env + debug

**Scenario:** An operator accidentally sets `DEBUG=true` in a
production deployment.

**Test file:** `klai-portal/backend/tests/test_docs_gating.py`

**Setup:** A test fixture that builds the FastAPI app with parametrised
env vars.

**Steps:**

| `PORTAL_ENV` | `DEBUG` | Expected |
|---|---|---|
| `"development"` | `True` | `GET /docs` → 200 |
| `"development"` | `False` | `GET /docs` → 404 |
| `"staging"` | `True` | `GET /docs` → 200 |
| `"production"` | `False` | `GET /docs` → 404 |
| `"production"` | `True` | App **refuses to start**: `Settings()` raises `ValueError` with message mentioning `DEBUG=true` and `production` |

**Steps (detailed):**
1. For rows 1-4, instantiate the app and call `GET /docs`. Assert
   status code matches the expected column.
2. For row 5 (production + debug=true), wrap `Settings()` construction
   in a `pytest.raises(ValidationError)` (Pydantic v2 wraps the
   ValueError) and assert the message contains `"DEBUG"` and
   `"production"`.
3. Assert the same behaviour for `GET /openapi.json`:
   - Gated like `/docs`.
   - Returns 404 when either gate fails.

**Pass condition:**
- All 5 rows pass.
- `openapi.json` gating is symmetric with `/docs` gating.

**Covers:** REQ-28.1, REQ-28.2, REQ-28.3, REQ-28.4 (REQ-28.4 verified
manually by inspecting `deploy/docker-compose.yml`; no test fixture
for compose).

---

## Summary (v0.2.0)

> **Implementation status:** all 8 portal-slice ACs landed on
> branch `feature/SPEC-SEC-HYGIENE-001-portal-v03` (one commit per
> finding). Status detail, commit hashes, and test counts are tracked
> in `progress.md` § "portal-slice (HY-19..HY-28)". Findings #25 and
> #26 do not exist in this SPEC — the table jumps from #24 to #27.

| Finding | Test file | Requirements covered | Status |
|---|---|---|---|
| #19 | `test_signup_rate_limit.py` | REQ-19.1-19.5 | shipped |
| #20 | `test_validate_callback_url.py` | REQ-20.1-20.3 | shipped |
| #21 | `test_auth_bff_return_to.py` | REQ-21.1-21.4 | shipped |
| #22 | `test_signup_password_strength.py` | REQ-22.1-22.4 | shipped |
| #23 | `test_widget_config_docs.py` | REQ-23.1-23.3 | shipped |
| #24 | `test_widget_jwt_per_tenant.py` | REQ-24.1, 24.2, 24.4, 24.5 | shipped |
| #27 | `test_tenant_matcher_cache.py` | REQ-27.1-27.3 | shipped |
| #28 | `test_docs_gating.py` | REQ-28.1-28.4 | shipped |

Each file is a new regression test that would have flagged the
original finding. Per CLAUDE.md Rule 4 (Reproduction-First Bug Fix),
these tests are written BEFORE the fix in /run and confirmed failing
before any source code is changed.

---

# Internal-wave additions (2026-04-24)

One AC per v0.3.0 finding (HY-30 through HY-50). Grouped by service
to match the REQ organisation in `spec.md`.

All scribe / retrieval / connector tests use the same pytest-asyncio
+ httpx.AsyncClient pattern as portal-api tests unless noted.

---

## klai-connector hygiene ACs

### AC-30 — `HTTPException` NameError regression

**Scenario:** A client requests a connector ID that does not exist.
The service MUST return 404 (not 500).

**Test file:** `klai-connector/tests/test_connector_routes_not_found.py`

**Setup:**
- Fresh test DB with zero connector rows.
- Valid Zitadel JWT for an authenticated org.

**Steps:**
1. `GET /api/v1/connectors/00000000-0000-0000-0000-000000000000` →
   expect **404**, body `{"detail": "Connector not found"}`.
2. `PUT /api/v1/connectors/00000000-0000-0000-0000-000000000000`
   with a valid body → expect **404**, same detail.
3. `DELETE /api/v1/connectors/00000000-0000-0000-0000-000000000000`
   → expect **404**, same detail.
4. Insert a connector for a DIFFERENT org, then hit the same IDs
   from the first org's JWT. Expect **404** (cross-tenant still
   behaves as not-found, not 403/500).

**Pass condition:**
- All four steps return 404.
- No step returns 500.
- The regression test fails against the pre-fix codebase (verify by
  stashing the import line during first run).

**Ruff F821 sub-test:**
- In CI, ruff runs `F821` against `klai-connector/app/routes/`.
- A synthetic test in `klai-connector/tests/test_ruff_config.py`
  asserts `F821` is in the ruff `select` list for the service.

**Covers:** REQ-30.1, REQ-30.2, REQ-30.3.

### AC-31 — `/api/v1/compute-fingerprint` dead import

**Scenario:** Either the endpoint is removed entirely, or it works
end-to-end via a replacement adapter. Only one outcome is
acceptable.

**Test file:** `klai-connector/tests/test_compute_fingerprint.py`

**Branch A — endpoint removed:**
1. `POST /api/v1/compute-fingerprint` with any body → expect
   **404** OR **405** (FastAPI's response for unknown path).
2. `openapi.json` (in dev env) does NOT list `/compute-fingerprint`
   as a path.

**Branch B — endpoint rewired:**
1. Mock the replacement crawl4ai HTTP endpoint to return markdown
   content for a test URL.
2. `POST /api/v1/compute-fingerprint` with `{"url": "https://example.com"}`
   → expect **200** with `{"fingerprint": "<hex>", "word_count": >= 20}`.
3. Mock crawl4ai to return empty / < 20 words → expect **422**.
4. Mock crawl4ai to raise → expect **502** with
   `{"detail": "Crawl failed"}` (generic, no module names).
5. Assert no response body ever contains the string
   `app.adapters.webcrawler` or `ModuleNotFoundError`.

**Pass condition:**
- /run selects Branch A or B. Whichever is selected, ALL steps
  pass. If B, step 5 is the specific REQ-31.2 assertion.

**Covers:** REQ-31.1, REQ-31.2, REQ-31.3, REQ-31.4 (portal consumer
update is verified manually; no fixture).

### AC-32 — Connector per-org rate limit

**Scenario:** A single org cannot exceed 10 write requests per
minute or 60 read requests per minute.

**Test file:** `klai-connector/tests/test_connector_rate_limit.py`

**Setup:**
- Fresh Redis key namespace.
- Valid JWT for `org_id=1`.
- Fixture that can advance the simulated clock (freezegun or
  explicit `_now` monkey-patch).

**Steps (write limit):**
1. `POST /api/v1/connectors` 10 times in succession → all 201.
2. 11th `POST` within the same minute → expect **429** with
   `{"detail": "rate limit exceeded"}`.
3. Advance clock by 61 seconds.
4. 12th `POST` → expect 201 (limit reset).

**Steps (read limit):**
5. `GET /api/v1/connectors` 60 times → all 200.
6. 61st `GET` → expect **429**.
7. Advance clock by 61 seconds.
8. 62nd `GET` → expect 200.

**Fail-open sub-test:**
9. Monkey-patch Redis to raise `ConnectionError`.
10. `POST /api/v1/connectors` → expect 201 (fail open).
11. structlog event `connector_rate_limit_redis_unavailable`
    emitted.

**Cross-tenant sub-test:**
12. Hit the write limit from `org_id=1`.
13. Immediately `POST /api/v1/connectors` from `org_id=2` → expect
    201 (per-org isolation works).

**Pass condition:** All 13 steps pass.

**Covers:** REQ-32.1, REQ-32.2, REQ-32.3, REQ-32.4.

**Implementation note (default deviation, ratified during /run):**
The shipped defaults in `app/core/config.py` are **120 reads/min/org**
and **30 writes/min/org** — higher than the SPEC literal (60/10) at
REQ-32.2. Rationale: industry references for admin/management APIs
cluster around 75-1200 req/min/org (Auth0 Management API: 120/min
free tier, Heroku Platform API: 75/min, Slack Admin Oversight: 1200/min);
10 writes/min would pinch legitimate admin onboarding flows that
configure 5+ connectors back-to-back, especially with React strict
mode double-firing in dev. The values are env-tunable via
`CONNECTOR_RL_READ_PER_MIN` / `CONNECTOR_RL_WRITE_PER_MIN` and the
acceptance test itself sets them to the SPEC literal (60/10) so it
exercises the SPEC-described boundaries verbatim — no test rewrite is
needed if defaults change. Step 1 above ("10 POSTs in succession")
exercises the 10-write boundary regardless of prod default.

---

## klai-scribe hygiene ACs

### AC-33 — Audio path traversal rejection

**Scenario:** Crafted `user_id` cannot escape the audio directory.

**Test file:** `klai-scribe/scribe-api/tests/test_audio_path_safety.py`

**Steps (parametrised on `user_id` input):**

| Input | Expected |
|---|---|
| `"269462541789364226"` (normal) | returns a valid Path under base |
| `"../../../etc/passwd"` | raises `ValueError` |
| `"../evil"` | raises `ValueError` |
| `"/absolute/path"` | raises `ValueError` |
| `"..\\win"` | raises `ValueError` |
| `"user.with.dot"` | raises `ValueError` (defense-in-depth; HY-34 regex rejects anyway) |
| `""` (empty) | raises `ValueError` |

**Pass condition:**
- Normal input returns a Path whose `.resolve().is_relative_to(base)`
  is True.
- Every crafted input raises `ValueError`.
- No input writes or reads a file outside `base`.

**Covers:** REQ-33.1, REQ-33.2, REQ-33.3.

### AC-34 — Zitadel `sub` charset whitelist

**Scenario:** A JWT with a malformed `sub` is rejected at the auth
layer before any downstream handler sees it.

**Test file:** `klai-scribe/scribe-api/tests/test_auth_sub_validation.py`

**Steps:**
1. Generate a test JWT with `sub="269462541789364226"` (normal) →
   auth passes, returns AuthContext.
2. JWT with `sub="../evil"` → auth returns **401**, detail
   `"invalid token"`.
3. JWT with `sub="user with spaces"` → **401**.
4. JWT with `sub="a" * 65` (too long) → **401**.
5. JWT with `sub=""` (empty) → **401**.
6. JWT with `sub="user_id-42"` (valid alphanumeric + _ + -) → auth
   passes.
7. JWT with `sub="uuid-with-dashes-a1b2c3d4-e5f6"` → auth passes.

**Pass condition:**
- Legitimate format(s) pass; malformed rejects at auth layer.
- Downstream handlers NEVER see a malformed sub.

**Covers:** REQ-34.1, REQ-34.2, REQ-34.3, REQ-34.4.

### AC-35 — Stranded `processing` row reaper

**Scenario:** A transcription row stuck in `status="processing"`
for more than 30 minutes is automatically flipped to `failed` on
worker startup.

**Test file:** `klai-scribe/scribe-api/tests/test_stranded_reaper.py`

**Setup:**
- Test DB with a `transcriptions` table.
- Config: `SCRIBE_STRANDED_TIMEOUT_MIN=30`.

**Steps:**
1. Insert a row with `status="processing"`, `started_at=NOW - 35
   minutes`.
2. Insert a row with `status="processing"`, `started_at=NOW - 10
   minutes` (under threshold, should NOT be reaped).
3. Insert a row with `status="complete"`, any start time (should
   NOT be touched).
4. Call `reap_stranded(session)`.
5. Assert row 1 is now `status="failed"`,
   `error_reason="worker_restart_stranded"`.
6. Assert row 2 is still `status="processing"`.
7. Assert row 3 is still `status="complete"`.
8. structlog event `scribe_stranded_recovered` emitted once for
   row 1, with `txn_id` and `age_minutes>=35`.

**Pass condition:** Steps 5-8 all hold.

**Audio preservation sub-test:**
9. Pre-reaping, row 1's `audio_path="/data/audio/user/txn.wav"`.
10. After reaping, `audio_path` is UNCHANGED (REQ-35.3 — no delete).

**Covers:** REQ-35.1, REQ-35.2, REQ-35.3, REQ-35.4.

### AC-36 — Finalize race order + janitor

**Scenario:** A crash between DB commit and disk delete does NOT
leave an orphan file on disk indefinitely.

**Test file:** `klai-scribe/scribe-api/tests/test_finalize_order.py`

**Steps (order reversal, REQ-36.1):**
1. Set up a transcription row with `audio_path` pointing at a
   real test file.
2. Call `finalize_success(txn)`.
3. Assert `delete_audio` was called BEFORE `session.commit`.
4. Assert on success, file is gone AND DB row has `audio_path=None`.

**Crash simulation (REQ-36.2):**
5. Monkey-patch `delete_audio` to succeed.
6. Monkey-patch `session.commit` to raise (simulating crash after
   delete but before commit).
7. Call `finalize_success(txn)` → expect the exception to propagate.
8. Assert file is gone (delete succeeded before the crash).
9. Assert DB still has the original `audio_path` (commit never
   happened).

**Janitor sub-test (REQ-36.2):**
10. Write an orphan file at `/data/audio/user/orphan.wav`. No DB
    reference.
11. Set `SCRIBE_JANITOR_GRACE_HOURS=0` for the test.
12. Run `janitor.sweep()`.
13. Assert the orphan file is gone.
14. structlog event `scribe_janitor_orphan_deleted` emitted.

**Grace period sub-test (REQ-36.2):**
15. Repeat step 10 with `SCRIBE_JANITOR_GRACE_HOURS=24`.
16. Run janitor → file is NOT deleted (under grace).

**Covers:** REQ-36.1, REQ-36.2, REQ-36.3, REQ-36.4.

### AC-37 — Whisper URL allowlist + sanitised `/health`

**Scenario:** Misconfigured `whisper_server_url` is rejected at
startup; `/health` errors do not leak internal URLs.

**Test file:** `klai-scribe/scribe-api/tests/test_health_safety.py`

**Settings validator sub-test (REQ-37.1):**
1. `Settings(whisper_server_url="http://whisper:8000")` → succeeds.
2. `Settings(whisper_server_url="http://localhost:8000")` → succeeds.
3. `Settings(whisper_server_url="http://voys.getklai.com:8000")` →
   succeeds.
4. `Settings(whisper_server_url="http://evil.com/")` → raises
   `ValidationError`.
5. `Settings(whisper_server_url="http://169.254.169.254/")` → raises.
6. `Settings(whisper_server_url="file:///etc/passwd")` → raises.

**`/health` sanitisation sub-test (REQ-37.2):**
7. Mock httpx to raise `ConnectError("http://whisper:8000/health:
   connection refused")`.
8. `GET /health` → expect **503** body
   `{"status": "error", "detail": "whisper unreachable"}`.
9. Assert response body does NOT contain `"whisper:8000"` or
   `"ConnectError"` or any other internal detail.
10. structlog output DOES contain the full exception with
    `exc_info`.

**Pass condition:** All 10 steps hold.

**Covers:** REQ-37.1, REQ-37.2, REQ-37.3.

### AC-38 — CORS regex MX:WARN annotation (docs-only)

**Scenario:** The scribe CORS config carries an explicit defense-in-
depth annotation.

**Test file:** `klai-scribe/scribe-api/tests/test_cors_annotation.py`

**Steps:**
1. Read `klai-scribe/scribe-api/app/main.py` as text.
2. Locate the `app.add_middleware(CORSMiddleware, ...)` call.
3. Assert the line(s) immediately preceding that call contain
   `@MX:WARN` AND `@MX:REASON`.
4. Assert the `@MX:REASON` text mentions "back-end-only" OR
   "not browser-reachable".
5. Assert the `@MX:REASON` text references
   `SPEC-SEC-HYGIENE-001 REQ-38` OR `SPEC-SEC-CORS-001`.

**Pass condition:** All 5 assertions hold.

**Covers:** REQ-38.1, REQ-38.2, REQ-38.3.

---

## klai-retrieval-api hygiene ACs

### AC-39 — `/health` event-loop + topology safety

**Scenario:** `/health` does not block the event loop and does not
leak internal topology on dependency failure.

**Test file:**
`klai-retrieval-api/tests/test_health_safety.py`

**Event-loop sub-test (REQ-39.1):**
1. Monkey-patch `db.connection.ping` to `time.sleep(1.0)` (sync
   sleep simulating a slow FalkorDB).
2. Fire `GET /health` + concurrent `GET /` (a cheap endpoint).
3. Assert the cheap endpoint's response time is <100 ms regardless
   of /health taking ~1s (no event-loop blocking).

**Topology sub-test (REQ-39.2):**
4. Monkey-patch httpx to raise
   `ConnectError("http://172.18.0.1:7997: connection refused")`.
5. `GET /health` → 503.
6. Assert response body's TEI field is `"error"` (generic), NOT
   `"error: ..."` with URL.
7. Assert structlog output contains the full exception
   (`exc_info=True`).

**Status code preservation (REQ-39.4):**
8. With one dep down, `/health` returns 503 (as before). With all
   deps OK, returns 200.

**Pass condition:** All 8 steps hold.

**Covers:** REQ-39.1, REQ-39.2, REQ-39.3, REQ-39.4.

### AC-40 — `_pending` bounded

**Scenario:** Under a flood, the event set does not exceed the cap.

**Test file:**
`klai-retrieval-api/tests/test_events_bounded.py`

**Setup:**
- `RETRIEVAL_EVENTS_MAX_PENDING=1000`.
- Monkey-patch `_emit` to hang (so tasks don't self-discard).

**Steps:**
1. Call `emit_event(...)` 2000 times in a tight loop.
2. Assert `len(_pending) <= 1000`.
3. Assert `retrieval_events_dropped_total` counter == 1000.
4. structlog event `retrieval_events_cap_hit` emitted (at least
   once; rate-limited per REQ-40.2).

**Recovery sub-test:**
5. Release the `_emit` hang so pending tasks complete.
6. After all tasks drain, `len(_pending) == 0`.
7. Subsequent `emit_event` calls proceed normally (cap resets).

**Pass condition:** Steps 2-7 all hold.

**Covers:** REQ-40.1, REQ-40.2, REQ-40.3.

### AC-41 — X-Request-ID / X-Org-ID length cap

**Scenario:** Oversized or malformed trace headers do not pollute
log context.

**Test file:**
`klai-retrieval-api/tests/test_request_id_validation.py`

**Steps (parametrised):**

| `X-Request-ID` input | Expected bound value |
|---|---|
| `"abc-123"` | `"abc-123"` |
| `"A" * 128` | `"A" * 128` (exactly at cap, accepted) |
| `"A" * 129` | server-generated UUID (over cap, replaced) |
| `"<script>"` | UUID (fails charset) |
| `"\x1b[31mred\x1b[0m"` | UUID (fails charset) |
| `""` (empty) | UUID |
| absent (no header) | UUID |

**X-Org-ID parametrised:**

| Input | Expected |
|---|---|
| `"42"` | `"42"` |
| `"0"` | `"0"` |
| `"99999999999999999999"` | `"99999999999999999999"` (20 digits) |
| `"1234567890123456789012"` | DROPPED from context |
| `"abc"` | DROPPED |
| `"-5"` | DROPPED |

**Pass condition:**
- Every row's bound context value matches expected.
- Log records (captured via fixture) show the expected bound values.

**Cross-service symmetric sub-test (REQ-41.4):**
- A meta-test in `klai-portal/backend/tests/test_request_id_validation.py`
  covers the same matrix on portal-api's middleware. Same for every
  other service in scope. Grep-based CI check ensures all services
  implement the same regex.

**Covers:** REQ-41.1, REQ-41.2, REQ-41.3, REQ-41.4.

### AC-42 — Rate-limit fail-open annotation (docs-only + log hardening)

**Scenario:** The fail-open branch is explicitly annotated and the
log statement captures the traceback.

**Test file:**
`klai-retrieval-api/tests/test_rate_limit_annotation.py`

**Steps:**
1. Read `retrieval_api/services/rate_limit.py` as text.
2. Locate the `except Exception as exc:` inside `check_limit`.
3. Assert `@MX:WARN` and `@MX:REASON` are present on preceding
   comment lines.
4. Assert `@MX:REASON` text contains `"SPEC-SEC-HYGIENE-001 REQ-42"`
   OR `"SPEC-RETRIEVAL-RL-FAILCLOSED-001"`.
5. Assert the `logger.warning` call uses `exc_info=True` (not
   `error=str(exc)`).

**Behavioural no-change test:**
6. Monkey-patch redis to raise.
7. Call `check_limit(...)` → returns True (fail open, unchanged).

**Pass condition:** All 7 steps hold.

**Covers:** REQ-42.1, REQ-42.2.

### AC-43 — TRY antipattern fixes

**Scenario:** `search.py` no longer lists `TimeoutError` inside an
`Exception` tuple and no longer uses `error=str(exc)`.

**Test file:**
`klai-retrieval-api/tests/test_search_error_handling.py`

**Grep-based static assertions:**
1. Read `retrieval_api/services/search.py` as text.
2. Assert NO occurrence of `except (TimeoutError, Exception)` in
   the file.
3. Assert NO occurrence of `error=str(exc)` in the file (TRY401).
4. Assert `logger.exception(...)` OR `logger.warning(..., exc_info=True)`
   appears in every `except` block that logs.

**Behavioural test:**
5. Monkey-patch TEI client to raise `TimeoutError("slow")`.
6. Call the affected search function.
7. Assert a log record with level WARNING (or ERROR) was emitted
   WITH a traceback attached (captured via caplog fixture).

**Ruff sub-test:**
8. `uv run ruff check retrieval_api/services/search.py` exits 0.

**Pass condition:** All 8 steps hold.

**Covers:** REQ-43.1, REQ-43.2, REQ-43.3, REQ-43.4.

### AC-44 — JWKS worker-DoS landmine closed

**Scenario:** Under the dev-bypass path, a malicious Bearer token
returns 401 immediately (not 20s later). Under the prod path,
invalid JWKS URL prevents startup.

**Test file:**
`klai-retrieval-api/tests/test_auth_jwks_landmine.py`

**Short-circuit sub-test (REQ-44.1, REQ-44.5):**
1. Set `settings.jwt_auth_enabled=False`.
2. Send `GET /retrieve` with `Authorization: Bearer x`.
3. Measure response time from request to 401 → assert < 100 ms.
4. Assert httpx was NOT called (no JWKS fetch attempt).

**Empty-URL startup sub-test (REQ-44.2, REQ-44.6):**
5. Create Settings with `jwt_auth_enabled=True, jwks_url=""`.
6. Assert `ValidationError` on Settings instantiation.

**JWKS timeout cap (REQ-44.3):**
7. With `jwt_auth_enabled=True, jwks_url="http://slow.example.com/jwks"`,
   monkey-patch httpx to take 5s to first response.
8. Auth middleware should timeout at 3s max (not 10s).

**JWKS cache (REQ-44.4):**
9. First request with valid Bearer → JWKS fetched once.
10. Immediately repeat request → JWKS NOT fetched again (cache hit).
11. Advance clock 16 minutes.
12. Third request → JWKS fetched (cache expired).

**Pass condition:** All 12 steps hold.

**Covers:** REQ-44.1, REQ-44.2, REQ-44.3, REQ-44.4, REQ-44.5,
REQ-44.6.

---

## klai-knowledge-mcp hygiene ACs

### AC-45 — DNS-rebinding annotation (docs-only)

**Scenario:** The FastMCP DNS-rebinding flag carries a warning
annotation; Caddyfile lists MCP as non-public.

**Test file:**
`klai-knowledge-mcp/tests/test_mcp_hygiene.py`

**Steps:**
1. Read `klai-knowledge-mcp/main.py` as text.
2. Locate the line containing `enable_dns_rebinding_protection=False`.
3. Assert `@MX:WARN` appears on a preceding comment line.
4. Assert `@MX:REASON` text includes "not internet-reachable" or
   "MCP is not public" AND references SPEC-SEC-HYGIENE-001 REQ-45.
5. Read `deploy/caddy/Caddyfile` as text.
6. Assert it contains a comment listing `klai-knowledge-mcp` as a
   service that is NOT internet-reachable.

**Pass condition:** All 6 assertions hold.

**Covers:** REQ-45.1, REQ-45.2, REQ-45.3.

### AC-46 — `page_path` encoding rejection (stub-level)

**Scenario:** Conservative rejection of URL-encoded and
fullwidth-encoded path traversal attempts.

**Test file:**
`klai-knowledge-mcp/tests/test_page_path_validation.py`

**Steps (parametrised):**

| Input | Expected |
|---|---|
| `"docs/section/page"` (normal) | accepted |
| `"../etc/passwd"` | rejected |
| `"%2e%2e/passwd"` | rejected (% char triggers reject per REQ-46.1) |
| `"%2E%2E/passwd"` | rejected |
| `"%2f%2fevil"` | rejected |
| `"．．/etc/passwd"` (fullwidth) | rejected (NFKC normalises to `..`) |
| `"docs/sub\\evil"` | rejected (backslash) |
| `"/absolute"` | rejected (leading /) |
| `"docs/has%20space"` | rejected (% char) |
| `"docs/has-dash"` | accepted |

**Pass condition:**
- All rejected inputs raise `ValueError`.
- All accepted inputs pass.
- A note in the test module documents that full encoding coverage
  (overlong UTF-8, etc.) lands in the split follow-up SPEC per
  REQ-46.3.

**Covers:** REQ-46.1 (stub). REQ-46.2/REQ-46.3 are tracked as a
follow-up research spike — no fixture here.

### AC-47 — MCP tool rate-limit

**Scenario:** Tool calls exceed the cap and receive a JSON-RPC
rate-limit error.

**Test file:**
`klai-knowledge-mcp/tests/test_mcp_rate_limit.py`

**Setup:**
- `MCP_RL_READ_PER_MIN=60`.
- `MCP_RL_WRITE_PER_MIN=30`.
- Mock Zitadel identity with `user_id="test-user-123"`.

**Read limit sub-test:**
1. Call `list_sources` 60 times via the MCP test harness → all 60
   succeed.
2. 61st call → JSON-RPC error `-32001` "rate limit exceeded".

**Write limit sub-test:**
3. Call a write tool (e.g. `add_source`) 30 times → all succeed.
4. 31st call → JSON-RPC error `-32001`.

**Per-user isolation sub-test:**
5. Hit the limit for `user_id="user-A"`.
6. Immediately call the same tool as `user_id="user-B"` → succeeds.

**Identity absent sub-test (REQ-47.2):**
7. Call a tool with no authenticated identity → JSON-RPC error
   `-32000` "authentication required". Actual behaviour matches
   the current MCP auth layer; this test documents the sequence.

**Fail-open sub-test (REQ-47.4):**
8. Monkey-patch Redis to raise.
9. Call `list_sources` 100 times → all succeed (fail open).
10. structlog warning emitted.

**Error body sub-test (REQ-47.3):**
11. When rate-limited, error `message` field is exactly `"rate
    limit exceeded"` — does not echo the limit value, the window,
    or the current rate.

**Pass condition:** Steps 2, 4, 5, 6, 8-11 all hold.

**Covers:** REQ-47.1, REQ-47.2, REQ-47.3, REQ-47.4, REQ-47.5.

**Implementation note (slice-deviation, knowledge-mcp /run 2026-04-29):**
HY-47 is NOT shipped at the MCP layer. Two reasons drove the move:

1. The SPEC text talks about `list_sources`, `query_kb`, `get_page_content`,
   and `add_source` tools — none of which exist in `klai-knowledge-mcp`
   today. The current MCP exposes only three write tools
   (`save_personal_knowledge`, `save_org_knowledge`, `save_to_docs`). The
   read sub-test (steps 1-2) cannot be wired against a tool that does
   not exist; building a synthetic read tool just to satisfy the SPEC is
   scope creep that ships a feature with no callers.
2. The structurally correct location for write rate-limiting is one layer
   deeper, at `klai-knowledge-ingest`. That service is the choke point
   every save eventually flows through (today: portal-api + MCP; future:
   any new caller). A throttle there protects every path including
   future ones, keys on the same identity tuple (forwarded via
   `X-User-ID` / `X-Org-ID` headers from MCP), and matches the pattern
   already used for `partner_rate_limit.py` in portal-api.

Action: HY-47 is deferred from this SPEC and tracked as
`SPEC-INGEST-RATELIMIT-001` (write-rate-limit on
`POST /ingest/v1/document`, ZSET sliding-window keyed on
`(org_id, user_id)`, fail-open on Redis outage — same shape as
`klai-connector/app/services/rate_limit.py` / SPEC-API-001 REQ-2.4).
The AC tests in `tests/test_mcp_rate_limit.py` are NOT created in
this slice. When the follow-up SPEC ships, its acceptance file will
adapt the matrix above to the real ingest endpoint surface.

### AC-48 — Personal-KB slug annotation (docs-only)

**Scenario:** The personal-KB slug derivation site carries an
MX:NOTE pointing at SPEC-SEC-IDENTITY-ASSERT-001.

**Test file:**
`klai-knowledge-mcp/tests/test_personal_kb_annotation.py`

**Steps:**
1. Read `klai-knowledge-mcp/main.py` as text.
2. Locate the line containing `kb_slug = f"personal-{identity.user_id}"`.
3. Assert `@MX:NOTE` on a preceding comment line.
4. Assert the note text mentions SPEC-SEC-IDENTITY-ASSERT-001.
5. Assert the note text mentions SPEC-SEC-HYGIENE-001 REQ-48.
6. Assert the slug format is UNCHANGED (still `f"personal-{...}"`
   — REQ-48.2).

**Pass condition:** All 6 assertions hold.

**Covers:** REQ-48.1, REQ-48.2, REQ-48.3.

---

## klai-mailer hygiene ACs (defense-in-depth)

### AC-49 — Signature error taxonomy collapse

**Scenario:** All four signature-verification failure modes return
the same 401 body.

**Test file:**
`klai-mailer/tests/test_signature_oracle.py`

**Steps:**
1. POST to the mailer webhook with no `ZITADEL-Signature` header →
   expect 401, body `{"detail": "unauthorized"}`.
2. POST with `ZITADEL-Signature: malformed` → expect 401, same body.
3. POST with valid format but timestamp 1 hour old → expect 401,
   same body.
4. POST with valid format, fresh timestamp, but wrong HMAC → expect
   401, same body.
5. Assert response bodies for steps 1-4 are BYTE-IDENTICAL.
6. Assert structlog events for steps 1-4 contain distinct
   `verification_phase` values (`missing_header`, `malformed`,
   `timestamp_too_old`, `hmac_mismatch`).

**Pass condition:** Steps 5 and 6 both hold.

**Coverage dedup note:** if SPEC-SEC-MAILER-INJECTION-001 claims
this fix, AC-49 becomes "assertion only" in HYGIENE-001's close-out
PR — the test still exists (it's a regression check), but the
primary fix ships from the other SPEC.

**Covers:** REQ-49.1, REQ-49.2, REQ-49.3, REQ-49.4.

### AC-50 — v-field parser MX:NOTE (docs-only)

**Scenario:** The signature parser carries a tripwire annotation.

**Test file:**
`klai-mailer/tests/test_signature_parser_annotation.py`

**Steps:**
1. Locate the parser function (grep for `ZITADEL-Signature` header
   handling during /run).
2. Assert `@MX:NOTE` on a preceding comment line.
3. Assert the note text contains "v1-only" or "v2" AND
   "downgrade" or "REVISIT" AND references SPEC-SEC-HYGIENE-001
   REQ-50.
4. Assert no code change: the parser still accepts unknown `vN=`
   fields silently (REQ-50.2 — no behaviour change today).

**Pass condition:** All 4 assertions hold.

**Covers:** REQ-50.1, REQ-50.2, REQ-50.3, REQ-50.4.

---

## Summary (v0.3.0)

Original v0.2.0 ACs plus 21 new internal-wave ACs. Full table:

| Finding | Test file | Requirements covered |
|---|---|---|
| #19 | `test_signup_rate_limit.py` | REQ-19.1-19.5 |
| #20 | `test_validate_callback_url.py` | REQ-20.1-20.3 |
| #21 | `test_auth_bff_return_to.py` | REQ-21.1-21.4 |
| #22 | `test_signup_password_strength.py` | REQ-22.1-22.4 |
| #23 | `test_widget_config_docs.py` | REQ-23.1-23.3 |
| #24 | `test_widget_jwt_per_tenant.py` | REQ-24.1, 24.2, 24.4, 24.5 |
| #27 | `test_tenant_matcher_cache.py` | REQ-27.1-27.3 |
| #28 | `test_docs_gating.py` | REQ-28.1-28.4 |
| HY-30 | `klai-connector/tests/test_connector_routes_not_found.py` | REQ-30.1-30.3 |
| HY-31 | `klai-connector/tests/test_compute_fingerprint.py` | REQ-31.1-31.4 |
| HY-32 | `klai-connector/tests/test_connector_rate_limit.py` | REQ-32.1-32.4 |
| HY-33 | `klai-scribe/.../tests/test_audio_path_safety.py` | REQ-33.1-33.3 |
| HY-34 | `klai-scribe/.../tests/test_auth_sub_validation.py` | REQ-34.1-34.4 |
| HY-35 | `klai-scribe/.../tests/test_stranded_reaper.py` | REQ-35.1-35.4 |
| HY-36 | `klai-scribe/.../tests/test_finalize_order.py` | REQ-36.1-36.4 |
| HY-37 | `klai-scribe/.../tests/test_health_safety.py` | REQ-37.1-37.3 |
| HY-38 | `klai-scribe/.../tests/test_cors_annotation.py` | REQ-38.1-38.3 |
| HY-39 | `klai-retrieval-api/tests/test_health_safety.py` | REQ-39.1-39.4 |
| HY-40 | `klai-retrieval-api/tests/test_events_bounded.py` | REQ-40.1-40.3 |
| HY-41 | `klai-retrieval-api/tests/test_request_id_validation.py` | REQ-41.1-41.4 |
| HY-42 | `klai-retrieval-api/tests/test_rate_limit_annotation.py` | REQ-42.1-42.2 |
| HY-43 | `klai-retrieval-api/tests/test_search_error_handling.py` | REQ-43.1-43.4 |
| HY-44 | `klai-retrieval-api/tests/test_auth_jwks_landmine.py` | REQ-44.1-44.6 |
| HY-45 | `klai-knowledge-mcp/tests/test_mcp_hygiene.py` | REQ-45.1-45.3 |
| HY-46 | `klai-knowledge-mcp/tests/test_page_path_validation.py` | REQ-46.1 (stub) |
| HY-47 | `klai-knowledge-mcp/tests/test_mcp_rate_limit.py` | REQ-47.1-47.5 |
| HY-48 | `klai-knowledge-mcp/tests/test_personal_kb_annotation.py` | REQ-48.1-48.3 |
| HY-49 | `klai-mailer/tests/test_signature_oracle.py` | REQ-49.1-49.4 |
| HY-50 | `klai-mailer/tests/test_signature_parser_annotation.py` | REQ-50.1-50.4 |

Every test file is new. Each is a regression test that would have
flagged the original finding. Per CLAUDE.md Rule 4
(Reproduction-First Bug Fix), the tests are written BEFORE the fix
in /run and confirmed failing before any source code is changed.

Stub note: HY-46 only has stub-level AC coverage (REQ-46.1).
Detailed REQ-46.2 and REQ-46.3 test matrix ships with the follow-up
SPEC after the klai-docs route-handler audit.

