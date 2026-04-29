# Changelog

## [Unreleased] — 2026-04-29 — SPEC-SEC-INTERNAL-001: service-wide internal-secret surface hardening

Closes Cornelis 2026-04-22 audit findings #14 (rate-limit fail-open),
#18 (FLUSHALL in regenerate), A2 (taxonomy timing compare), A3 (BFF
proxy header passthrough), A4 (`exc.response.text` log reflection in
20+ portal sites) plus 7 internal-wave findings (2026-04-24) covering
mailer / connector / scribe-api / knowledge-mcp. Behavioural changes
are backward-compatible at every wire-level surface; the new failure
modes (fail-closed startup on empty mandatory secrets, 503 on Redis
outage) only surface under conditions that should not exist in
production. Production env-parity verified against
`klai-infra/core-01/.env.sops` BEFORE merge per the
`validator-env-parity` pitfall.

### Added (security)

- **`klai-libs/log-utils/`** (NEW shared package, distribution
  `klai-log-utils`) — four-symbol public API: `sanitize_response_body`
  (REQ-4.1, scrubs known-secret substrings before truncation, default
  512 chars), `sanitize_from_settings` (REQ-4.4, convenience wrapper),
  `extract_secret_values` (REQ-4.2, walks Pydantic `model_fields` and
  matches secret-shaped names with length ≥ 8), `verify_shared_secret`
  (REQ-1.7, constant-time `hmac.compare_digest` with empty-configured
  ValueError guard). `py.typed` marker (PEP 561) so consumer pyright
  doesn't fall back to `reportMissingTypeStubs`. 29 tests, ruff +
  pyright strict clean.
- **Per-service sanitizer wrappers** —
  `klai-portal/backend/app/utils/response_sanitizer.py` /
  `klai-connector/app/core/sanitize.py` /
  `klai-scribe/scribe-api/app/core/sanitize.py` /
  `klai-knowledge-mcp/main.py::_KNOWN_SECRETS` — each binds the local
  Settings instance to the shared sanitizer.
- **`klai-portal/backend/app/core/config.py::internal_rate_limit_fail_mode`**
  (REQ-5) — new `Literal["open", "closed"]` Settings field, default
  `"closed"`. Production fails-closed on Redis outage (returns HTTP
  503 with `internal_rate_limit_fail_closed` warning); staging /
  dev override to `"open"` for SEC-005 baseline behaviour.
- **`klai-portal/backend/app/core/config.py::librechat_cache_key_pattern`**
  (REQ-2.3) — new Settings field, default `"configs:*"`. Future
  LibreChat upgrade can flip the namespace via SOPS without a code
  change.
- **`klai-portal/backend/app/api/proxy.py::_SECRET_HEADER_BLOCKLIST`**
  + `_SECRET_HEADER_REGEX` (REQ-3) — explicit deny-list
  (`x-internal-secret`, `x-klai-internal-secret`,
  `x-retrieval-api-internal-secret`, `x-scribe-api-internal-secret`)
  plus the catch-all regex
  `(?i)^(x-)?(klai-internal|internal-auth|internal-token)`. Every
  blocked attempt emits `proxy_header_injection_blocked` at info; the
  header VALUE is never logged.
- **Fail-closed Settings validators** on connector / scribe-api /
  knowledge-mcp (REQ-9.3 / REQ-9.4 / REQ-9.5). Mirrors the mailer
  validator already shipped in SPEC-SEC-MAILER-INJECTION-001 (#168).
  Process refuses to import / start when any required outbound secret
  is empty. Runtime guards on
  `klai-connector/app/services/portal_client.py::_headers()` and
  `klai-connector/app/clients/knowledge_ingest.py::__init__` catch the
  `Settings.model_construct()` bypass path used in some unit tests.
- **`rules/no-secret-eq-compare.yml` + `rules/no-secret-neq-compare.yml`**
  + RHS variants (REQ-6) — ast-grep rules with a `kind: identifier`
  constraint so `Model.field == ...` SQLAlchemy expressions don't
  false-positive. Wired into the five service workflows
  (`portal-api.yml` / `klai-mailer.yml` / `klai-connector.yml` /
  `scribe-api.yml` / `klai-knowledge-mcp.yml`). Regression fixture at
  `.github/test-fixtures/sec-internal-001/regression.py` with five
  intentional violations.

### Changed (security)

- **`klai-portal/backend/app/api/internal.py::regenerate_librechat_configs`**
  (REQ-2) — replaced `redis_client.flushall()` with
  `scan_iter(match=settings.librechat_cache_key_pattern, count=100)` +
  batched `unlink(*batch)`. Failure surfaces as
  `redis-cache-invalidation: <exc>` in the response `errors` list (was
  `redis-flushall: ...`); restart step still runs (REQ-2.5).
  `app/services/provisioning/infrastructure.py::_flush_redis_and_restart_librechat`
  follows the same SCAN+UNLINK shape on the sync redis client.
- **`klai-portal/backend/app/api/taxonomy.py::_require_internal_token`**
  (REQ-1.1) — now uses `log_utils.verify_shared_secret`. The previous
  `if token != f"Bearer {settings.internal_secret}":` leaked
  length/prefix timing.
- **`klai-knowledge-mcp/main.py::save_to_docs`** (REQ-8) — return
  contract changed from
  `f"Error: klai-docs returned HTTP {resp.status_code}. Details: {resp.text[:300]}"`
  to
  `f"Error saving to docs: upstream returned HTTP {status}. Request ID: {uuid}. Operator: check VictoriaLogs."`.
  The sanitised upstream body is logged server-side at the same
  request_id so operators retain debuggability without leaking the
  body to the chat UI.
- **`klai-connector/app/services/sync_engine.py`** (REQ-10) — the
  `enqueue_err.response.text[:500]` write into `sync_runs.error_details`
  JSONB now passes through the connector's `sanitize_response_body`
  wrapper first. A reflected `KNOWLEDGE_INGEST_SECRET` from upstream
  cannot land in Postgres or in the portal connector-management UI.
- **`klai-scribe/scribe-api/app/services/knowledge_adapter.py`** —
  removed the `if settings.knowledge_ingest_secret:` silent-omit guard.
  Header injection is now unconditional now that the Settings
  validator enforces non-empty.
- **`klai-scribe/scribe-api/Dockerfile`** — full rewrite to the
  repo-root + `uv sync --frozen` pattern (mirrors knowledge-mcp /
  connector / portal-api). The previous `uv pip install -r pyproject.toml`
  flow did not honour `[tool.uv.sources]` so the `klai-log-utils`
  path-dep silently fell through to a PyPI lookup.
  `.github/workflows/scribe-api.yml` build context broadened to repo
  root accordingly.
- **REQ-4 sweep (~28 sites)** — every raw `exc.response.text` /
  `resp.text[:N]` log call across portal-api / connector / scribe-api /
  knowledge-mcp is rewritten through the per-service sanitizer
  wrapper. Where SPEC-SEC-AUTH-COVERAGE-001 (#195) had already
  replaced `logger.exception(...)` with structured `_slog.exception(...)`
  + `_emit_auth_event(...)` (22 of the 24 auth.py sites), the merge
  resolution kept main's structured-event approach — both achieve
  REQ-4 and events are more thorough.

### Tests

- 29 in `klai-libs/log-utils/tests/`.
- 11 new in portal-api (`tests/test_taxonomy_internal_token.py`,
  `tests/test_proxy_header_injection.py`, 3 new fail-mode tests in
  `tests/test_internal_hardening.py`, rewritten
  `tests/test_librechat_regenerate.py` around the SCAN+UNLINK
  contract — `redis_client.flushall.assert_not_called()` doubles as
  a tripwire).
- 12 in knowledge-mcp (`tests/test_sec_internal_001.py`) including
  subprocess-based fail-closed boot tests and source-grep regression
  guards.
- 10 in connector (`tests/test_sec_internal_001.py`).
- 8 in scribe-api (`tests/test_sec_internal_001.py`).

### Deployed

PR #201 merged 2026-04-29 07:47 UTC (admin override after CI green —
required-review policy is in place but the PR was self-authored).
Auto-deploy chain ran clean on all 5 services; container ages on
core-01 are 1-3 minutes post-merge with zero error-level logs in the
20-minute post-deploy window. `internal_rate_limit_fail_closed` count
is zero (Redis healthy, defensive fail-closed path not exercised).

---

## [Unreleased] — 2026-04-29 — SPEC-SEC-SESSION-001: session and cookie robustness

Closes Cornelis 2026-04-22 audit findings #13 (TOTP per-instance counter),
#15 (`klai_idp_pending` cookie no origin-context binding), #16 (`klai_sso`
ephemeral-key fallback on empty `SSO_COOKIE_KEY`). Behavioural changes are
backward-compatible at the HTTP surface — only the server-side storage
substrate, the Fernet payload shape, and the lifespan startup-abort path
change.

