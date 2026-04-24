# Acceptance Criteria — SPEC-SEC-INTERNAL-001

EARS-format acceptance tests that MUST pass before this SPEC is considered
complete. Each test maps to one or more REQ-N.M items in `spec.md`. Every
test is self-contained and does not rely on SPEC-SEC-005 passing, EXCEPT
AC-2 (FLUSHALL replacement) and AC-5 (fail-mode) — those explicitly build
on the SPEC-SEC-005-hardened handler (per REQ-7.4).

## AC-1: Constant-time token comparison in taxonomy.py

**REQ-1.1, REQ-1.3**

- **WHEN** `klai-portal/backend/app/api/taxonomy.py:399` `_require_internal_token`
  is invoked with a correct Authorization header **THE** function **SHALL**
  return `None` without raising, identical pre- and post-fix.
- **WHEN** it is invoked with an incorrect Authorization header **THE**
  function **SHALL** raise `HTTPException(401)`.
- **WHEN** `settings.internal_secret` is empty **THE** function **SHALL**
  raise `HTTPException(503)` before any comparison runs.

### AC-1.1: Timing benchmark

- **WHEN** 10 000 validations are run with a **valid** token AND 10 000
  with a **near-valid** token (identical length, first byte different)
  **THE** absolute difference between the two sample means **SHALL** be
  within the 2·σ jitter envelope of `hmac.compare_digest` on a 64-byte
  comparison on the benchmark host.
- **Verification harness**: `pytest -m benchmark tests/api/test_taxonomy_internal_timing.py`
  using `pytest-benchmark`. The test is advisory on CI (timing on GitHub
  runners is too noisy) and enforced locally with
  `MOAI_BENCHMARK_STRICT=1`. An intentional regression — reverting to
  `!=` string equality — SHALL produce a mean delta at least 5× larger
  than the baseline. The test records both numbers in its output.
- **Accepted baseline delta (local)**: under 200 ns on a modern M-series
  Mac or a recent x86-64 host; documented in the test docstring.

### AC-1.2: ast-grep rule blocks regressions

- **WHEN** a PR introduces `token != expected` (or `==`) where `expected`
  or `token` variable name matches `(?i)(secret|internal_token|bearer_token|api_key)`
  **THE** `ast-grep/action` job in `.github/workflows/portal-api.yml`
  **SHALL** fail with exit code non-zero AND **THE** job log **SHALL**
  contain the message "Use `hmac.compare_digest` for secret/token
  comparisons — see SPEC-SEC-INTERNAL-001 REQ-1".
- Verification: a test fixture PR in `.github/test-fixtures/sec-internal-001/`
  contains the forbidden pattern; the ast-grep rule run against it must
  exit non-zero.

## AC-2: FLUSHALL is never called by any HTTP-reachable handler

**REQ-2.1, REQ-2.2, REQ-2.4**

### AC-2.1: Source-level assertion

- **WHEN** the entire `klai-portal/backend/app/` tree is scanned **THE**
  string `flushall(` **SHALL NOT** appear in any file under `api/`,
  `services/`, or `core/`. Enforcement: a `grep -Rn 'flushall(' klai-portal/backend/app/api klai-portal/backend/app/services klai-portal/backend/app/core`
  step in CI that fails on any match.
- Historical reference (`.claude/rules/klai/platform/docker-socket-proxy.md`)
  may still mention `FLUSHALL` in documentation; the CI check scopes to
  code directories only.

### AC-2.2: Runtime assertion via Redis MONITOR

- **WHEN** the test harness issues `POST /internal/librechat/regenerate`
  with a valid Authorization header AND a Redis test instance streaming
  commands via `MONITOR` **THE** recorded command log **SHALL NOT**
  contain the literal command `FLUSHALL` (case-insensitive).
- **WHEN** the same request is issued **THE** command log **SHALL** contain
  at least one `SCAN` command AND at least one `UNLINK` command whose key
  argument matches the configured pattern (`configs:*` by default per
  REQ-2.3).

### AC-2.3: Targeted invalidation does not destroy unrelated keys

- **WHEN** Redis contains pre-seeded keys `internal_rl:1.2.3.4`,
  `partner_rl:abc`, `sso_cache:user42`, `configs:librechat-config`, and
  `configs:librechat-config:acme` **AND** the regenerate endpoint is
  called **THE** `internal_rl:`, `partner_rl:`, and `sso_cache:` keys
  **SHALL** still exist after the call **AND THE** two `configs:*` keys
  **SHALL** have been removed.