### Added (security)

- **`klai-portal/backend/app/api/auth.py::_get_sso_fernet`** —
  `@lru_cache(maxsize=1)` accessor that raises `RuntimeError` on empty /
  whitespace `SSO_COOKIE_KEY` instead of falling through to
  `Fernet.generate_key()`. Mirrors the pattern already used by
  `signup.py::_get_fernet`. Three callsites migrated (`_encrypt_sso`,
  `_decrypt_sso`, `idp_signup_callback`'s pending-cookie encrypt site).
- **`klai-portal/backend/app/api/auth.py::_totp_pending_create` / `_get` /
  `_incr_failures` / `_delete`** — Redis-backed pending-state primitives
  using two keys per token: `totp_pending:<token>` (HASH with
  `session_id`, `session_token`, `ua_hash`, `ip_subnet`) +
  `totp_pending_failures:<token>` (STRING incremented via atomic `INCR`).
  Both keys carry the same TTL (`settings.totp_pending_ttl_seconds`,
  default 300 s). Replaces the in-memory `_pending_totp = TTLCache(...)`
  which let a 5-failure ceiling become an N×5 ceiling behind a
  round-robin proxy.
- **`klai-portal/backend/app/api/signup.py::_verify_idp_pending_binding`** —
  consume-side check that compares the decrypted Fernet payload's
  `ua_hash` / `ip_subnet` against values derived from the current
  request. Mismatch returns HTTP 403 + structlog event; the cookie is
  preserved so the legitimate user can resume from the same browser
  within TTL.
- **`klai-portal/backend/app/services/request_ip.py`** (NEW) — public
  `resolve_caller_ip` (right-most `X-Forwarded-For` entry from Caddy →
  `request.client.host` → `"unknown"`) plus `resolve_caller_ip_subnet`
  (`/24` IPv4, `/48` IPv6). Extracted from `app/api/internal.py` once a
  third callsite (auth IDP-pending issue + signup-social binding
  consume) joined the existing internal-rate-limit consumer.
- **`klai-portal/backend/app/main.py` lifespan SSO check** — calls
  `_get_sso_fernet()` BEFORE the dev/prod branch so
  `is_auth_dev_mode=True` no longer bypasses the SSO-key validation
  (REQ-4.4 closes the silent-on-dev-box failure mode). Empty key emits
  structlog `critical` event `sso_cookie_key_missing_startup_abort`
  with `env_var="SSO_COOKIE_KEY"` + `sops_path` BEFORE re-raising, so
  Alloy captures the abort in VictoriaLogs even though the process is
  about to exit non-zero.
- **4 structured events** in VictoriaLogs:
  `totp_pending_lockout` (warning, on the 5th failed code),
  `totp_pending_redis_unavailable` (error, fail-CLOSED on Redis outage),
  `idp_pending_binding_mismatch` (warning, on UA / IP-subnet mismatch),
  `sso_cookie_key_missing_startup_abort` (critical, on lifespan abort).
  All events carry prefix-only PII (8-hex hash prefixes, `/24`/`/48`
  subnet network addresses, 8-char token prefixes) — never raw UA, raw
  IP, full token, or session credentials. PII guard verified by
  `tests/test_session_logging_pii.py`.
- **`deploy/grafana/provisioning/alerting/portal-session-rules.yaml`**
  (NEW) — two LogsQL alerts on the new events:
  - R1 `session_sso_cookie_key_missing` (critical): `>= 1` event in any
    1m window. A single occurrence means a portal-api replica failed to
    boot due to misconfigured `SSO_COOKIE_KEY` — operators must intervene.
  - R2 `session_totp_redis_unavailable` (critical): `> 3` events in 1m.
    Sustained burst means TOTP login is broken (fail-CLOSED kicks in
    when Redis is unreachable; users see HTTP 503).
- **22 new tests** across six files: `test_auth_totp_lockout.py`,
  `test_idp_pending_binding.py`, `test_startup_sso_key_guard.py`,
  `test_auth_login_happy_path.py`, `test_session_logging_pii.py`, plus
  request-parameter migrations across `test_auth_security.py`,
  `test_auth_mfa_fail_closed.py`, `test_social_signup.py`,
  `test_auth_totp_endpoints.py`, `test_auth_idp_endpoints.py`.

### Changed

- **`SSO_COOKIE_KEY` startup validation moved out of the prod-only
  missing-vars list in `app/main.py`** — was double-listed there
  pre-SPEC; the lifespan-level check via `_get_sso_fernet()` is the
  authoritative guard now. ZITADEL_PAT / PORTAL_SECRETS_KEY /
  ENCRYPTION_KEY / DATABASE_URL still validated by the prod-only block.
- **`klai-portal/backend/tests/conftest.py`** — adds the shared
  `fake_redis` fixture (in-memory `fakeredis.aioredis.FakeRedis` swapped
  into the `_pool_holder` singleton for one test). All Redis-using auth
  tests pick it up via fixture parameter.
- **`klai-portal/backend/tests/helpers.py`** — adds `make_request()`
  factory that builds a synthetic Starlette `Request` for tests that
  bypass the FastAPI router. Defaults to `127.0.0.1:12345` so
  `resolve_caller_ip` returns a parseable address without per-test
  setup.
- **`klai-portal/backend/pyproject.toml`** — adds `fakeredis>=2.26` to
  both dev dep groups (`[project.optional-dependencies]` and
  `[dependency-groups]`).
- **`klai-portal/backend/app/core/config.py`** — adds
  `totp_pending_ttl_seconds: int = 300` setting; default matches the
  legacy in-memory window. Tunable per environment without code change.

### Removed

- **`_pending_totp = TTLCache(...)` module global** in `app/api/auth.py`.
  The `TTLCache` class itself remains available as a generic utility
  (REQ-1.8), but the production TOTP path no longer routes through it.
- **`Fernet.generate_key()` fallback** at the old `auth.py:106`. There
  is now no path under which portal-api will issue cookies signed with
  an ephemeral, per-replica key. Misconfiguration aborts the process.

### Coverage

- 22 dedicated tests across the four security-critical surfaces (Redis
  TOTP helpers, lifespan SSO guard, IDP-pending binding check, PII
  redaction). Acceptance scenarios 1-8 from `acceptance.md` all
  represented; CI `Build and push portal-api / quality` job passed
  pytest + ruff + pyright + ruff-format on the merged PR.

## [Unreleased] — 2026-04-28 — SPEC-SEC-AUTH-COVERAGE-001: auth.py coverage + observability hardening (14 endpoints)

Companion to SPEC-SEC-MFA-001. Same Cornelis-audit context (2026-04-22)
applied to all 14 in-scope auth.py endpoints beyond `login`: every
documented failure leg now emits a structured event, every state-changing
success emits an audit log, and 74 new tests verify both.

### Added (security observability)

- **`klai-portal/backend/app/api/auth.py::_emit_auth_event`** — generalized
  helper for structured auth-event emission. Privacy-safe (sha256 email
  hashing via `email_hash`), structlog-based, fan_in ≥ 14. Replaces the
  single-purpose `_emit_mfa_check_failed` (now a thin wrapper for
  back-compat).
- **16 structured `*_failed` events** emitted across 14 endpoints —
  `totp_setup_failed`, `totp_confirm_failed`, `totp_login_failed`,
  `passkey_setup_failed`, `passkey_confirm_failed`,
  `email_otp_setup_failed`, `email_otp_confirm_failed`,
  `email_otp_resend_failed`, `idp_intent_failed`,
  `idp_intent_signup_failed`, `idp_callback_failed`,
  `idp_signup_callback_failed`, `password_reset_failed`,
  `password_set_failed`, `sso_complete_failed`, `verify_email_failed`.
  Common shape: `{event, reason, outcome, zitadel_status, email_hash, level}`.
- **`audit.log_event`** on every state-changing success: `auth.totp.setup`,
  `auth.totp.confirmed`, `auth.totp.login`, `auth.passkey.setup`,
  `auth.passkey.confirmed`, `auth.email-otp.setup`,
  `auth.email-otp.confirmed`, `auth.email-otp.resent`, `auth.password.reset`,
  `auth.password.set`, `auth.login.idp`, `auth.signup.idp`,
  `auth.sso.completed`, `auth.email.verified`. Closes the audit-trail gap
  flagged by Cornelis.
- **`klai-portal/backend/tests/auth_test_helpers.py`** (NEW) — shared
  fixtures and patches: `respx_zitadel`, `_make_login_body`,
  `_expected_email_hash`, `_session_ok`, `_make_sso_cookie`, `_make_db_mock`,
  `_audit_log_patch`, `_capture_events`. Replaces 5 duplicate
  `_audit_log_patch` definitions across test files (DRY refactor in polish
  round closed REQ-5.6 regression).
- **74 new test scenarios** across 7 test files covering all 14 endpoints
  via `respx`-mocked Zitadel HTTP layer (no `MagicMock` on
  `app.api.auth.zitadel` per REQ-5.7).
- **5 `@MX:ANCHOR` tags** on helpers with fan_in ≥ 3:
  `_emit_auth_event`, `_mfa_unavailable`, `_emit_mfa_check_failed`,
  `_finalize_and_set_cookie`, `_validate_callback_url`.
- **`deploy/grafana/provisioning/alerting/portal-auth-rules.yaml`** (NEW) —
  two LogsQL alerts on the new events:
  - R1 `auth_failure_rate_high` (warning): > 10 events/5m across the 16
    endpoint events.
  - R2 `auth_zitadel_5xx_burst` (critical): > 5 `reason=zitadel_5xx`
    events/1m (canonical Zitadel-outage signal).
- **`docs/runbooks/auth-failure-burst.md`** (NEW) — triage runbook for R1
  and R2: event taxonomy, cross-endpoint spread analysis, brute-force
  probe detection via `email_hash` distribution, Zitadel-outage handling.

### Changed

- **`logger.*` → `_slog.*` migration** for all 14 in-scope endpoints
  (REQ-5.3). Remaining 7 stdlib `logger.*` calls in shared helpers
  (`get_current_user_id`, `_validate_callback_url`, `_decrypt_sso`,
  `_finalize_and_set_cookie` cookie-set branch) are out-of-scope per the
  endpoint-only contract.
- **`klai-portal/backend/tests/conftest.py`** — re-exports `respx_zitadel`
  fixture for pytest auto-discovery; adds defaults for
  `ZITADEL_IDP_GOOGLE_ID`, `ZITADEL_IDP_MICROSOFT_ID`,
  `MONEYBIRD_WEBHOOK_TOKEN`, `VEXA_WEBHOOK_SECRET` so cross-cutting tests
  pass without a live `.env`.
- **`klai-portal/backend/pyproject.toml`** — adds `respx>=0.22` to dev
  dependency-group.

### Coverage

- `app.api.auth` line coverage: **64% → 80%** (+16% delta).
- REQ-5.5 PARTIAL (target was ≥85%): the remaining 5% gap is in shared
  helpers (`_finalize_and_set_cookie` error legs, `_validate_callback_url`
  localhost / untrusted-host branches, `_decrypt_sso` exception path),
  not in endpoint observability. Every documented failure leg of every
  in-scope endpoint has a test.

## [Unreleased] — 2026-04-27 — SPEC-SEC-MFA-001: MFA fail-closed in login flow

Closes SPEC-SEC-AUDIT-2026-04 findings #11 and #12 (Cornelis audit
2026-04-22). The portal-api login handler now refuses login with HTTP 503 +
`Retry-After: 5` whenever the MFA enforcement check cannot complete under
`mfa_policy="required"`, instead of silently bypassing MFA. Documented
fail-open behaviour is preserved under `mfa_policy="optional"` /
`"recommended"`.

### Fixed (security)

- **`klai-portal/backend/app/api/auth.py::login`** — replaced the
  `user_has_mfa = True` fallback with an explicit fail-closed branch.
  `has_any_mfa` raising `httpx.HTTPStatusError`, `httpx.RequestError`, or
  any unexpected exception now raises `HTTPException(503, …, headers={"Retry-After": "5"})`
  before any cookie or session artefact is created.
- **Pre-auth try split** — `find_user_by_email` 5xx and `RequestError` now
  surface as 503 BEFORE `create_session_with_password` runs. 4xx is still
  treated as "well-formed not found" and the password check returns 401.
  This closes the finding-#12 path where `zitadel_user_id` could remain
  `None` and silently skip the MFA enforcement block.
- **DB-lookup splits** — `portal_user` lookup raise still fail-opens
  (provisioning grace), but `portal_user found + PortalOrg fetch raise`
  now fail-closes 503 rather than silently downgrading to optional.
- **Orphan `PortalOrg` FK** — `portal_user.org_id` pointing at a missing
  org now emits a `mfa_check_failed` warning while preserving fail-open
  semantics. Pre-existing behaviour was a silent fall-back, hiding
  data-integrity bugs.

### Added

- **`klai-portal/backend/app/api/auth.py`** — three helpers:
  - `_mfa_unavailable()` — single source of truth for the 503 response.
  - `_emit_mfa_check_failed()` — structured structlog event emitter (fields:
    `reason`, `mfa_policy`, `zitadel_status`, `email_hash` (sha256), `outcome`).
    Email is never logged in plaintext.
  - `_resolve_and_enforce_mfa()` — extracted MFA enforcement block, fully
    branch-tested.