### AC-2.4: Partial Redis failure does not break the response contract

- **WHEN** the Redis UNLINK call raises `RedisError` halfway through the
  invalidation **THE** endpoint **SHALL** return HTTP 200 with the
  `errors` list containing a string matching
  `^redis-cache-invalidation: .*` **AND** the response body **SHALL**
  still list every tenant that had its yaml regenerated on disk.
- **THE** structlog output **SHALL** contain exactly one entry with
  `event="librechat_cache_invalidation_failed"` and `exc_info` populated.
- **THE** container-restart step (REQ-2.5) **SHALL** still execute for
  every tenant that had its yaml regenerated.

## AC-3: BFF proxy strips client-supplied X-Internal-Secret

**REQ-3.1, REQ-3.2, REQ-3.3, REQ-3.4, REQ-3.5**

### AC-3.1: Header is not forwarded upstream

- **WHEN** the test harness sends `GET /api/scribe/healthz` with a valid
  portal session cookie AND a client-supplied
  `X-Internal-Secret: attacker-guess` header **THE** upstream mock at
  scribe-api **SHALL** receive a request whose headers do NOT contain
  any key matching `(?i)x-internal-secret` — checked by asserting
  `"x-internal-secret" not in {k.lower() for k in upstream_request.headers}`.
- **AND THE** upstream mock **SHALL** still receive
  `Authorization: Bearer <session.access_token>` (portal-injected, REQ-3.4).

### AC-3.2: Regex catch-all covers forward-compatible names

- **WHEN** a client sends any of the following headers — `X-Klai-Internal-Secret`,
  `x-klai-internal-foo`, `Internal-Auth-Bar`, `Internal-Token-Baz` —
  **THE** upstream mock **SHALL NOT** receive any header matching that
  regex.
- **WHEN** a client sends legitimate headers — `X-Request-ID`,
  `X-Forwarded-For`, `X-Real-IP`, `X-Custom-Business-Header`,
  `Accept-Language`, `User-Agent` — **THE** upstream mock **SHALL**
  receive each of them unchanged.

### AC-3.3: Blocked-injection log entry

- **WHEN** a client attempts to inject an X-Internal-Secret header **THE**
  portal-api **SHALL** emit exactly one structlog entry with
  `event="proxy_header_injection_blocked"`, `header="x-internal-secret"`,
  `service="scribe"` **AND** the log entry **SHALL NOT** contain the
  header value.

## AC-4: Response-body sanitizer redacts known secrets

**REQ-4.1, REQ-4.2, REQ-4.3, REQ-4.4, REQ-4.6**

### AC-4.1: Secret substring is replaced

- **WHEN** `sanitize_response_body` is called with an `httpx.Response`
  whose body is
  `f"gRPC error: invalid token {settings.internal_secret} for user mark@voys.nl"`
  **THE** returned string **SHALL** be equal to
  `"gRPC error: invalid token <redacted> for user mark@voys.nl"` —
  the user email is NOT redacted (not a secret), the secret IS redacted,
  no other bytes change.

### AC-4.2: Output is truncated to max_len

- **WHEN** `sanitize_response_body(resp)` is called with a 10 000-byte
  body **THE** returned string **SHALL** have length <= 512.

### AC-4.3: Redaction counter logged

- **WHEN** a sanitizer call redacts at least one occurrence **THE** log
  stream **SHALL** contain exactly one `event="response_body_sanitized"`
  entry with `redaction_count` equal to the number of replacements and
  `original_length` equal to the pre-truncation body length.

### AC-4.4: Idempotent on empty / None

- **WHEN** called with `None` **THE** utility **SHALL** return `""`
  without raising.
- **WHEN** called with a response whose body is empty **THE** utility
  **SHALL** return `""` without emitting the sanitized log entry.

### AC-4.5: All call sites rewritten

- **WHEN** `grep -Rn 'exc\.response\.text' klai-portal/backend/app/`
  is run after the SPEC lands **THE** output **SHALL** be empty for
  all files under `app/api/` and `app/services/` EXCEPT inside
  helper modules that themselves pass through `sanitize_response_body`.
- A CI check in `.github/workflows/portal-api.yml` enforces this.

### AC-4.6: No leakage to VictoriaLogs