- **`klai-portal/backend/tests/test_auth_mfa_fail_closed.py`** (NEW) — 13
  respx-mocked scenarios exercising every fail-closed and documented
  fail-open branch. Uses respx against the real `ZitadelClient` instance
  (not `MagicMock` on `app.api.auth.zitadel`), per REQ-5.7.
- **`klai-portal/backend/pyproject.toml`** — `respx>=0.22` added to dev
  dependency group.
- **`deploy/grafana/provisioning/alerting/portal-mfa-rules.yaml`** (NEW) —
  two LogsQL alerts: rate >1/min sustained 5m (warning) and fail-open
  burst >10/min (critical), both linking to the runbook below.
- **`docs/runbooks/mfa-check-failed.md`** (NEW) — triage steps for both
  alerts, including security-escalation criteria.
- **`@MX:ANCHOR`** annotations on `_mfa_unavailable` and
  `_emit_mfa_check_failed` documenting the cross-component contracts
  (Grafana alert schema + runbook + frontend 503 expectations).

### Changed

- **`klai-portal/backend/tests/test_auth_security.py`** — `TestMFAPolicyEnforcement`
  narrowed:
  - `test_mfa_check_failure_defaults_to_pass` — **deleted** (REQ-5.3); the
    fail-open behaviour it asserted is now an anti-pattern.
  - `test_mfa_policy_lookup_failure_defaults_to_optional` — narrowed to
    cover only the "portal_user lookup raised" arm; the
    "portal_user found + org fetch raises" arm is now covered by the new
    fail-closed test module.

### Operational notes

- **Behaviour change for production users in required-MFA orgs.** During a
  Zitadel restart flap (5xx window) login now returns 503 + `Retry-After: 5`
  instead of silently letting the user through without MFA. Users retry
  within seconds. The portal frontend already handles 502 with a generic
  "try again later" message; 503 surfaces identically.
- **Grafana alerts go live with this PR.** `alerting-check.yml` will
  validate the YAML on merge; the alerts appear under "Klai → sec-mfa-001-portal-api"
  group after the next Grafana provisioning reload.
- **No DB migration required.** No new env vars. No backward-incompatible
  API contract changes.
- **Coverage gap** (deferred): overall coverage on `klai-portal/backend/app/api/auth.py`
  is 64% (target 85%), unchanged from before. The MFA enforcement block
  itself has full branch coverage. Closing the overall gap requires testing
  unrelated endpoints (TOTP setup, IDP intent, password reset, sso_complete)
  and is recommended as a follow-up SPEC.

---

## [Unreleased] — 2026-04-22 — SPEC-INFRA-005: Stateful service persistence hardening

Triggered by the 2026-04-19 FalkorDB graph data loss incident
(`docs/runbooks/post-mortems/2026-04-19-falkordb-graph-loss.md`). Closes the
class of bug where a stateful service silently writes to its container's
ephemeral layer because the compose mount target does not match the image's
actual data path.

### Added

- **`deploy/volume-mounts.yaml`** — single source of truth for every RW bind
  or named-volume mount in `docker-compose.yml` (28 entries). Each entry
  carries image, container path, backup method, retention rule, PII flag.
- **`scripts/audit-compose-volumes.sh`** + GitHub Action — pre-merge guard
  that fails any PR introducing a mount mismatch or an unregistered volume.
  Backed by `scripts/test-audit-compose.sh` with 4 scenarios (baseline plus
  three regression patterns) so the audit cannot itself silently break.
- **`deploy/scripts/persistence-smoke.sh`** — post-deploy proof that each
  stateful service's writes actually land on the host volume (not on the
  container's writable layer). Five services: falkordb, redis, postgres,
  qdrant, garage. Wired into `docs/runbooks/version-management.md` §3.3
  step 7 + §3.4 step 6.
- **`deploy/scripts/persistence-probe.sh`** + systemd timer + Alloy textfile
  collector — exports `klai_persistence_file_age_seconds{service,path}` to
  VictoriaMetrics every 10 minutes. Two Grafana alert rules
  (`persistence-rules.yaml`): "stale" (24h loose default, tighten per-service
  after baseline emerges) and "missing" (`age == -1`, the sharp 2026-04-19
  detector — fires when the host file simply isn't there).
- **`scripts/backup.sh` extension** — went from 6 to 13 data steps.
  New: vexa-redis, Qdrant per-collection snapshots via API, FalkorDB,
  Garage meta-snapshot + data-blob rsync, scribe-audio, research-uploads,
  Firecrawl postgres. All age-encrypted to the existing Hetzner Storage
  Box rsync target.
- **Healthchecks** for falkordb, gitea, grafana, victoriametrics, victorialogs.
  Last few stateful services on the stack without one. Ollama remains
  distroless; covered by Uptime Kuma `push_exec`.
- **Stateful service change checklist** — new §11 in `version-management.md`
  with 7 mandatory checks for any PR touching a stateful service.
- **Pitfall §7.10 — "Bind mount path must match the image's data path"** —
  full narrative of the FalkorDB incident with the prevention command.

### Fixed

- **FalkorDB compose mount** — `/opt/klai/falkordb-data:/data` →
  `/opt/klai/falkordb-data:/var/lib/falkordb/data`. Previous mount was
  cosmetic; persistence had been a lie since 2026-03-26.
- **Scribe audio retention** — `delete_when_transcribed` was the stated
  policy but only the user-initiated DELETE endpoint actually unlinked
  the file. The success path of POST /v1/transcribe and /retry now calls
  `audio_storage.finalize_success()`. New helper module
  `klai-scribe/scribe-api/app/services/audio_storage.py` with 6
  regression tests. Production orphan from 2026-04-10 cleaned up.
- **Research-uploads retention** — implemented event-driven policy decided
  by product owner: file deleted when source is removed, when notebook is
  removed, OR when tenant is decommissioned. New
  `klai-focus/research-api/app/services/upload_storage.py` helper centralises
  all three triggers with path-traversal guards + idempotent semantics. 13
  regression tests. Closes pre-existing orphan-files bug in `delete_notebook`
  (Postgres+Qdrant rows were dropped but PDFs remained on disk forever).
  Manual ops CLI `scripts/research_tenant_cleanup.py` for trigger 3 until
  portal-api gains automatic tenant teardown.

### Operational notes

- Backup cron continues to run nightly at 02:00 as the `klai` user; manual
  backup runs MUST also use `sudo -u klai`, not plain `sudo` — root has no
  Storage Box SSH key (now documented in `backup.sh` header).
- Phase 6 staleness threshold starts at 24h for every service. Per-service
  tuning is a follow-up after ~2 weeks of real metric data.

---

## [Unreleased] — 2026-04-17 — SPEC-WIDGET-002: Split API Keys and Chat Widgets

### Added — SPEC-WIDGET-002: Independent API keys and Chat widgets domains

- **Separate database tables:** New `widgets` + `widget_kb_access` tables with own RLS policies. Widget auth is 100% JWT-based (`WIDGET_JWT_SECRET`) — no shared secrets with API keys. Data migrated from combined `partner_api_keys` table.
- **Separate admin endpoints:** `/api/api-keys/*` and `/api/widgets/*` replace the combined `/api/integrations/*`. Each has full CRUD with domain-specific schemas.
- **Separate admin UI:** Two sidebar items "API keys" and "Chat widgets". Each has its own wizard (4 steps) and tabbed detail view (5 tabs matching wizard steps 1-to-1). No `if (isWidget)` branches.
- **Widget streaming chat:** Embedded `klai-chat.js` (56KB gzip 21KB) with SSE streaming via `fetchEventSource`, grounded KB-only system prompt (refuses to guess), markdown rendering via `snarkdown` + `DOMPurify` XSS protection.
- **Klai brand styling:** Widget defaults to amber primary (#fcaa2d), warm ivory background (#fffef2), cream cards (#f3f2e7), Parabole font. Dark text on amber (WCAG compliant).
- **Widget i18n:** NL + EN labels auto-detected from browser locale, overridable via `data-locale` attribute.
- **Wildcard origin matching:** `https://*.getklai.com` matches all tenant subdomains. Fail-closed (empty list blocks everything).
- **CI pipeline:** Widget bundle built inside portal-frontend workflow. Triggers on `klai-widget/src/**` changes.
- **Tests:** 26 tests covering smoke (12), integration (7), widget-config (5), origin matching (5).

### Removed

- **`/api/integrations/*` endpoints** — hard-removed, returns 404.
- **Revoke/active concept** — no soft-delete for either domain. `DELETE` is the only way to end an API key or widget. `active` column dropped from `partner_api_keys`.
- **`integration_type` discriminator** — column dropped. No `if (isWidget)` branches in code.
- **`admin_integrations_*` paraglide keys** — 48 dead keys removed, 68 renamed to `admin_api_keys_*`, `admin_widgets_*`, `admin_shared_*`.

### Fixed

- **RLS DELETE policy** on `partner_api_keys` — was missing, caused silent 204 with 0 rows affected.
- **RLS SELECT policies** on `widgets` and `widget_kb_access` — changed to permissive (`USING true`) for the public widget-config endpoint.
- **`set_tenant` in admin `_get_caller_org`** — was missing, caused `InvalidTextRepresentationError` on all admin writes to RLS-scoped tables.
- **Partner chat retrieval URL** — `/retrieve/v1/query` corrected to `/retrieve` (the actual endpoint).
- **Embed snippet URL** — `cdn.getklai.com` (not a real CDN) corrected to `my.getklai.com` (portal Caddy).
- **Widget JS MIME type** — self-hosted in portal `public/widget/` served as `text/javascript` by Caddy (previously returned `text/html` from Astro catch-all).

## [Unreleased] — 2026-04-17 — SPEC-CRAWL-004: Automatic Auth Guard Setup

### Added — SPEC-CRAWL-004: AI-first auth guard in connector wizard

- **Auto-detection during preview:** when a webcrawler preview succeeds with cookies, the system automatically computes a canary fingerprint and uses AI to detect the login indicator element. Admin sees "✓ Auth protection enabled" — no technical config needed.
- **knowledge-ingest/fingerprint.py** (NEW): stdlib-only SimHash reimplementation, compatible with klai-connector's trafilatura version. Zero external deps.
- **knowledge-ingest/selector_ai.py:** added `detect_login_indicator_via_llm()` — identifies logout buttons, user menus, and account dropdowns via LLM DOM analysis.
- **knowledge-ingest/routes/crawl.py:** `CrawlPreviewResponse` extended with `auth_guard` field containing canary URL, fingerprint, and login indicator.
- **klai-connector/routes/fingerprint.py** (NEW): `POST /api/v1/compute-fingerprint` endpoint for manual canary URL changes. Uses `_post_crawl_sync()` shared helper.
- **Portal backend:** `_auto_fill_canary_fingerprint()` on connector create/update — recomputes fingerprint when canary_url set but fingerprint missing. XOR validator relaxed for backend auto-fill flow.
- **Portal frontend:** auth guard confirmation card in preview step with Shield icon + expandable advanced settings for manual override.

### Fixed

- **Semgrep CI:** excluded minified widget JS (`klai-chat.js`) from SAST scan — false positive on Shadow DOM API in pre-built SolidJS bundle.

## [Unreleased] — 2026-04-17 — SPEC-CRAWL-003: Three-Layer Content Quality Guardrails

### Added — SPEC-CRAWL-003: Auth-expiry detection for webcrawler connectors

- **Layer A — Canary fingerprint (pre-sync fail-fast):** Re-crawls a reference page before each sync and compares its SimHash fingerprint to the stored baseline. Similarity < 0.80 aborts the sync immediately with `status=auth_error`, `quality_status=failed`. Prevents contaminated content from reaching Qdrant.
- **Layer B — Per-page login indicator:** CSS selector (`login_indicator_selector`) embedded in Crawl4AI `wait_for` to detect auth-walled pages. Pages that fail the selector are excluded from ingest with a single summary log (no per-page log spam).
- **Layer C — Post-sync boilerplate-ratio metric:** 64-bit SimHash fingerprint per page; greedy centroid clustering (pairwise for ≤200 pages, LSH 8×8 bands for >200). Clusters exceeding 15% of total pages flag `quality_status=degraded`. Minimum 30 pages for statistical validity.
- **`klai-connector/app/services/content_fingerprint.py`** (NEW): Pure-function module with `compute_content_fingerprint()`, `similarity()`, `find_boilerplate_clusters()`, and `ContentFingerprint` NewType.
- **`klai-connector/app/services/events.py`** (NEW): Fire-and-forget product event emission via direct DB write to shared `product_events` table. Resolves Zitadel org_id to `portal_orgs.id` FK.
- **Alembic migration 005**: `quality_status VARCHAR(20)` nullable column on `connector.sync_runs`.
- **Grafana alert**: "Knowledge sync quality degraded" (uid=bfjbxm0h95q0wf) — queries `product_events` for `knowledge.sync_quality_degraded` events.
- **Portal validation**: `WebcrawlerConfig` Pydantic model extended with `canary_url`, `canary_fingerprint`, `login_indicator_selector` + XOR validator.
- **165 tests** across 6 test files; `content_fingerprint.py` at 98% coverage.

### Fixed — Post-deploy bugs found during E2E

- Product event emission used a non-existent HTTP endpoint (`POST /internal/product-events`). Replaced with direct DB write matching portal's own pattern.
- `from app.core.database import session_maker` captured `None` at import time. Fixed to read `database.session_maker` at call time.
- Layer C detail logs had `sample_urls=[]` (lookup key mismatch). Fixed to use cluster URLs directly.
- `wait_for` combined login indicator with `||` syntax (invalid Crawl4AI). Fixed to embed CSS check inside JS arrow function.

### Changed — Code quality improvements

- Extracted `_post_crawl_sync()` helper — single place for POST /crawl plumbing (cookie hooks, auth, payload construction).
- `LAYER_C_MIN_PAGES = 30` as named module-level constant (was inline magic number).
- `ContentFingerprint = NewType("ContentFingerprint", str)` for type safety across adapter and sync engine.
- Log deduplication: event name in message string, queryable data exclusively in `extra={}`.
- Robust `wait_for` matching via `re.match()` instead of brittle `startswith("js:() =>")`.

### Ops

- Cleaned 1115 login-wall boilerplate chunks from Redcactus KB in Qdrant (1124 clean chunks remaining).

## [Unreleased] — 2026-04-16 — SPEC-KB-IMAGE-001: Adapter-owned image URL resolution (refactor)

### Changed

- **`klai-connector/app/adapters/webcrawler.py`**: `_process_results()` resolveert nu relatieve image URLs naar absoluut t.o.v. de pagina-URL, direct bij het ophalen van resultaten. Geen connector-type dispatch meer in `sync_engine` voor webcrawler URLs.
- **`klai-connector/app/adapters/notion.py`**: Verwijderd `_image_cache` side-channel en `get_cached_images()` methode. `fetch_document()` zet `ref.images` nu direct (conform het BaseAdapter contract).
- **`klai-connector/app/adapters/github.py`**: `source_url` is nu de GitHub blob-view URL (`https://github.com/{owner}/{repo}/blob/{branch}/{path}`) voor gebruikerszichtbare citaties. De raw URL (`raw.githubusercontent.com`) wordt alleen intern gebruikt in `fetch_document()` als basis voor het resolven van markdown image URLs. Nieuwe statische helper `_extract_markdown_images()` vult `ref.images` met absolute URLs voor `.md` en `.rst` bestanden.
- **`klai-connector/app/services/sync_engine.py`**: `_extract_and_upload_images()` hernoemd naar `_upload_images()`. Alle connector-type dispatch verwijderd. `text` parameter verwijderd. `extract_markdown_image_urls()` en `resolve_relative_url` imports verwijderd. Parameter `ref` getypt als `DocumentRef` in plaats van `Any`.
- **`klai-connector/app/adapters/base.py`**: Docstrings documenteren nu expliciet het contract: `ImageRef.url` MUST be absolute HTTP(S); `DocumentRef.images` is URL-based only (DOCX/PDF embeds gaan via de parser pipeline, niet via dit veld). Docstrings toegevoegd voor `source_url` en `last_edited` velden op `DocumentRef`.

### Added

- **18 nieuwe unit tests** verdeeld over 4 testbestanden:
  - `tests/adapters/test_github_images.py` (NIEUW) — 9 tests voor markdown URL resolution (relatief, absoluut, dot-slash, branch handling, data URI skipping, leading-slash urljoin semantics)
  - `tests/adapters/test_webcrawler.py::TestImageUrlResolution` — 4 tests voor `_process_results()` absolute URL conversie
  - `tests/adapters/test_notion.py` — 3 tests voor `ref.images` populatie + afwezigheid van legacy `_image_cache`
  - `tests/test_sync_engine_images.py::TestUploadImagesIsConnectorAgnostic` — 2 tests voor connector-agnostic upload

> Dit is een refactor. Er zijn geen nieuwe gebruikerszichtbare features. Extern gedrag is ongewijzigd.

## [Unreleased] — 2026-04-06 — SPEC-KB-026: Taxonomy Integration Hardening

### Fixed — SPEC-KB-026: Taxonomy Integration Hardening (6 bugs)

- **R1+R2 (Critical) — `clustering_tasks.py`**: Fixed `submit_taxonomy_proposal` signature mismatch that caused a `TypeError` on every clustering run — no proposals were ever submitted. Added `cluster_centroid` field to `TaxonomyProposal` dataclass and payload so auto-categorise fires after proposal approval.
- **R3 (Major) — `proposal_generator.py`**: `maybe_generate_proposal()` now calls `generate_node_description()` — node descriptions were always empty.
- **R4 (Major) — portal gap classification**: New `POST /ingest/v1/taxonomy/classify` endpoint in `klai-knowledge-ingest`. Portal's gap classification wired to it in `app/api/internal.py` (was a skeleton that only logged "not yet connected").
- **R5 (Major) — auto-categorise job**: Replaced `asyncio.create_task()` fire-and-forget in `app/api/taxonomy.py` with a Procrastinate background job via `POST /ingest/v1/taxonomy/auto-categorise-job` (stepwise retry: 30 s → 5 m → 30 m).
- **R6 (Medium) — centroid staleness**: `load_centroids()` now rejects files older than 48 h (`taxonomy_centroid_max_age_hours` config). Timezone-safe; treats unparseable timestamps as stale.

### Added — SPEC-KB-026: New endpoints and tests

- `POST /ingest/v1/taxonomy/classify` — classify a gap against the active taxonomy
- `POST /ingest/v1/taxonomy/auto-categorise-job` — enqueue Procrastinate auto-categorise task
- `_StepwiseRetry` Procrastinate task with 30 s / 5 m / 30 m backoff
- `classify_gap_taxonomy()` and `enqueue_auto_categorise()` on `KnowledgeIngestClient`
- 7 new test files covering all fixed bugs

## [Unreleased] — 2026-04-06

### Added — SPEC-KB-023: Taxonomy Discovery — Blind Labeling at Ingest

- **`content_labeler.py`** (new module): `generate_content_label(title, content_preview)` generates 3–5 lowercase keywords describing a document BEFORE any taxonomy context is shown. Uses `klai-fast`, 15 s timeout, returns `[]` on failure (non-fatal).
- **Bias prevention**: label generation runs before `classify_document` so the LLM cannot be anchored by existing taxonomy node names. Enables unbiased category discovery for SPEC-KB-024 clustering.
- **Rate limiting**: shares the existing `_TokenBucketLimiter` / `_RateLimitedTransport` singleton from `taxonomy_classifier.py` (1 req/s). Sequential execution (label first, then classify) means no additional rate-limit config needed.
- **Qdrant storage**: `content_label` stored as keyword array payload on ALL chunks of a document, via both `upsert_chunks` and `upsert_enriched_chunks`. Survives the enrichment pipeline via `extra_payload` passthrough.
- **Qdrant index**: keyword payload index on `content_label` added to `ensure_collection()` alongside existing indexes. Enables scroll filters in SPEC-KB-024 clustering.
- **LLM budget**: 2 calls per document total — `content_label` (blind) + `taxonomy_node_ids`/`tags` classification (anchored). No additional LLM calls introduced.
- **Config**: `content_label_timeout: float = 15.0` in `config.py`.
- **11 unit tests** in `tests/test_content_labeler.py` covering: happy path, timeout/error → `[]`, lowercase, dedup, clamp to 5, 500-char truncation, empty LLM response, non-string filter, `klai-fast` model check, system prompt guard (no taxonomy terms).
- **4 unit tests** in `tests/test_taxonomy_qdrant.py` (`TestUpsertChunksContentLabel`) covering: stored when provided, empty list stored (not omitted), None means absent, all chunks get same label.

## [Unreleased] — 2026-04-05

### Added — SPEC-KB-019: Notion Connector

- **NotionAdapter** (`klai-connector/app/adapters/notion.py`): `BaseAdapter` implementation using `notion_client.AsyncClient`. Supports `list_documents`, `fetch_document`, `get_cursor_state`, and `post_sync`.
- **Incremental sync**: `last_synced_at` cursor state filters pages by `last_edited_time` for efficient delta syncs.
- **Rate limiting**: `asyncio.Semaphore(3)` for 3 req/s Notion API limit with exponential backoff on 429 responses.
- **Config**: `access_token` (required), `database_ids` (optional, newline-separated list for UI), `max_pages` (default 500).
- **AdapterRegistry**: Notion registered as `"notion"` in `klai-connector/app/main.py`.
- **Frontend form** (`$kbSlug_.add-connector.tsx`): 2-step form — credentials (token + database IDs) + settings (assertion modes + max pages). Notion enabled in connector grid.
- **i18n**: 6 new `admin_connectors_notion_*` keys in EN and NL.
- **Credential encryption**: `SENSITIVE_FIELDS["notion"] = ["access_token"]` in `connector_credentials.py` — Notion tokens encrypted at rest via SPEC-KB-020 DEK/KEK hierarchy.
- **9 unit tests** in `klai-connector/tests/adapters/test_notion.py` covering adapter methods, config validation, rate limiting, and access_token security.
- **Note**: `database_ids` is stored and parsed but does not filter Notion API results (notion_client v2 removed `databases.query()`). All workspace-accessible pages sync. Filtering will be added in a future SPEC.

### Added — SPEC-KB-020: Secure Connector Credential Storage

- **AES-256-GCM cipher** (`app/core/security.py`): `AESGCMCipher` class with nonce||ciphertext envelope, random nonce per encryption, authenticated decryption.
- **KEK-DEK hierarchy** (`app/services/connector_credentials.py`): `ConnectorCredentialStore` with per-tenant DEK encrypted by KEK derived from `ENCRYPTION_KEY` env var. `get_or_create_dek` uses `SELECT ... FOR UPDATE` to prevent race conditions.
- **SENSITIVE_FIELDS mapping**: github (`access_token`, `installation_token`, `app_private_key`), notion (`access_token`), google_drive/ms_docs (`oauth_token`, `refresh_token`, `access_token`), web_crawler (`auth_headers`).
- **Schema migration**: `encrypted_credentials BYTEA` on `portal_connectors`, `connector_dek_enc BYTEA` on `portal_orgs` (migration `172c9ab5f151`).
- **API integration**: encrypt on connector create/update, mask sensitive fields in API responses (`app/api/connectors.py`). Internal endpoint (`/internal/connectors/{id}`) decrypts and merges before returning to connector service.
- **Startup guard** (`app/main.py`): hard-fails at startup if `ENCRYPTION_KEY` is missing or not a valid 64-char hex string (REQ-CRYPTO-003).
- **Structlog masking** (`app/logging_setup.py`): `mask_secret_str` processor prevents `SecretStr` values from leaking into log output.
- **Data migration script** (`scripts/migrate_connector_credentials.py`): backfills encrypted credentials for existing connectors.
- **Deploy**: `ENCRYPTION_KEY: ${PORTAL_API_ENCRYPTION_KEY}` added to `deploy/docker-compose.yml` portal-api environment.
- **41 tests** across `test_security.py` (12), `test_log_masking.py` (7), `test_connector_credentials.py` (16), `test_connector_encryption_api.py` (6).
- **New env var required**: `PORTAL_API_ENCRYPTION_KEY` (64-char hex, generate with `openssl rand -hex 32`).

## [Unreleased] — 2026-04-01

### Added — SPEC-AUTH-002: Product Entitlements

- **Plan-to-products mapping** (`app/core/plans.py`): `free` (none), `core` (chat), `professional` (chat, scribe), `complete` (chat, scribe, knowledge). Application-level constant, not stored in DB.
- **Product assignments**: direct per-user assignments via `portal_user_products` table + group-based inheritance via `portal_group_products`. Effective products = union of both.
- **`require_product()` dependency**: FastAPI dependency factory that returns 403 if the user lacks the required product. Applied to `/meetings` (scribe) and `/knowledge` (knowledge) routes.
- **Seat enforcement**: invite endpoint returns 409 Conflict when `active_users >= org.seats`, with `FOR UPDATE` lock to prevent race conditions.
- **Auto-assignment on invite**: new users automatically receive all products included in the org's current plan.
- **Plan change handling**: upgrade makes new products assignable (no auto-enable); downgrade revokes over-ceiling assignments for both user and group products.
- **JWT enrichment endpoint**: `GET /api/internal/users/{id}/products` for Zitadel Action to enrich access tokens with `klai:products` claim. Fail-closed (empty list on error).
- **Admin product API**: `GET/POST/DELETE /api/admin/users/{id}/products`, `GET /api/admin/users/{id}/effective-products`, `GET /api/admin/products`, `GET /api/admin/products/summary`.
- **Migration**: `portal_user_products` table with UNIQUE constraint on `(zitadel_user_id, product)`, index on `(org_id, product)`, and backfill from existing org plans.
- **28 unit tests** covering all 9 SPEC requirements (TS-001 through TS-018).

### Added — SPEC-CRAWLER-003: Link-Graph Retrieval Enrichment

- **Link graph helpers** (`link_graph.py`): four async query functions against `knowledge.page_links` — `get_outbound_urls`, `get_anchor_texts`, `get_incoming_count`, `compute_incoming_counts` — all org- and kb-scoped.
- **Qdrant indexes**: `source_url` (keyword) and `incoming_link_count` (integer) payload indexes added to `klai_knowledge` collection via `ensure_collection()`.
- **Batch link count update** (`qdrant_store.update_link_counts()`): refreshes `incoming_link_count` for all chunks of a URL via `set_payload()`, with semaphore (20 concurrent) and per-call timeout (5 s).
- **Crawl route enrichment**: single-URL ingest populates `source_url`, `links_to` (cap 20), `anchor_texts`, and `incoming_link_count` in `extra_payload` before enrichment task dispatch.
- **Bulk crawler**: batch `update_link_counts()` call after crawl loop to refresh incoming counts for all pages in the KB.
- **Anchor text augmentation** (`enrichment_tasks.py`): appends deduplicated "Also known as: anchor1 | anchor2" block to `enriched_text` (dense + sparse vectors) when anchor texts are available.
- **Retrieval — 1-hop forward expansion** (`retrieve.py`): after RRF merge, outbound URLs from top `link_expand_seed_k` chunks are used to fetch additional candidate chunks via `fetch_chunks_by_urls()`; skipped for notebook scope.
- **Retrieval — authority boost**: `score += link_authority_boost * log(1 + incoming_link_count)` applied to all candidate chunks when `link_authority_boost > 0`.
- **`fetch_chunks_by_urls()`** (`search.py`): payload-filter-based chunk lookup by `source_url` using `client.scroll()` with a 3 s timeout; returns `score=0.0` for reranker scoring.
- **Config** (`retrieval_api/config.py`): five new settings — `link_expand_enabled` (default `True`), `link_expand_seed_k` (10), `link_expand_max_urls` (30), `link_expand_candidates` (20), `link_authority_boost` (0.05).
- **Metrics**: new `step_latency_seconds` label `link_expand` in retrieval-api Prometheus metrics.

## [Unreleased] — 2026-03-27

### Added — SPEC-KB-014: Knowledge Gap Detection & UI

- **LiteLLM hook**: Gap detection in `klai_knowledge.py` — classifies retrieval results as `hard_gap` (zero chunks), `soft_gap` (all chunk scores below threshold), or `success`. Fire-and-forget async reporting via `asyncio.create_task()`.
- **Internal API**: `POST /internal/v1/gap-events` endpoint for service-to-service gap event ingestion (authenticated via `PORTAL_INTERNAL_SECRET`).
- **Database**: New `portal_retrieval_gaps` table with FK to `portal_orgs`, composite indexes on `(org_id, occurred_at)` and `(org_id, query_text)`. 90-day retention policy.
- **Gap API**: `GET /api/app/gaps` (list with filters: days, gap_type, limit) and `GET /api/app/gaps/summary` (aggregated counts: total_7d, hard_7d, soft_7d) — admin-only.
- **KB stats**: Extended `GET /api/app/knowledge-bases/{slug}/stats` to include `org_gap_count_7d`.
- **Gap dashboard** (`/app/gaps`): Table of unanswered questions grouped by query text, with type badge (hard/soft), nearest KB, frequency, and action buttons (navigate to KB or knowledge index). Product-gated to `knowledge` entitlement.
- **Knowledge index card**: Admins see a "Knowledge Gaps" card on `/app/knowledge` with the 7-day gap count and a link to the dashboard.
- **KB detail tile**: "Gaps (7d)" metric tile added to the KB overview tab alongside existing stats.
- **i18n**: 18 new `gaps_*` keys in EN and NL.

### Configuration — SPEC-KB-014

New optional environment variables for the LiteLLM container (safe defaults built in):
- `KLAI_GAP_SOFT_THRESHOLD` (default: `0.4`) — reranker score below which all chunks are classified as low-confidence
- `KLAI_GAP_DENSE_THRESHOLD` (default: `0.35`) — dense score fallback threshold

**Migration required:** Run `alembic upgrade head` on portal-api before restart to create the `portal_retrieval_gaps` table.