- **WHEN** a contrived upstream returns a body containing
  `settings.internal_secret` AND the call site is one of the 26 sites
  rewritten by REQ-4.4 AND the resulting log line is captured in
  structured form **THE** captured log record **SHALL NOT** contain
  the verbatim secret value.

## AC-5: Rate-limit fail-mode behaves per config

**REQ-5.1, REQ-5.2, REQ-5.3, REQ-5.4**

### AC-5.1: Fail-closed default returns 503

- **WHEN** `settings.internal_rate_limit_fail_mode == "closed"`
  AND `get_redis_pool()` returns `None`
  AND a valid internal-authenticated request hits `/internal/user-language`
  **THE** response **SHALL** be HTTP 503 with detail
  `"Internal rate limit backend unavailable"`.
- **AND THE** log stream **SHALL** contain exactly one entry with
  `event="internal_rate_limit_fail_closed"`, `caller_ip=<ip>`,
  `exc_info` populated.

### AC-5.2: Fail-closed on Redis exception

- **WHEN** `settings.internal_rate_limit_fail_mode == "closed"`
  AND `check_rate_limit` raises `ConnectionError`
  **THE** response **SHALL** be HTTP 503 with the same detail as AC-5.1.

### AC-5.3: Fail-open preserves SPEC-SEC-005 behaviour

- **WHEN** `settings.internal_rate_limit_fail_mode == "open"`
  AND `get_redis_pool()` returns `None`
  **THE** response **SHALL** be HTTP 200 (the existing handler runs as
  if rate-limit was disabled) **AND THE** log stream **SHALL** contain
  exactly one entry with `event="internal_rate_limit_redis_unavailable"`
  (the pre-SPEC key, unchanged).

### AC-5.4: Default applies when env var is unset

- **WHEN** `INTERNAL_RATE_LIMIT_FAIL_MODE` is absent from the environment
  **THE** `Settings` class **SHALL** resolve
  `internal_rate_limit_fail_mode == "closed"`.

### AC-5.5: Staged-rollout compatibility

- **WHEN** the SPEC-SEC-INTERNAL-001 code is deployed without the env
  var being flipped from `open` to `closed` **THE** rate-limit
  behaviour **SHALL** be byte-equivalent to the SPEC-SEC-005 baseline
  (fail-open, same log key). No deploy-time regressions.

## AC-6: SPEC-SEC-005 compatibility

**REQ-7.1, REQ-7.2, REQ-7.4**

- **WHEN** SPEC-SEC-005 acceptance tests AC-1 through AC-12 are run
  against the post-SEC-INTERNAL-001 code **THE** tests **SHALL** all
  pass unchanged. This SPEC does not regress any SPEC-SEC-005 criterion.
- **WHEN** the audit row for an internal call is inspected **THE**
  `details` JSONB column **SHALL** continue to contain only
  `caller_ip` and `method` (no sanitizer output bleeds into audit rows,
  per REQ-7.2).

## AC-7: No raw secrets in logs — end-to-end

Integration-level assertion combining REQ-3, REQ-4, REQ-1 fixes.

- **WHEN** an adversarial test client executes the sequence:
  1. Browser session authenticated to portal-frontend.
  2. Fetches `/api/scribe/foo` with
     `X-Internal-Secret: actual-correct-secret-copy-pasted-from-env`.
  3. scribe-api replies 400 with a body echoing the request headers
     (simulated behaviour).
  **THEN** the VictoriaLogs capture for the full request_id chain
  **SHALL NOT** contain the verbatim secret value in any field of any
  log entry from portal-api, scribe-api, or retrieval-api.
- Verified by grep against a captured log fixture.

## Test harness notes

- Redis MONITOR tests use a dedicated `fakeredis.aioredis.FakeRedis` with
  a command-trace extension at
  `klai-portal/backend/tests/fakeredis_trace.py` (new). Real Redis is not
  required; the trace captures every command issued against the fake.
- The BFF proxy tests use `respx` to mock scribe-api and assert on the
  outbound request's headers.
- The timing benchmark (AC-1.1) is advisory on CI and enforced locally;
  CI runs only the assertion-based tests.
- The ast-grep rule fixture PR (AC-1.2) lives at
  `.github/test-fixtures/sec-internal-001/regression.py` and is not
  imported by any production code.

---

## AC-8 (v0.3.0): Constant-time checks service-wide

**REQ-1.5, REQ-1.6, REQ-6 (extended scope)**

### AC-8.1: mailer inbound compare is constant-time

- **WHEN** `klai-mailer/app/main.py:182` `_validate_incoming_secret`
  (or equivalent) is invoked with a correct `X-Internal-Secret` header
  **THE** function **SHALL** return without raising, identical
  pre- and post-fix.
- **WHEN** it is invoked with an incorrect header **THE** function
  **SHALL** raise `HTTPException(401)`.
- **WHEN** `settings.internal_secret` is empty at startup **THE**
  process **SHALL NOT** start (per REQ-9.2 pairing).

### AC-8.2: ast-grep rule runs against every klai-* service tree

- **WHEN** CI runs on a PR that touches ANY klai service
  (portal-api, mailer, connector, scribe, knowledge-mcp) **THE**
  `ast-grep/action` step **SHALL** be present in each service's
  workflow file AND **SHALL** fail on any `!=` / `==` comparison
  where one operand's variable name matches the secret regex.
- **Verification**: fixture file
  `.github/test-fixtures/sec-internal-001/mailer_regression.py`
  contains `if secret != expected:` — ast-grep run against the mailer
  workflow must exit non-zero.

### AC-8.3: No sibling service has a regression at implementation time

- **WHEN** `ast-grep` is run across
  `klai-connector/`, `klai-scribe/`, `klai-knowledge-mcp/` with the
  `no-string-compare-on-secret.yml` rule **THE** output **SHALL** be
  empty (zero violations). Any hit blocks the SPEC from merging.

### AC-8.4: Timing benchmark extended to mailer

- Analog of AC-1.1 against mailer's `_validate_incoming_secret`.
  10 000 valid + 10 000 near-valid compares; mean delta within 2·σ of
  `hmac.compare_digest` baseline. Advisory on CI; enforced locally
  with `MOAI_BENCHMARK_STRICT=1`.

## AC-9 (v0.3.0): Empty outbound secrets fail-closed at startup

**REQ-9.1, REQ-9.2, REQ-9.3, REQ-9.4, REQ-9.5, REQ-9.7**

### AC-9.1: mailer refuses to start with empty WEBHOOK_SECRET

- **WHEN** the mailer container is started with `WEBHOOK_SECRET=""`
  AND all other required env vars present **THE** process **SHALL**
  exit non-zero within 5 seconds **AND** stderr **SHALL** contain a
  `pydantic.ValidationError` message mentioning `webhook_secret` and
  `min_length=8` (or equivalent).
- **Verification**: `docker run --rm -e WEBHOOK_SECRET= ... klai-mailer:test`;
  assert exit code non-zero and stderr regex match.

### AC-9.2: mailer refuses to start with empty INTERNAL_SECRET

- **WHEN** the mailer container is started with `INTERNAL_SECRET=""`
  AND `WEBHOOK_SECRET` non-empty **THE** process **SHALL** either
  exit non-zero at startup OR **SHALL** reject every `/internal/send`
  request at runtime with HTTP 503. The implementation SHALL choose
  the startup-refusal path when feasible.

### AC-9.3: connector refuses to start with empty KNOWLEDGE_INGEST_SECRET

- **WHEN** `KNOWLEDGE_INGEST_SECRET=""` **THE** connector process
  **SHALL** exit non-zero at startup with a
  `pydantic.ValidationError`.
- Same for `PORTAL_INTERNAL_SECRET=""`.

### AC-9.4: connector PortalClient never sends empty Bearer

- **WHEN** `PortalClient._headers()` is called in a test harness
  with `settings.portal_internal_secret == ""` **THE** call **SHALL**
  raise (not return) — verified by the startup validator running
  inside the test's Settings construction.
- **Regression test**: forcibly construct a `Settings` with empty
  secret via `Settings.model_construct()` (bypasses validation) and
  assert `PortalClient(settings)._headers()["Authorization"] != "Bearer "`.
  The test uses a sentinel value or asserts that no literal
  `"Bearer "` is ever emitted.

### AC-9.5: scribe-api refuses to start with empty KNOWLEDGE_INGEST_SECRET

- **WHEN** `KNOWLEDGE_INGEST_SECRET=""` **THE** scribe-api container
  **SHALL** exit non-zero at startup.
- **AND** `knowledge_adapter.py` outbound call SHALL inject the
  header unconditionally (no `if settings.knowledge_ingest_secret:`
  guard remains) — verified by grep: the guard string
  `if settings.knowledge_ingest_secret:` **SHALL NOT** appear in
  `klai-scribe/scribe-api/app/services/`.

### AC-9.6: knowledge-mcp refuses to start with empty secrets

- **WHEN** `KNOWLEDGE_INGEST_SECRET=""` OR `DOCS_INTERNAL_SECRET=""`
  **THE** knowledge-mcp module-level assertion **SHALL** fail at
  import time **AND** the ASGI app construction **SHALL** raise.
- Grep check: `if KNOWLEDGE_INGEST_SECRET:` and
  `if DOCS_INTERNAL_SECRET:` **SHALL NOT** appear in `main.py` after
  the fix lands.

### AC-9.7: CI boot-matrix test

- A GitHub Actions job `sec-internal-001-empty-secret-matrix`
  **SHALL** start each of the five services in a container with
  each secret env var set to `""` in turn, asserting non-zero exit
  for every row. The matrix is generated from a YAML manifest
  `.github/fixtures/sec-internal-001-boot-matrix.yaml` listing
  (service × env-var) pairs.

## AC-10 (v0.3.0): sync_run.error_details never persists raw secrets

**REQ-10.1, REQ-10.3, REQ-10.4**

### AC-10.1: Sanitizer runs before persistence

- **WHEN** `sync_engine` encounters an `httpx.HTTPStatusError` from
  knowledge-ingest whose body contains
  `settings.knowledge_ingest_secret` **AND** `sync_run.error_details`
  is subsequently persisted via SQLAlchemy **THE** stored JSONB
  **SHALL NOT** contain the verbatim secret substring.
- **Verification**: integration test with a real Postgres (testcontainers)
  + respx-mocked knowledge-ingest returning a 500 with the secret in
  the body. After the sync, query
  `SELECT error_details FROM connector.sync_runs WHERE id = ?` and
  assert `<redacted>` appears where the secret would have been.

### AC-10.2: No known-secret substring in any stored row

- **WHEN** the DB fixture is populated by running 10 failed syncs with
  diverse error bodies containing the full set of known outbound
  secrets (`KNOWLEDGE_INGEST_SECRET`, `PORTAL_INTERNAL_SECRET`,
  `GITHUB_APP_PRIVATE_KEY`, `ENCRYPTION_KEY`) **THE** scan
  `SELECT id FROM connector.sync_runs WHERE error_details::text ~ '<regex of known secret values>'`
  **SHALL** return zero rows.
- **Regex**: assembled at test time from
  `extract_secret_values(Settings())` — same source as the runtime
  sanitizer.

### AC-10.3: Forwarded error_details does not leak to portal UI

- **WHEN** the sanitized error_details is forwarded to portal via
  `report_sync_status` **AND** portal's connector-management UI
  renders the `error_details` column on `GET /connectors/<id>/runs`
  **THE** rendered response body **SHALL NOT** contain any known
  secret substring.
- **Verification**: Playwright-free integration test — portal-api
  endpoint returns the connector-runs JSON; assert via
  `json.dumps(response) does not contain any Settings().secret value`.

### AC-10.4: Backfill is not required but is possible

- **THE** SPEC **SHALL NOT** ship a backfill migration; but
  the DB schema **SHALL** allow a future
  `regexp_replace` pass against `error_details::text` without
  schema changes. Verified by confirming the column is still
  `jsonb` (cast-safe to text) post-fix.

## AC-11 (v0.3.0): MCP tool return never contains upstream body

**REQ-8.1, REQ-8.2, REQ-8.3, REQ-8.4**

### AC-11.1: save_to_docs never echoes resp.text

- **WHEN** `save_to_docs(...)` is invoked via the MCP protocol
  AND klai-docs returns HTTP 500 with a body
  `'Internal server error: {"Authorization": "Bearer docs-internal-secret-value", ...}'`
  **THE** return string from the MCP tool **SHALL NOT** contain
  `"docs-internal-secret-value"` nor `"Bearer"` nor any substring
  of the upstream body.
- **THE** return string **SHALL** match the regex
  `^Error saving to docs: upstream returned HTTP \d+\. Request ID: [0-9a-f-]+\. .*$`.

### AC-11.2: Every MCP tool return path is covered

- Grep `return f"Error.*{resp\.text" OR return f"Error.*{response\.text"`
  across `klai-knowledge-mcp/` **SHALL** return zero matches after
  the fix lands. CI check via a grep step in
  `.github/workflows/knowledge-mcp.yml`.

### AC-11.3: Operator can still debug via request_id

- **WHEN** the MCP tool returns the new error string with
  `Request ID: <uuid>` **AND** an operator queries VictoriaLogs with
  `request_id:<uuid>` **THE** resulting log stream **SHALL** contain
  at least one `event="upstream_error"` entry with the sanitized
  upstream body in the `body` field (sanitized per REQ-4).
- Verification: integration test spans MCP call → log emission →
  VictoriaLogs fixture scrape; assert the body is sanitized but
  present (preserves operator debuggability).

### AC-11.4: No debug-mode escape hatch

- Grep `return f".*{resp\.text" AND (if .*debug|IF .*DEBUG|settings\.debug)`
  in `klai-knowledge-mcp/` **SHALL** return zero matches. REQ-8.5
  forbids a debug-gated echo path.

## AC-12 (v0.3.0): Sanitizer is a shared library

**REQ-4.7**

### AC-12.1: klai-libs/log-utils/ is a Python package

- **WHEN** `cd klai-libs/log-utils && uv pip install -e .` is run
  **THE** package **SHALL** install without error **AND**
  `python -c "from log_utils import sanitize_response_body, extract_secret_values, verify_shared_secret"`
  **SHALL** succeed.

### AC-12.2: Each consumer wires via path dependency

- Each of the five consuming services' `pyproject.toml` **SHALL**
  declare `log-utils` as a path-dependency:
  `log-utils = { path = "../../klai-libs/log-utils" }`
  (exact relative path per service layout).
- Grep assertion: every service's `pyproject.toml` contains
  `log-utils` under `[tool.uv.sources]` or `[tool.poetry.dependencies]`
  or equivalent.

### AC-12.3: Unit tests pass in isolation

- **WHEN** `cd klai-libs/log-utils && uv run pytest` is executed
  **THE** test suite **SHALL** pass, covering:
  - `sanitize_response_body` with None / empty / populated bodies
  - `sanitize_response_body` with secret substring present → redacted
  - `extract_secret_values` with a Pydantic Settings instance →
    returns only fields matching the secret regex AND length >= 8
  - `verify_shared_secret` with matching / mismatching / empty inputs
  - `sanitize_response_body` is constant-time to the extent the
    underlying string-replace operation allows (no early return on
    secret presence)

### AC-12.4: Public API is stable

- **WHEN** `log_utils/__init__.py` is grep'd for
  `^from .* import (sanitize_response_body|extract_secret_values|verify_shared_secret|sanitize_from_settings)$`
  **THE** result **SHALL** contain all four symbols (public API
  surface). Breaking any of them requires a major version bump.

## AC-13 (v0.3.0): End-to-end no-secret-leak across all five services

Integration-level assertion combining REQ-4, REQ-8, REQ-9, REQ-10.

- **WHEN** an adversarial test client executes the full cross-service
  sequence:
  1. Sync a knowledge-ingest-backed connector; knowledge-ingest returns
     500 with its configured secret in the body → assert
     `sync_runs.error_details` + structlog both redacted.
  2. Submit a scribe transcription; the whisper backend returns 500
     with a secret-bearing body → assert structlog redacted.
  3. Trigger a Zitadel webhook at mailer with a forged body → assert
     mailer refuses the webhook (compare_digest mismatch) AND the log
     line does NOT contain the configured webhook_secret.
  4. Call the `save_to_docs` MCP tool; klai-docs returns 500 with
     `DOCS_INTERNAL_SECRET` in the body → assert the MCP tool return
     string does NOT contain the secret AND the portal structlog does
     NOT contain the secret verbatim.
- **THEN** VictoriaLogs capture for the full multi-service
  `request_id` chain **SHALL NOT** contain the verbatim value of ANY
  of `INTERNAL_SECRET`, `WEBHOOK_SECRET`, `KNOWLEDGE_INGEST_SECRET`,
  `DOCS_INTERNAL_SECRET`, `PORTAL_INTERNAL_SECRET` in any field of any
  log entry from any service.
- Verified by grep against a captured log fixture AND by a direct
  `SELECT error_details::text FROM connector.sync_runs` scan.

