---
id: SPEC-SEC-HYGIENE-001
version: 0.6.0
status: in-progress
created: 2026-04-24
updated: 2026-04-29
author: Mark Vletter
priority: low
tracker: SPEC-SEC-AUDIT-2026-04
---

# SPEC-SEC-HYGIENE-001: Security Hygiene Grouped Fixes

> **Scope warning:** v0.3.0 absorbs 21 additional hygiene items surfaced by
> the internal wave of reviews (klai-connector, klai-scribe, klai-retrieval-api,
> klai-knowledge-mcp, klai-mailer). The SPEC is now much larger than the
> original "single focused PR" framing. Implementers have explicit permission
> (see Out of Scope) to split HYGIENE-001 into per-service follow-up SPECs
> if PR review finds the aggregate unwieldy. The primary goal is that every
> item here has a REQ, a research note, and an AC — whether those ship in
> one PR or five is a call for /run.

## HISTORY

### v0.6.0 (2026-04-29) — portal slice in flight (PR #209)

Portal-slice (HY-19, HY-20, HY-21, HY-22, HY-23, HY-24, HY-27, HY-28)
implemented in 12 commits on `feature/SPEC-SEC-HYGIENE-001-claude`,
opened as PR #209 against `main`. All 8 v0.2.0 P3 findings ship in a
single PR per the v0.2.0 assumption.

Per-AC summary (all green; full per-commit detail in `progress.md`):

- **HY-19 / REQ-19** — `app/services/signup_email_rl.py` (NEW) +
  wiring in `app/api/signup.py`. Redis INCR + EXPIRE on
  `signup_email_rl:<sha256(normalised_email)>` with 24h window;
  3 successful signups before 4th → 429. Email normalisation
  (lowercase + strip `+alias`) so `Mark+signup@voys.nl` shares a
  counter with `mark@voys.nl`. Plaintext emails never enter Redis or
  logs (sha256). Fail-open on Redis unreachable. 16 tests.
- **HY-20 / REQ-20** — `app/api/auth.py` `_validate_callback_url` is
  now async; additionally requires the first subdomain label to be in
  the active `portal_orgs.slug` set (deleted_at IS NULL). 60s in-process
  TTL cache + explicit `invalidate_tenant_slug_cache()` hooks at
  signup.py (both flows), orchestrator.py soft-delete, and
  retry_provisioning.py un-soft-delete. localhost / 127.0.0.1 / bare
  apex preserved. 7 tests + conftest pre-populate so existing
  login/audit suites don't trigger DB load.
- **HY-21 / REQ-21** — `app/api/auth_bff.py` `_safe_return_to`
  percent-decodes once before all other checks, rejects backslash
  prefix (`/\\evil`), encoded slash (`/%2fevil`), and `\\\\` anywhere.
  Returns ORIGINAL value on success so legitimate `?foo=bar%20baz`
  query params survive. 12 parametrised cases + None guard.
- **HY-22 / REQ-22** — `app/api/signup.py` `password_strength` moved
  from `@field_validator` to `@model_validator(mode="after")` so
  zxcvbn can read email + first/last name + company_name as
  `user_inputs`. Score floor 3 (0-4 scale). Length-12 stays as the
  first gate. Falls back to length-only with structlog error if zxcvbn
  import fails at module load. 7 tests including the
  `Mark.Vletter`-loses-a-point regression for the user_inputs wiring.
  New runtime dependency: `zxcvbn>=4.5,<5.0` (pure Python, MIT).
- **HY-23 / REQ-23** — `app/api/partner.py` `widget_config` docstring
  expanded with explicit "Security model" section: Origin is UX-only
  (not a security boundary), widget_id is the public identifier, the
  HS256 session_token is the actual access-control mechanism. The
  pre-existing `@MX:REASON` updated to reference the docstring's
  framing + SPEC-SEC-HYGIENE-001 REQ-23. Forward-link to REQ-24's
  blast-radius narrowing. 6 docstring/MX assertions.
- **HY-24 / REQ-24** — `app/services/widget_auth.py`
  `_derive_tenant_key(master, slug)` uses HKDF-SHA256 with salt
  `b"klai-widget-jwt-v1"`, info=tenant slug, length=32. Both
  `generate_session_token` and `decode_session_token` take
  `tenant_slug`. Decode peeks at the unverified payload to read
  `org_id`, looks up the slug, then verified-decodes with the derived
  key. Cross-tenant tokens raise `jwt.InvalidSignatureError`
  specifically — symmetric (A→B AND B→A) regression assertion. 6 tests.
- **HY-27 / REQ-27** — `app/services/tenant_matcher.py` CACHE_TTL
  reduced from 5 min → 60 s (Option A). Module + function docstrings
  document the rationale. 11 tests including the deterministic
  expired-entry → re-fetch behaviour test.
- **HY-28 / REQ-28** — `app/main.py` `_should_expose_docs(settings)`
  helper returns true iff `debug AND portal_env != "production"`. New
  `portal_env: str = "production"` Settings field +
  `@model_validator _no_debug_in_production` raises ValueError at
  startup for the catastrophic combo. `deploy/docker-compose.yml`
  forwards `PORTAL_ENV: ${PORTAL_ENV:-production}`. Both vars default
  safely so the validator NEVER fires on a missing env — no
  klai-infra/.env.sops change required (per `validator-env-parity`
  pitfall). 7 tests.

Sync-phase additions (this commit):

- `_should_expose_docs` test inlined a duplicate to avoid importing
  `app.main` from a non-main test module — the import triggered
  `setup_logging()` + bound the structlog wrappers in
  `app.middleware.klai_cors` BEFORE the cors-allowlist tests could
  reconfigure structlog, masking the rejected-event capture. Captured
  the lesson in `progress.md` under "Lessons learned"; the long-term
  fix is an import-graph lint rule that flags `from app.main import …`
  in tests.
- `@MX:ANCHOR` added to `invalidate_tenant_slug_cache` (fan_in = 3:
  signup.py × 2 + orchestrator.py + retry_provisioning.py). Per the
  MX protocol P1 rule (fan_in ≥ 3 → mandatory anchor) this would have
  blocked at sync Phase 0.6.
- `tech.md` Portal Backend section: added `zxcvbn` (>=4.5, <5.0) row
  to the dependency table.

Verification:

- 1317 / 1317 backend tests pass (full suite, excluding
  `tests/test_provisioning.py` which needs Docker). The cors-allowlist
  test isolation flake from the merge of REQ-28 + SPEC-SEC-CORS-001
  was closed by inlining `_should_expose_docs` in the test (above).
- Ruff + pyright clean on every changed file.
- `validator-env-parity` HIGH pitfall: REQ-28 validator only fires on
  the combination DEBUG=true AND PORTAL_ENV=production; both vars
  default to safe values so no klai-infra/.env.sops change required.
  REQ-24 HKDF: `widget_jwt_secret` field default is `""` (legacy
  behaviour) — runtime widget-token validation catches an empty-secret
  misconfig the first time a token is decoded; documented as
  R-24-deploy in progress.md.

Outstanding HYGIENE-001 slices: knowledge-mcp (HY-45..HY-48), mailer
(HY-49..HY-50), retrieval-api (HY-39..HY-44, PR #188 open).

### v0.5.0 (2026-04-28) — connector slice closed-out (followup landed)

Followup PR landed direct-to-main as 3 commits + merge `6e92f68d`:
- `e7967255` — REQ-30.3 mechanically closed: `quality` job added to
  `.github/workflows/klai-connector.yml` runs `uv run ruff check .`
  and blocks `build-push` on failure. CI ran green on first push to
  main (4m9s). Plus 9 small lint-debt fixes (notion.py constants moved
  to module scope, `SyncStatus` → `enum.StrEnum`, alembic import order,
  models/connector.py E501) so the new step passes on the existing
  tree without scope creep into untouched code.
- `0770056e` — HY-31 HTTP-niveau dekking: 3 new integration tests in
  `tests/test_compute_fingerprint.py` replace `httpx.AsyncClient` itself
  (vs the original tests which patched `_fetch_page_markdown`). Pin the
  POST `/crawl` payload shape, the Bearer-header contract (parametrized
  over `crawl4ai_internal_key` set/unset), and the dict/string
  `markdown` response branches. Plus pyright strict cleanup on
  `routes/fingerprint.py`: 11 → 0 strict warnings via explicit local
  annotations + per-line `# pyright: ignore[reportUnknownVariableType]`
  on JSON-boundary unknowns.
- `7833fe6f` — AC-32 implementation note documenting the rate-limit
  default-deviation (shipped 120/30 vs SPEC literal 60/10), with the
  /run research backing (Auth0 120, Heroku 75, Slack 1200) and a
  reminder that the AC test itself sets limits to 60/10 via env
  override so the SPEC-literal boundaries are still exercised.

CI/quality state on main after followup:
- `Build and push klai-connector` workflow: completed success, includes
  the new `quality` job.
- Ruff clean across the whole connector tree (was 5 pre-existing errors
  before followup).
- Pyright strict on `routes/fingerprint.py`: 0 errors (was 11).
- Connector test count: 305 passing + 11 pre-existing `_image_transport`
  failures (image-storage scope, separate SPEC).

Status remains `in-progress`: connector slice fully closed-out; scribe
slice already shipped at v0.4.0; remaining slices (portal HY-19..HY-28,
retrieval HY-39..HY-44, MCP HY-45..HY-48, mailer HY-49..HY-50) still
outstanding.

### v0.4.0 (2026-04-27) — scribe + connector slices shipped
- **Scribe slice** (HY-33..HY-38) shipped via PR #179, merge commit `4463bb3d`.
  Production deploy verified on core-01: container running new image, alembic
  upgraded `0006 → 0007_c5f9e3a4` (manual `docker exec` since the scribe-api
  CI workflow does not run alembic), `/health` returns 200, reaper succeeded
  on second startup (first startup logged `scribe_startup_reaper_failed` as
  expected before the migration applied — caught by the lifespan try/except).
- **Connector slice** (HY-30..HY-32) shipped as direct commits on main:
  `10715d18` (HY-30 HTTPException + ruff F821 contract), `e4ddaa8b` (HY-31
  compute-fingerprint rewire to crawl4ai), `e7efe1db` (HY-32 per-org Redis
  sliding-window rate limit). Pushed without a PR/merge commit.
- Status flipped `draft → in-progress`: 9 of 29 findings shipped (6 scribe + 3
  connector). Remaining slices (portal HY-19..HY-28, retrieval HY-39..HY-44,
  MCP HY-45..HY-48, mailer HY-49..HY-50) stay outstanding and may be split
  into per-service follow-up SPECs per Out-of-Scope §v0.3.0.
- See `.moai/specs/SPEC-SEC-HYGIENE-001/progress.md` for both per-slice
  checklists with implementation notes and (for scribe) deploy verification
  chain.

### v0.3.0 (2026-04-24)
- Expanded scope to absorb 21 additional findings from the internal-wave review
  across klai-connector, klai-scribe, klai-retrieval-api, klai-knowledge-mcp,
  and klai-mailer. New REQ groups REQ-30..REQ-50.
- Added per-service sub-sections under Requirements so each service can be
  reviewed (and potentially extracted into its own follow-up SPEC) independently.
- Added scope-warning note at top; Out-of-Scope now grants explicit permission
  to split this SPEC if /run finds it too large for one PR.
- Updated Environment with all new file paths (connector routes, scribe audio
  pipeline, retrieval-api health + logging + rate-limit + JWT-path, MCP DNS
  rebinding + kb_slug + page_path).
- Updated Assumptions — cross-service scope is acknowledged.
- research.md and acceptance.md each get an "Internal-wave additions
  (2026-04-24)" section; existing §19..§28 content preserved verbatim.

### v0.2.0 (2026-04-24)
- Expanded stub into full EARS SPEC via `/moai plan`
- Added per-finding requirement sub-sections (REQ-19.x .. REQ-28.x)
- Added research.md (current-state synopsis per finding, library candidate review)
- Added acceptance.md (testable scenarios, one per finding)
- Scope unchanged from v0.1.0: 8 P3 findings from Cornelis audit 2026-04-22

### v0.1.0 (2026-04-24)
- Stub created from SPEC-SEC-AUDIT-2026-04 (Cornelis audit 2026-04-22)
- Priority P3 — grouped hygiene fixes with no direct critical exploit

---

## Goal

Close the 29 remaining hygiene findings:
- The eight original P3 items from the April 2026 Cornelis audit (#19-#28,
  portal-api). These are the v0.2.0 baseline and ship first.
- Twenty-one additional items (HY-30..HY-50) from the internal-wave review
  spanning klai-connector, klai-scribe, klai-retrieval-api, klai-knowledge-mcp,
  and klai-mailer. These are defense-in-depth, landmine-prevention, and
  hygiene improvements — none is an active exploit, but collectively they
  reduce the attack surface, harden defense-in-depth, and prevent the same
  items from re-appearing in the next audit.

Each finding gets either (a) a minimal code change with a regression test,
or (b) a documented acceptance with a code comment/docstring justifying why
no code change is required. No single finding is allowed to expand beyond
its minimal fix; structural changes (migrating widget JWT to asymmetric
signing, full JWKS-failure circuit-breaker redesign, switching FastMCP
to a different transport) are explicitly Out of Scope and tracked as
separate future SPECs.

---

## Success Criteria

- All 29 findings either have a passing regression test under the affected
  service's `tests/` directory or a documented acceptance in code/docs.
- No public consumer of `/api/signup`, `/api/auth/*`, `/partner/v1/widget-config`,
  `/api/v1/connectors`, `/api/v1/compute-fingerprint`, `/api/v1/transcribe`,
  `/health`, `/retrieve`, `/internal/*`, or `/docs` breaks as a result of
  this SPEC.
- `ruff check` (including F821) and `pyright` pass clean on every changed file
  across all affected services.
- `widget_jwt_secret` rotation is documented (runbook or inline comment) so
  the HKDF-derivation change does not silently invalidate live widget sessions.
- The SPEC-SEC-AUDIT-2026-04 tracker can mark all 29 findings closed (or
  handed off to split follow-up SPECs if /run chooses to split).

---

## Environment

### Original (v0.2.0) paths

- **Portal backend:** Python 3.13, FastAPI, SQLAlchemy async, Pydantic v2,
  structlog, `uv`. Main code at `klai-portal/backend/app/`.
- **Rate limiting backend (for #19):** Redis via the existing `get_redis_pool()`
  helper and the sliding-window pattern in
  `klai-portal/backend/app/api/partner_dependencies.py:check_rate_limit`.
- **Edge proxy (for #19 visibility):** Caddy at `deploy/caddy/Caddyfile` — the
  existing `@portal-api-sensitive` zone (lines 146-158) already limits
  `/api/signup` + `/api/billing/*` at 10 events/min per IP. This SPEC layers
  a per-email Redis limit on top inside the application, not in Caddy.
- **Password policy library (for #22):** `zxcvbn-python` (pure-Python port of
  Dropbox's zxcvbn; PyPI `zxcvbn`). Acceptable new dependency; MIT licensed,
  no native extensions, ~400KB installed.
- **Key derivation (for #24):** Python stdlib `cryptography.hazmat.primitives.kdf.hkdf.HKDF`
  — already transitively available via `cryptography` (JWT/Fernet dep).
- **OIDC layer (for #20):** Zitadel already validates `redirect_uri` against
  the registered OIDC client list before issuing a callback URL, so the
  portal-level `_validate_callback_url` is defense-in-depth only. The
  allowlist check lives on top of that, not in place of it.
- **Observability:** structlog JSON via Alloy → VictoriaLogs (30d). Every
  new rejection/violation emits a stable structlog event key so the audit
  of these fixes can be done via LogsQL queries without adding metrics.

### Internal-wave (v0.3.0) paths

- **klai-connector:** Python 3.13, FastAPI, SQLAlchemy async, `uv`. Key files:
  - `klai-connector/app/routes/connectors.py` (CRUD, HTTPException NameError)
  - `klai-connector/app/routes/fingerprint.py` (compute-fingerprint, dead import)
  - `klai-connector/app/middleware/auth.py` (Zitadel surface, no rate limit)
- **klai-scribe:** Python 3.13, FastAPI, worker pattern. Key files:
  - `klai-scribe/scribe-api/app/services/audio_storage.py` (path construction,
    finalize race)
  - `klai-scribe/scribe-api/app/core/auth.py` (Zitadel JWT decode)
  - `klai-scribe/scribe-api/app/api/transcribe.py` (transcription orchestration)
  - `klai-scribe/scribe-api/app/api/health.py` (configurable whisper URL)
  - `klai-scribe/scribe-api/app/main.py` (CORS regex)
- **klai-retrieval-api:** Python 3.13, FastAPI, asyncpg + Qdrant + FalkorDB.
  Key files:
  - `klai-retrieval-api/retrieval_api/main.py` (/health blocking + topology leak)
  - `klai-retrieval-api/retrieval_api/services/events.py` (unbounded `_pending`)
  - `klai-retrieval-api/retrieval_api/logging_setup.py` (X-Request-ID /
    X-Org-ID log poisoning)
  - `klai-retrieval-api/retrieval_api/services/rate_limit.py` (Redis fail-open)
  - `klai-retrieval-api/retrieval_api/services/search.py` (TRY antipattern)
  - `klai-retrieval-api/retrieval_api/middleware/auth.py` (JWKS-path 20s
    worker-DoS landmine)
- **klai-knowledge-mcp:** Python 3.13, FastMCP transport, no FastAPI. Key file:
  - `klai-knowledge-mcp/main.py` (DNS rebinding, kb_slug, page_path, no RL)
- **klai-mailer:** Python 3.13, FastAPI, Zitadel webhook signatures. Key files
  (overlap with SPEC-SEC-MAILER-INJECTION-001; kept here defense-in-depth):
  - mailer webhook signature verification module (path recorded during /run
    via `Grep _verify_zitadel_signature`).

### Shared infrastructure

- **Observability:** structlog JSON via Alloy → VictoriaLogs. All services
  already route logs through Alloy; no infra change required for REQ-41
  (X-Request-ID / X-Org-ID length cap) — the cap lives in
  `logging_setup.py` `RequestContextMiddleware`.
- **Rate-limit backend (retrieval + MCP):** Redis via shared helper. Note
  retrieval's current fail-open behaviour (REQ-42) is documented, not
  changed, in this SPEC.
- **CI:** ruff F821 is currently enabled project-wide but flagged items
  (REQ-30) slipped through because the affected package was excluded from
  the linted set at the connector service level. REQ-30 includes
  re-enabling ruff F821 on `klai-connector/`.

## Assumptions

### v0.2.0 assumptions (unchanged)

- `zxcvbn-python` is acceptable as a new runtime dependency (reviewed against
  `.claude/rules/klai/pitfalls/process-rules.md` — no conflicts).
- Reducing `tenant_matcher._cache` TTL to 60 seconds does not materially
  increase Zitadel lookup load. Profile first during /run.
- Widget customers can tolerate a JWT signing-secret change if rotation is
  coordinated. HKDF derivation is deterministic per-tenant, so rotating
  the master `WIDGET_JWT_SECRET` still breaks live widgets — this SPEC does
  NOT rotate the master secret, it only derives a per-tenant key from it.
- All eight v0.2.0 findings ship in a single portal-api PR. No partial delivery.

### v0.3.0 assumptions (new)

- Cross-service scope: /run MAY legitimately split HYGIENE-001 into per-
  service follow-up SPECs (HYGIENE-001a..e) if PR review determines the
  aggregate is too large. See Out-of-Scope for explicit permission.
- klai-connector `app/adapters/webcrawler` module is currently deleted or
  unreachable (HY-31). /run verifies by grepping for the module name and
  either removes the consumer endpoint or reinstates the adapter — both
  are acceptable fixes.
- klai-scribe `Path.resolve().is_relative_to(base)` check can be wrapped
  around the existing `audio_path` construction without a schema migration.
- klai-retrieval-api `/health` latency budget tolerates async wrappers for
  the currently-sync FalkorDB ping (HY-39). A tiny thread-pool hop is
  acceptable vs. event-loop blocking.
- klai-knowledge-mcp is currently NOT exposed via Caddy. HY-45 is a
  landmine fix, not an active exploit. If /run finds the MCP becomes
  internet-reachable between plan and run, this AC is upgraded to P1.
- klai-mailer HY-49 and HY-50 are defense-in-depth duplicates of items
  that MAY also be claimed by SPEC-SEC-MAILER-INJECTION-001. Drop from
  HYGIENE-001 only if that SPEC explicitly claims them; otherwise leave
  them here so nothing falls between the cracks.

## Risks

### v0.2.0 risks (unchanged)

- **R1:** zxcvbn adds a runtime import cost (~30 MB RAM for its dictionary).
  Measured acceptable on the portal-api memory budget (currently ~250 MB idle,
  512 MB limit). Mitigated by lazy-loading the dictionary on first call.
- **R2:** Per-email signup rate limit (#19) could be gamed with `+alias@`
  gmail-style addresses. Mitigated by normalising the email (lowercase +
  strip `+alias`) before keying the Redis counter. Explicit in REQ-19.3.
- **R3:** Tenant-slug allowlist (#20) requires a portal_orgs.slug query on
  every callback. Mitigated by caching the slug set for 60 seconds in
  process memory, invalidated on tenant create/delete. See REQ-20.2.
- **R4:** Reducing `tenant_matcher` TTL to 60s (#27) increases Zitadel
  lookup rate ~5x during peak invite processing. Mitigated by accepting
  the load (Zitadel handles >100 req/s comfortably) OR by keeping TTL
  5min and adding an explicit invalidation hook on plan change. Both
  variants are acceptable per REQ-27.1.
- **R5:** Double-gating `/docs` on `DEBUG` AND `PORTAL_ENV != production`
  (#28) requires a new `PORTAL_ENV` pydantic-settings field. Existing
  deployments may not set it; default must be conservative (`production`
  if unset).

### v0.3.0 risks (new)

- **R6:** HY-30 (connector HTTPException NameError) is technically an active
  500 bug, not hygiene. Fix is trivial (one import) but the unit-test
  regression check must include EVERY route that calls `raise
  HTTPException(...)` in `connectors.py` lines 75/90/121 — otherwise one
  code path stays broken. Coverage is the risk, not the fix itself.
- **R7:** HY-33/34 (scribe audio path traversal) depends on Zitadel `sub`
  charset. Zitadel's default `sub` is a numeric string, but custom auth
  flows can return arbitrary UTF-8. The regex whitelist in REQ-34 MUST
  tolerate every legitimate Zitadel sub format or scribe breaks for
  legitimate users.
- **R8:** HY-39 (retrieval /health async) — wrapping the falkordb sync
  `.ping()` in `asyncio.to_thread` costs one thread context-switch per
  /health call (currently polled at 10s interval by Caddy). The overhead
  is negligible, but the pattern must be consistent with other services
  that use the same client (document in research).
- **R9:** HY-44 (JWKS 20s worker-DoS) is CONDITIONAL on `ZITADEL_ISSUER`
  being unset at the same time as attacker sending `Authorization: Bearer`.
  Current deployments always set ZITADEL_ISSUER, so the landmine is
  dormant. The fix (short-circuit to 401 when `jwt_auth_enabled=False`
  and Bearer token present) is ~3 lines but must not regress legitimate
  behaviour where local-dev sometimes flips the flag.
- **R10:** HY-48 (personal-KB kb_slug guessability) overlaps with
  SPEC-SEC-IDENTITY-ASSERT-001. Risk is duplicate work. Mitigation:
  HYGIENE-001 only files the hygiene angle (guessable slug format),
  leaving the membership-check structural fix to IDENTITY-ASSERT-001.
- **R11:** HY-49 / HY-50 overlap with SPEC-SEC-MAILER-INJECTION-001. Same
  dedup story. If that SPEC ships first and covers both, HYGIENE-001
  drops them in the closing PR description.

---

## Out of Scope

### v0.2.0 out-of-scope (unchanged)

- Replacing IDP-callback subdomain trust with explicit OIDC client config
  (separate strategic SPEC; Zitadel already provides the primary defence
  layer — see SPEC-SEC-HYGIENE-001 research.md §20).
- Migrating widget JWT to asymmetric signing (ES256/EdDSA). Future SPEC
  `SPEC-WIDGET-JWT-ASYMMETRIC`.
- Replacing zxcvbn with a HIBP-integrated breach check. Future privacy
  SPEC tracked separately.
- Migrating `tenant_matcher._cache` to Redis. In-process cache with
  short TTL is sufficient for current scale (<100 invites/day).
- Rewriting `/api/signup` to be synchronous end-to-end. Background
  provisioning stays as-is; this SPEC only adds the rate-limit layer
  and references the existing SPEC-PROV-001 stuck-detector runbook.

### v0.3.0 out-of-scope (new)

- **SPEC split permission (explicit):** /run MAY split HYGIENE-001 into
  per-service follow-up SPECs:
  - `SPEC-SEC-HYGIENE-CONNECTOR-001` (HY-30..HY-32)
  - `SPEC-SEC-HYGIENE-SCRIBE-001` (HY-33..HY-38)
  - `SPEC-SEC-HYGIENE-RETRIEVAL-001` (HY-39..HY-44)
  - `SPEC-SEC-HYGIENE-MCP-001` (HY-45..HY-48)
  - `SPEC-SEC-HYGIENE-MAILER-001` (HY-49..HY-50)
  (The original HY-19..HY-28 v0.2.0 batch stays under HYGIENE-001 proper.)
  The decision is at /run's discretion. A split keeps each PR reviewable;
  merging stays fine if the aggregate diff is manageable.
- Replacing FastMCP with a different MCP transport to get DNS-rebinding
  protection by default (HY-45). Future SPEC `SPEC-MCP-TRANSPORT-001`.
- Structural rework of knowledge-mcp personal-KB identity derivation
  (HY-48). Tracked by SPEC-SEC-IDENTITY-ASSERT-001.
- Structural rework of retrieval-api rate-limit fail-closed (HY-42).
  Documented here as defense-in-depth gap; fail-closed is a product-
  availability decision tracked separately.
- Comprehensive MCP rate-limiting (HY-47). This SPEC adds a basic token-
  bucket gate; a proper per-tenant quota system is a separate SPEC
  (`SPEC-MCP-QUOTAS-001`).
- Full input-validation audit of klai-docs URL handlers (HY-46
  CANNOT-VERIFY blast radius). Tracked as follow-up research spike.

---

## Findings table (v0.3.0)

| ID     | Service            | Status      | Severity      | REQ group     |
|--------|--------------------|-------------|---------------|---------------|
| #19    | portal-api         | full        | P3            | REQ-19.x      |
| #20    | portal-api         | full        | P3            | REQ-20.x      |
| #21    | portal-api         | full        | P3            | REQ-21.x      |
| #22    | portal-api         | full        | P3            | REQ-22.x      |
| #23    | portal-api         | docs-only   | P3            | REQ-23.x      |
| #24    | portal-api         | full        | P3            | REQ-24.x      |
| #27    | portal-api         | full        | P3            | REQ-27.x      |
| #28    | portal-api         | full        | P3            | REQ-28.x      |
| HY-30  | klai-connector     | full        | P2 (active)   | REQ-30        |
| HY-31  | klai-connector     | full        | P3            | REQ-31        |
| HY-32  | klai-connector     | full        | P3            | REQ-32        |
| HY-33  | klai-scribe        | full        | P3            | REQ-33        |
| HY-34  | klai-scribe        | full        | P3            | REQ-34        |
| HY-35  | klai-scribe        | full        | P3            | REQ-35        |
| HY-36  | klai-scribe        | full        | P3            | REQ-36        |
| HY-37  | klai-scribe        | full        | P3            | REQ-37        |
| HY-38  | klai-scribe        | docs-only   | P3            | REQ-38        |
| HY-39  | klai-retrieval-api | full        | P3            | REQ-39        |
| HY-40  | klai-retrieval-api | full        | P3            | REQ-40        |
| HY-41  | klai-retrieval-api | full        | P3            | REQ-41        |
| HY-42  | klai-retrieval-api | docs-only   | P3            | REQ-42        |
| HY-43  | klai-retrieval-api | full        | P3            | REQ-43        |
| HY-44  | klai-retrieval-api | full        | P3 (landmine) | REQ-44        |
| HY-45  | klai-knowledge-mcp | docs-only   | P3 (landmine) | REQ-45        |
| HY-46  | klai-knowledge-mcp | stub        | P3 (partial)  | REQ-46        |
| HY-47  | klai-knowledge-mcp | full        | P3            | REQ-47        |
| HY-48  | klai-knowledge-mcp | docs-only   | P3            | REQ-48        |
| HY-49  | klai-mailer        | full        | P3 (DiD)      | REQ-49        |
| HY-50  | klai-mailer        | docs-only   | P3 (DiD)      | REQ-50        |

**Status legend:**
- `full` — code change + regression test required.
- `docs-only` — docstring / comment / MX:REASON update; no code change.
- `stub` — partial coverage in this SPEC; full details deferred to the
  follow-up split SPEC. Currently applies only to HY-46 because its blast
  radius crosses into klai-docs and requires a route-handler audit before
  a concrete REQ can be written.

---

## Requirements

EARS format. Requirements are organised by finding number. v0.2.0 findings
(#19-#28) remain verbatim below; v0.3.0 additions (HY-30..HY-50) follow,
grouped by service.

### Finding #19 — Per-email signup rate limit

**Current state:** `POST /api/signup` is rate-limited at the Caddy edge by
the `@portal-api-sensitive` zone (10 events/min per client IP across
`/api/signup` + `/api/billing/*`). The per-IP limit does not prevent a
single actor cycling email addresses from a single IP, nor does it notice
a single email attempting signup repeatedly from many IPs (botnet style).
Background provisioning (`signup.py:99-209`) is already queued via
`BackgroundTasks` and covered by SPEC-PROV-001's stuck-detector; no
change needed there.

- **REQ-19.1:** WHEN `POST /api/signup` is called AND the normalised email
  has already been used for 3 successful signups within the last 24 hours,
  THE service SHALL return HTTP 429 with response body
  `{"detail": "Too many signup attempts for this email. Please try again tomorrow."}`
  and log a structlog event `signup_email_rate_limited` with the
  normalised email hash (not the email itself).
- **REQ-19.2:** The per-email rate limit SHALL be implemented as a Redis
  INCR + EXPIRE counter keyed on `signup_email_rl:<sha256(normalised_email)>`
  with a 24-hour window. A SHA-256 hash is used so plaintext emails never
  enter Redis.
- **REQ-19.3:** Email normalisation for the rate-limit key SHALL lowercase
  the address and strip any `+alias` suffix from the local-part (e.g.
  `Mark+signup@voys.nl` and `mark@voys.nl` share a counter). The normalised
  form SHALL only be used for the rate-limit key — the account is still
  created with the user-supplied email.
- **REQ-19.4:** WHEN Redis is unreachable, THE rate-limit check SHALL fail
  OPEN (allow the signup) AND log a structlog warning
  `signup_email_rl_redis_unavailable` so observability sees the degradation.
  This mirrors the partner-API pattern in `partner_dependencies.py`.
- **REQ-19.5:** The per-email limit SHALL run AFTER Pydantic validation
  (so malformed emails never hit Redis) AND BEFORE the Zitadel org-creation
  call (so rejected attempts never touch Zitadel quota).
- **REQ-19.6:** Background tenant provisioning at `signup.py:201`
  (`background_tasks.add_task(provision_tenant, …)`) SHALL remain unchanged;
  the existing SPEC-PROV-001 stuck-detector runbook is the canonical
  mitigation for stuck provisioning, referenced from the signup docstring.

### Finding #20 — Callback URL subdomain allowlist

**Current state:** `_validate_callback_url` at `auth.py:138-159` accepts any
hostname that equals `settings.domain` (`getklai.com`) OR ends with
`.getklai.com`. This trusts every past, present, and future subdomain — an
attacker who controls any `*.getklai.com` subdomain (e.g. via a dangling
DNS record) could direct callbacks to themselves. Zitadel's own
`redirect_uri` validation is the primary defence; this requirement adds
a second, tenant-explicit layer.

- **REQ-20.1:** WHEN `_validate_callback_url(url)` is called AND the
  hostname is not `localhost`, `127.0.0.1`, or exactly `settings.domain`,
  THE function SHALL additionally verify that the subdomain label
  (the portion before the first `.`) appears in the active
  `portal_orgs.slug` allowlist. IF the subdomain is not in the allowlist,
  the function SHALL raise `HTTPException(502)` with the same generic
  error message as today ("Login failed, please try again later") and
  log `callback_url_subdomain_not_allowlisted` with the hostname.
- **REQ-20.2:** The allowlist SHALL be cached in process memory for 60
  seconds. Cache invalidation SHALL happen explicitly at tenant
  create / soft-delete (called from `provisioning/orchestrator.py` and
  the offboarding flow) AND implicitly via the 60-second TTL. Miss rate
  on the cache SHALL be emitted as `tenant_slug_allowlist_cache_miss`
  for observability.
- **REQ-20.3:** The `localhost` and `127.0.0.1` short-circuits at
  `auth.py:150` SHALL be preserved unchanged — they remain the local-dev
  escape hatch and are safe because Zitadel registers them explicitly
  as dev redirect URIs.

### Finding #21 — `_safe_return_to` backslash and percent-decode

**Current state:** `_safe_return_to` at `auth_bff.py:399-404` rejects
return-to values that start with `//` or contain `://` but does not:
(a) reject backslash-leading paths like `/\evil.com`, which some browsers
normalise to `//evil.com`, and (b) percent-decode once before checking,
allowing `/%2fevil.com` to bypass the `//` guard.

- **REQ-21.1:** WHEN `_safe_return_to(value)` is called, THE function
  SHALL percent-decode the value exactly once BEFORE all subsequent checks,
  so encoded slashes and backslashes are evaluated in their decoded form.
- **REQ-21.2:** The function SHALL reject (return `"/app"`) any value
  whose decoded form:
  - Is empty, OR
  - Does not start with `"/"`, OR
  - Starts with `"//"` (protocol-relative URL), OR
  - Starts with `"/\"` (browser-normalised protocol-relative), OR
  - Contains `"://"` anywhere in the decoded string, OR
  - Contains `"\\"` (double backslash, browser-normalised path-traversal).
- **REQ-21.3:** The function SHALL continue to return the ORIGINAL
  (non-decoded) value when the checks pass, so legitimate return-to
  paths with URL-encoded query parameters survive intact.
- **REQ-21.4:** A unit test SHALL exist at
  `klai-portal/backend/tests/test_auth_bff_return_to.py` covering at
  minimum: `/\evil.com`, `/%2fevil.com`, `//evil.com`, `https://evil.com`,
  `/app/dashboard?foo=bar%20baz`, empty string, `None`-equivalent.

### Finding #22 — Password policy strength check

**Current state:** `SignupRequest.password_strength` at `signup.py:53-58`
accepts any password with length ≥ 12 characters. A 12-character password
of `Password1234` or `aaaaaaaaaaaa` passes; so does `123456789012` if a
user types it. No dictionary check, no entropy estimate.

- **REQ-22.1:** THE `password_strength` validator SHALL reject any password
  whose zxcvbn score is less than 3 (on the 0-4 scale). Rejection SHALL
  return the Pydantic validation error message
  `"Wachtwoord is te zwak. Kies een langer of minder voorspelbaar wachtwoord."`
  (Dutch default; English variant for `preferred_language="en"` accounts
  is not required since validation runs before preferred_language is set).
- **REQ-22.2:** The minimum length of 12 characters SHALL be retained as
  the FIRST gate (zxcvbn is only invoked if length ≥ 12). This matches
  the OWASP ASVS V2.1.1 guidance and keeps the fast path fast.
- **REQ-22.3:** zxcvbn SHALL be invoked with the user's `email`,
  `first_name`, `last_name`, and `company_name` as the `user_inputs`
  argument so "Mark2026!!MarkVletter" scores low against the user's own
  PII. Wiring this through requires moving `password_strength` from a
  field-level `@field_validator` to a model-level `@model_validator`.
- **REQ-22.4:** IF zxcvbn import fails at module load time (missing dep
  in a misconfigured deployment), THE validator SHALL fall back to the
  current length-only check AND log a structlog error
  `zxcvbn_unavailable_falling_back_to_length_check` so deployment issues
  are visible without breaking user signups.

### Finding #23 — Widget-config Origin header documentation

**Current state:** `GET /partner/v1/widget-config` at `partner.py:388-481`
validates the `Origin` header against a per-widget `allowed_origins` list
and rejects mismatches with 403. Auditor concern: the `Origin` header is
spoofable by non-browser clients (e.g. curl, custom integrations), so an
attacker who knows a widget_id can fetch its config despite the Origin
check. **Per the Cornelis audit the finding is PARTIAL, because downstream
chat and retrieval endpoints are gated by the HS256 JWT issued to the
widget — the Origin check is a UX hint (stops a different tenant's site
from embedding this widget), not a security boundary.** This SPEC
documents that explicitly so the next audit does not re-file it.

- **REQ-23.1:** The docstring of `widget_config` at `partner.py:394-410`
  SHALL be updated to state explicitly:
  - The `Origin` header check is UX-gating, not a security boundary.
  - The primary identifier is `widget_id` (the URL `id` query parameter).
  - Downstream security (chat completions, KB retrieval) is enforced by
    the HS256 JWT `session_token`, which carries `wgt_id`, `org_id`,
    and allowed `kb_ids`.
  - Non-browser clients that spoof `Origin` cannot escalate privilege
    because they receive the same scoped token.
- **REQ-23.2:** The `allowed_origins` field description in the widget
  admin docs (`klai-portal/frontend/docs/widgets/*.md` if present, else
  inline in the `widgets` table migration comment) SHALL be updated to
  call out that `allowed_origins` controls browser embedding behaviour,
  not API access control.
- **REQ-23.3:** No code change to `widget_config` is required for this
  finding. The `@MX:WARN` at `partner.py:396-398` already marks the
  endpoint as public-by-design; its `@MX:REASON` SHALL be extended to
  reference REQ-23.1's docstring.

### Finding #24 — Per-tenant widget JWT secret derivation

**Current state:** `generate_session_token` at `widget_auth.py:20-56`
signs every widget's JWT with the single `settings.widget_jwt_secret`
(HS256 shared secret). A single secret exposure (e.g. RCE on portal-api
dumping env vars) would allow forging tokens for every tenant's widget.
Asymmetric signing (ES256/EdDSA) is the structural fix but is scoped to
a future SPEC. This SPEC narrows the blast radius with HKDF-derived
per-tenant keys.

- **REQ-24.1:** `generate_session_token` SHALL derive a per-tenant
  signing key using HKDF-SHA256 with:
  - `ikm`: the raw `settings.widget_jwt_secret` bytes.
  - `salt`: `b"klai-widget-jwt-v1"` (constant; enables future key-rotation
    by bumping to `v2`).
  - `info`: the tenant slug (`org.slug`) encoded as UTF-8. Using the
    slug rather than the integer `org_id` keeps the info value stable
    across tenant-ID re-numbering scenarios and is already unique.
  - `length`: 32 bytes (HS256-appropriate).
- **REQ-24.2:** `decode_session_token` SHALL accept a `tenant_slug`
  argument and derive the same key before calling `jwt.decode`. All
  downstream callers of `decode_session_token` SHALL be updated to pass
  the slug; the slug is available from the JWT's `wgt_id` → widget lookup.
- **REQ-24.3:** Live widget sessions at the time of deploy SHALL be
  invalidated (users are re-issued tokens on next widget-config fetch,
  TTL is 1 hour). A runbook note SHALL be added to `partner.py`'s
  widget-config docstring reminding operators that this deploy invalidates
  all active widget sessions.
- **REQ-24.4:** The `generate_session_token` call site at
  `partner.py:452-457` SHALL be updated to pass `tenant_slug=org.slug`
  into the updated signature.
- **REQ-24.5:** Unit tests SHALL verify that a token issued for tenant A
  does NOT validate when decoded with tenant B's slug (the canonical
  regression test for this finding).

### Finding #27 — `tenant_matcher` cache invalidation on plan change

**Current state:** `tenant_matcher._cache` at `tenant_matcher.py:23-47`
caches email → (zitadel_user_id, org_id) resolutions for 5 minutes. If
a tenant downgrades from `professional` to `free` mid-window, scribe
continues accepting their meeting invites for up to 5 minutes after the
downgrade because the cache still has the old plan-eligible entry.
Business-logic risk, not an active exploit.

- **REQ-27.1:** EITHER (a) the `CACHE_TTL` at `tenant_matcher.py:23`
  SHALL be reduced from 5 minutes to 60 seconds, OR (b) an explicit
  `invalidate_cache(email)` function SHALL be added and called from the
  plan-change path (billing webhook / admin plan update). Option (a) is
  preferred for simplicity; option (b) is acceptable if profiling during
  /run shows the increased Zitadel load is unacceptable. The choice
  SHALL be documented in the module docstring with rationale.
- **REQ-27.2:** IF option (a) is chosen, THE module docstring SHALL
  document the new TTL AND the rationale (business-logic hygiene, not
  critical security). IF option (b), the docstring SHALL document the
  invalidation callsites.
- **REQ-27.3:** A unit test SHALL verify the chosen invalidation
  semantics — either a time-shift test for 60-second TTL, or a direct
  invalidation call for the hook variant.

### Finding #28 — `/docs` double-gating on env + debug

**Current state:** `app/main.py:170-177` exposes `/docs` and
`/openapi.json` iff `settings.debug` is truthy. Cornelis noted that a
deploy-time regression (accidentally setting `DEBUG=true` in production)
would expose the OpenAPI surface. Low risk because `DEBUG=true` also
enables `auth_dev_mode` which is disastrous for other reasons, but
defense-in-depth should prevent the documentation leak even if
`auth_dev_mode` is not set.

- **REQ-28.1:** THE FastAPI app at `klai-portal/backend/app/main.py:170`
  SHALL gate `docs_url` and `openapi_url` on BOTH `settings.debug == True`
  AND `settings.portal_env != "production"`. When either condition is
  false, both URLs SHALL be `None` (FastAPI will return 404).
- **REQ-28.2:** A new `portal_env: str = "production"` field SHALL be
  added to `klai-portal/backend/app/core/config.py:Settings`. Values
  accepted: `"development"`, `"staging"`, `"production"`. Default
  `"production"` is deliberately conservative so an unset env var in a
  new deployment does NOT expose `/docs`.
- **REQ-28.3:** A pydantic `@field_validator` on `debug` SHALL raise a
  `ValueError` at startup IF `debug=True` AND `portal_env == "production"`,
  preventing the server from starting at all in that misconfiguration.
  This is the hard guard; REQ-28.1 is the soft fallback if the validator
  is bypassed (e.g. by monkey-patching in a test).
- **REQ-28.4:** `deploy/docker-compose.yml` portal-api `environment:`
  block SHALL include `PORTAL_ENV: ${PORTAL_ENV:-production}` so the var
  is forwarded. Local-dev `.env` sets `PORTAL_ENV=development`.

---

## klai-connector hygiene (v0.3.0)

Three items. HY-30 is technically an active 500 bug (not hygiene) but is
grouped here because its fix is one line and the blast radius is local to
one route module. HY-31 and HY-32 are genuine hygiene.

### HY-30 — `HTTPException` NameError in `routes/connectors.py`

**Current state:** `klai-connector/app/routes/connectors.py` imports
`APIRouter, Depends, Request` from `fastapi` but NOT `HTTPException`
(line 5). Lines 75, 90, and 121 then `raise HTTPException(...)` in the
"connector not found" branches of `get_connector`, `update_connector`,
and `delete_connector`. At runtime the `raise` site executes, Python
resolves `HTTPException` via lookup, fails with `NameError`, and FastAPI
converts the uncaught exception to a generic 500. Symptoms:

- Every "not found" response is actually a 500.
- A legitimate existing connector returns normally; a non-existent one
  returns 500.
- The difference between 500 and 404 is a UUID-existence oracle (an
  attacker can enumerate connector UUIDs per-tenant by poking URLs).
- ruff rule `F821` (undefined name) would catch this but does not fire
  because `klai-connector/` appears to be excluded from the lint set
  (confirm during /run — if so, re-enable F821 for the package).

- **REQ-30.1:** `klai-connector/app/routes/connectors.py` line 5 SHALL
  import `HTTPException` alongside the existing FastAPI imports, making
  lines 75/90/121 runtime-correct.
- **REQ-30.2:** Unit tests SHALL cover every "not found" path in
  `get_connector`, `update_connector`, and `delete_connector`, asserting
  HTTP 404 and response body `{"detail": "Connector not found"}`. The
  tests MUST also assert that the pre-fix behaviour (500 with Internal
  Server Error body) is gone — i.e. a regression test that would have
  caught the original finding.
- **REQ-30.3:** CI configuration SHALL ensure ruff F821 runs against
  `klai-connector/app/` so future `NameError` landmines fail the build.
  If `klai-connector/` is currently excluded from the lint set, /run
  re-enables F821 for the package and adds a note in the service's
  `pyproject.toml` explaining that F821 is mandatory.
- **REQ-30.4:** The same `routes/` directory SHALL be grepped for any
  other missing FastAPI imports during /run (defense-in-depth; the
  explicit grep pattern is `^from fastapi import .*` vs. `raise
  HTTPException` / `Depends(` / `Request` usage in the file). Any
  additional hits become follow-up REQs in the same PR.

### HY-31 — `/api/v1/compute-fingerprint` imports deleted module

**Current state:** `klai-connector/app/routes/fingerprint.py` line 53
lazy-imports `from app.adapters.webcrawler import WebCrawlerAdapter,
_extract_markdown`. Per the audit the `app.adapters.webcrawler` module
has been deleted (migration away from a per-service crawler adapter to
a shared crawl4ai HTTP client). The lazy import defers the failure to
request-time rather than startup, so the endpoint silently 502s whenever
called — and the 502 `detail` includes `Crawl failed: ModuleNotFoundError:
No module named 'app.adapters.webcrawler'`, leaking the internal module
name.

- **REQ-31.1:** EITHER (a) `/api/v1/compute-fingerprint` SHALL be removed
  entirely if SPEC-CRAWL-004 REQ-9's canary-fingerprint flow has been
  superseded by a different mechanism, OR (b) the endpoint SHALL be
  rewired to the replacement crawl4ai HTTP client (same pattern as
  `klai-knowledge-ingest/knowledge_ingest/adapters/crawler.py`). /run
  picks one based on current product need. Option (a) is preferred if
  no active consumer calls the endpoint (check via VictoriaLogs for the
  last 30 days with `service:connector AND path:/api/v1/compute-fingerprint`).
- **REQ-31.2:** IF option (b) is chosen, the `Crawl failed: ...` error
  message at `fingerprint.py:98-99` SHALL be sanitised to return a
  generic `"Crawl failed"` detail; the original exception goes to
  `logger.exception(...)` only, so internal module names never appear
  in user-visible 502 bodies.
- **REQ-31.3:** A regression test SHALL exist that either asserts the
  endpoint returns 404/405 (if removed per REQ-31.1a) or that a
  successful crawl returns 200 + a well-formed response (if rewired per
  REQ-31.1b).
- **REQ-31.4:** Portal callers of `/api/v1/compute-fingerprint` SHALL
  be updated to match the chosen outcome. If the endpoint is removed,
  the portal admin UI that recomputes canary fingerprints is updated in
  the same PR or tagged as a follow-up SPEC.

### HY-32 — No rate-limit on Zitadel-authenticated `/api/v1/connectors`

**Current state:** `klai-connector/app/middleware/auth.py:100-148` wraps
every `/api/v1/connectors*` route in a Zitadel JWT check. Once a user is
authenticated, they can POST new connectors, fuzz UUIDs via GET/PUT/DELETE,
and do so at unbounded rate. Per-tenant row creation has no upper bound
in the app layer. This is a brute-force oracle (probing connector UUIDs)
plus an unbounded-row-creation vector. Caddy only limits portal-api's
`@portal-api-sensitive` zone; the connector service is behind a separate
internal route with no per-IP limit.

- **REQ-32.1:** EITHER (a) a Caddy rate-limit zone SHALL be added to
  `deploy/caddy/Caddyfile` for the connector service's public routes
  (connector service is not currently internet-reachable, so this may
  be a no-op), OR (b) the application SHALL layer a per-org Redis token
  bucket using the existing `partner_dependencies.check_rate_limit`
  pattern. Option (b) is preferred because it can key on `org_id` rather
  than `{remote_host}`.
- **REQ-32.2:** The per-org limit SHALL default to 60 requests/min per
  org for GET/LIST endpoints and 10 requests/min per org for
  POST/PUT/DELETE. These are defensive ceilings; legitimate admin flows
  stay well below.
- **REQ-32.3:** When Redis is unreachable, the rate-limit check SHALL
  fail OPEN (allow the request) and emit a structlog warning
  `connector_rate_limit_redis_unavailable`. Same fail-open pattern as
  REQ-19.4 and partner-API.
- **REQ-32.4:** A regression test SHALL verify that the 11th POST in a
  single minute from a single org returns 429.

---

## klai-scribe hygiene (v0.3.0)

Six items spanning audio path construction, JWT sub validation,
transcription lifecycle, storage finalize race, health-endpoint SSRF
landmine, and CORS regex.

### HY-33 — `audio_path` path traversal latent

**Current state:** `klai-scribe/scribe-api/app/services/audio_storage.py`
lines 30-77 construct audio paths via
`/data/audio/{user_id}/{txn_id}.wav` where `user_id` is the raw Zitadel
`jwt.sub` and `txn_id` is a server-generated UUID. No
`Path.resolve().is_relative_to(base)` check is performed before writing
or deleting the file. If a future auth flow returns a Zitadel `sub` that
contains `../` (custom IdPs, federated auth, a provider bug), the
server would happily escape the `/data/audio/` directory.

- **REQ-33.1:** EVERY audio-path construction SHALL pass through a
  single helper `_safe_audio_path(base: Path, user_id: str, txn_id: str)
  -> Path` that (a) joins base + user_id + txn_id.wav, (b) calls
  `.resolve()`, (c) asserts the resolved path `.is_relative_to(base.resolve())`.
  A violation SHALL raise `ValueError("invalid audio path")` which the
  caller maps to a 400.
- **REQ-33.2:** The helper SHALL be used by `save_audio`, `delete_audio`,
  and every other site that builds a path from `user_id`. /run greps
  for every `Path("/data/audio") / ...` construction in the scribe
  codebase and reroutes.
- **REQ-33.3:** A regression test SHALL feed `user_id="../../../etc/passwd"`
  and assert `ValueError` (or an equivalent 400 at the route layer).
  Similar tests for `user_id="/absolute/path"` and `user_id="..\\win"`.

### HY-34 — Zitadel `sub` charset / format not validated

**Current state:** `klai-scribe/scribe-api/app/core/auth.py:71-74`
extracts `jwt.sub` directly into a `user_id` variable used downstream
for path construction, SQL WHERE clauses, and structlog context. No
regex, no charset whitelist, no length cap. Zitadel's own `sub` is
currently a numeric string of 19-20 digits, but nothing in scribe
enforces that.

- **REQ-34.1:** `auth.py:71-74` SHALL validate `jwt.sub` against a
  regex `^[A-Za-z0-9_-]{1,64}$` before accepting it as the authenticated
  user identifier. Reject → 401 with `{"detail": "invalid token"}`.
- **REQ-34.2:** The regex SHALL be documented with a link to the Zitadel
  sub format reference so a future auth upgrade (custom IdP, SAML
  federation) knows to revisit the constraint.
- **REQ-34.3:** A regression test SHALL feed a synthetic JWT with
  `sub="../evil"` and assert the auth layer returns 401 BEFORE any
  downstream handler touches the sub.
- **REQ-34.4:** HY-33 (path traversal) and HY-34 (charset whitelist)
  are defense-in-depth partners — REQ-33's path check catches traversal
  even if REQ-34's regex fails; REQ-34's regex catches malformed sub
  even if REQ-33's path check is bypassed by a new writer.

### HY-35 — Stranded `status=processing` after worker crash

**Current state:** `klai-scribe/scribe-api/app/api/transcribe.py:156-176`
sets `record.status = "processing"` at transcription start and flips it
to `"complete"` or `"failed"` on exit. If the worker OOMs or the
container is killed mid-transcription, the `status = "processing"` row
stays forever. The UI shows the session as "still processing" with no
timeout. Worker restarts don't pick it up.

- **REQ-35.1:** A reaper SHALL scan the `transcriptions` table on worker
  startup for rows with `status="processing"` older than N minutes
  (N defaulting to 30, configurable via `SCRIBE_STRANDED_TIMEOUT_MIN`).
  Stranded rows SHALL be flipped to `status="failed"` with an explicit
  `error_reason="worker_restart_stranded"`.
- **REQ-35.2:** The reaper SHALL log every recovered row via
  `logger.warning("scribe_stranded_recovered", txn_id=..., age_minutes=...)`
  so observability sees the pattern.
- **REQ-35.3:** The reaper SHALL NOT delete the underlying audio file on
  recovery — that is a separate manual cleanup decision.
- **REQ-35.4:** A regression test SHALL simulate a stranded row (insert
  a `status="processing"` row with `started_at` 35 minutes in the past)
  and assert the reaper flips it to `failed` at startup.

### HY-36 — `finalize_success` race — audio stays on disk on crash

**Current state:** `klai-scribe/scribe-api/app/api/transcribe.py`
finalize path first does `session.commit()` (setting `record.audio_path
= None`) and THEN `await delete_audio(audio_path)`. If the process
crashes between these two, the DB says "audio deleted" but the file is
still on disk. No reconciliation runs.

- **REQ-36.1:** The finalize order SHALL be inverted: (1) `await
  delete_audio(audio_path)` first, (2) `session.commit()` after. A
  delete failure aborts the transaction so the DB stays consistent
  with disk (file still present, path still set).
- **REQ-36.2:** A janitor job SHALL scan `/data/audio/{user_id}/` for
  orphan files not referenced by any `transcriptions.audio_path` and
  delete them after a grace period (default 24 hours, configurable
  via `SCRIBE_JANITOR_GRACE_HOURS`). This catches the file-present /
  DB-says-null case that survives step 1's reorder if a disk-delete
  succeeded but DB commit failed.
- **REQ-36.3:** The janitor SHALL log every deleted orphan via
  `logger.info("scribe_janitor_orphan_deleted", path=..., age_hours=...)`.
- **REQ-36.4:** A regression test SHALL create an orphan file, run the
  janitor, and assert the file is gone after the grace period.

### HY-37 — `/health` uses configurable `whisper_server_url` (SSRF landmine)

**Current state:** `klai-scribe/scribe-api/app/api/health.py:20-31`
hits `httpx.get(settings.whisper_server_url + "/health")`. The
`whisper_server_url` is env-driven. If an operator ever sets it to
`http://internal-admin-api/health` (typo, config drift, test-env leak),
scribe's `/health` endpoint becomes an SSRF probe — an unauthenticated
attacker hitting `GET /health` triggers a server-to-server call to the
misconfigured URL. Currently the URL points at the whisper service and
is safe, so this is a landmine, not an active exploit.

- **REQ-37.1:** `whisper_server_url` SHALL be validated at Settings
  load time against an allowlist regex: the hostname MUST be one of
  `whisper`, `whisper-server`, `localhost`, or `127.0.0.1`, OR end with
  `.getklai.com` (same pattern as portal). Reject → `ValidationError`
  at app startup; service refuses to boot with a suspicious URL.
- **REQ-37.2:** The `/health` handler SHALL catch httpx `ConnectError`
  separately and return a generic `503 {"detail": "whisper unreachable"}`
  WITHOUT echoing the URL or the exception string. The URL is internal
  config; leaking it via the health endpoint is the SSRF-adjacent leak.
- **REQ-37.3:** A regression test SHALL assert the Settings validator
  rejects `http://evil.com/`, `http://169.254.169.254/` (AWS metadata),
  and `file:///etc/passwd`.

### HY-38 — CORS `allow_origin_regex` + `allow_credentials` (docs-only)

**Current state:** `klai-scribe/scribe-api/app/main.py:42-48` registers
`CORSMiddleware(allow_origin_regex=r"https://[a-z0-9-]+\.getklai\.com",
allow_credentials=True)`. Scribe is currently NOT directly browser-
reachable — portal-api is the only HTTP peer. But if that changes (a
future UI adds direct XHR to scribe), the combination of permissive
origin regex + credentials produces a cross-origin credentialed-request
vector. This finding overlaps conceptually with SPEC-SEC-CORS-001.

- **REQ-38.1:** The `allow_origin_regex` SHALL be supplemented with a
  leading code comment documenting: (a) scribe is currently back-end-
  only, (b) if scribe ever becomes browser-reachable, the regex must
  be tightened to an explicit allowlist OR SPEC-SEC-CORS-001 must be
  re-run against scribe. The comment is the "docs-only" deliverable
  for this finding.
- **REQ-38.2:** An MX:WARN annotation SHALL be added at the
  CORSMiddleware registration site with `@MX:REASON: permissive CORS
  regex + credentials — safe only because scribe is back-end-only.
  See SPEC-SEC-HYGIENE-001 REQ-38 and SPEC-SEC-CORS-001.`
- **REQ-38.3:** A test file-discovery assertion SHALL verify the MX:WARN
  exists (similar to REQ-23 — grep-based).

---

## klai-retrieval-api hygiene (v0.3.0)

Six items spanning health-endpoint event-loop blocking, unbounded
fire-and-forget tasks, log-poisoning via headers, Redis fail-open, TRY
antipattern, and a JWKS worker-DoS landmine.

### HY-39 — `/health` blocks event loop + topology recon

**Current state:** `klai-retrieval-api/retrieval_api/main.py:73-131`
defines the `/health` endpoint. Two issues:

1. **Event-loop blocking:** line 121 `db.connection.ping()` is
   `falkordb`'s synchronous client called directly inside an
   `async def health()` handler. With ~10s Caddy polling, every poll
   pauses the event loop for the duration of the ping.
2. **Topology recon:** lines 83, 101, 112, 124 all echo `str(exc)` into
   the JSON response — including internal hostnames (`whisper-server`,
   `qdrant`, `litellm`) and in some cases internal IPs
   (`http://172.18.0.1:7997` for TEI via docker bridge). An external
   attacker hitting `/health` can enumerate the internal service
   topology from error strings.

- **REQ-39.1:** The `falkordb` ping at line 120-121 SHALL be wrapped in
  `await asyncio.to_thread(db.connection.ping)` so the sync call runs
  in a thread pool without blocking the event loop. Same change for
  every other sync-client call inside `health()`.
- **REQ-39.2:** EVERY `except Exception as exc:` branch in `health()`
  SHALL replace `f"error: {exc}"` with a generic `"error"` in the
  response body. The exception detail goes to `logger.warning(...,
  exc_info=True)` ONLY — not echoed to the client.
- **REQ-39.3:** A regression test SHALL hit `/health` with one dependency
  deliberately pointing at an unreachable host and assert:
  - Response body contains `"tei": "error"` (generic), NOT the hostname.
  - structlog output contains the full exception via `exc_info`.
- **REQ-39.4:** IF `/health` currently 503s when one dependency is down,
  the new behaviour SHALL preserve the 503 status code — only the
  response body detail changes.

### HY-40 — Unbounded `_pending` fire-and-forget task set

**Current state:** `klai-retrieval-api/retrieval_api/services/events.py`
lines 24 + 96-99:

```python
_pending: set[asyncio.Task] = set()

def emit_event(...):
    task = asyncio.create_task(_emit(...))
    _pending.add(task)
    task.add_done_callback(_pending.discard)
```

The set IS bounded by task completion (each task discards itself when
done), BUT under a flood where completion is SLOWER than task creation
(Redis fail-open at REQ-42 + retrieval spike) the set grows without
upper bound. Worst case: tens of thousands of pending tasks in memory
→ OOM.

- **REQ-40.1:** `_pending` SHALL be capped at N active tasks (N default
  1000, configurable via `RETRIEVAL_EVENTS_MAX_PENDING`). If the cap
  is hit, `emit_event` SHALL drop the new event and increment a
  `retrieval_events_dropped_total` Prometheus counter.
- **REQ-40.2:** The drop SHALL log `logger.warning("retrieval_events_cap_hit",
  pending=len(_pending))` once per minute (rate-limited to avoid log
  spam).
- **REQ-40.3:** A regression test SHALL flood `emit_event` with 2000
  calls while stubbing `_emit` to hang, and assert the set size stays
  at or below 1000 AND the `retrieval_events_dropped_total` counter
  increments.
- **REQ-40.4:** Alternative: `emit_event` MAY instead put the event on
  a bounded `asyncio.Queue(maxsize=1000)` consumed by a single worker
  task. /run picks whichever is simpler to integrate; the invariant
  (bounded memory) is the requirement.

### HY-41 — X-Request-ID / X-Org-ID log poisoning

**Current state:** `klai-retrieval-api/retrieval_api/logging_setup.py`
lines 73-79 bind `X-Request-ID` and `X-Org-ID` from incoming headers
into structlog context with no length cap, no charset whitelist, no
ASCII enforcement. An attacker can send
`X-Request-ID: $(curl evil.com/...)` or `X-Request-ID: <script>` or
`X-Request-ID: <10MB of bytes>` and these flow into every log line.
Impact: (a) log storage pollution, (b) tenant dashboards displaying
attacker-controlled strings, (c) in the ASCII-escape case, potential
terminal-injection when admins tail logs interactively.

- **REQ-41.1:** `RequestContextMiddleware` SHALL validate `X-Request-ID`
  against `^[A-Za-z0-9_-]{1,128}$` and substitute a server-generated
  UUID when the header is missing, empty, or invalid.
- **REQ-41.2:** `X-Org-ID` SHALL be validated against `^[0-9]{1,20}$`
  (portal orgs are integer-valued; if this ever changes, the regex is
  updated in lockstep). Invalid values result in `X-Org-ID` being
  dropped from the log context entirely — NOT rejected at the HTTP
  layer (different origins for the same header are legitimate).
- **REQ-41.3:** A regression test SHALL send `X-Request-ID: <10KB of
  garbage>` and assert the bound context value is either the original
  (if under 128 chars and valid) or a server-generated UUID (if not).
- **REQ-41.4:** The same length-cap pattern SHALL be applied symmetrically
  in every service's `RequestContextMiddleware` — portal-api, connector,
  scribe, mailer, research-api, knowledge-ingest. /run greps for
  `RequestContextMiddleware` across all services and applies the same
  regex cap, because log poisoning is end-to-end.

### HY-42 — Rate-limiter fails open on Redis (documented DiD gap)

**Current state:** `klai-retrieval-api/retrieval_api/services/rate_limit.py:69-96`
catches every Redis exception and returns `True` (allow). This is a
documented design decision (REQ-4.5 in an earlier SPEC): retrieval must
stay available even if Redis is down. HYGIENE-001 does NOT change this
behaviour; it documents the trade-off so the next audit sees the
rationale and doesn't re-file.

- **REQ-42.1:** No code change. The existing fail-open path SHALL gain
  an MX:WARN annotation at the `except Exception` block, with
  `@MX:REASON: fail-open on Redis is a deliberate availability choice
  per SPEC-RETRIEVAL-RL-001 REQ-4.5. Fail-closed would take retrieval
  down with Redis, which is unacceptable given retrieval is on the hot
  path for user queries. See SPEC-SEC-HYGIENE-001 REQ-42.`
- **REQ-42.2:** The existing `logger.warning("rate_limit_redis_unavailable",
  ...)` SHALL include `exc_info=True` so the traceback is captured
  (currently it only logs the error string; see
  `.claude/rules/klai/projects/portal-logging-py.md` on TRY401).
- **REQ-42.3:** A companion SPEC SHALL be filed for a future fail-
  closed variant with a circuit-breaker (`SPEC-RETRIEVAL-RL-FAILCLOSED-001`).
  Mentioned in Out-of-Scope; HYGIENE-001 is explicitly NOT that SPEC.

### HY-43 — `except (TimeoutError, Exception)` TRY antipattern

**Current state:** `klai-retrieval-api/retrieval_api/services/search.py`
lines 142, 242, 310 all use `except (TimeoutError, Exception) as exc:`
followed by `logger.error("...", error=str(exc))`. Two separate lint
issues:

1. `TimeoutError` IS a subclass of `Exception`, so listing both is dead
   code. Ruff rule `TRY...` (exception-hierarchy) flags this.
2. `str(exc)` throws away the traceback. Ruff rule `TRY401` flags this.

Neither is a security bug; both are hygiene that would improve debug
capability and are already enforced by project ruff config.

- **REQ-43.1:** EVERY `except (TimeoutError, Exception)` in
  `search.py` SHALL be replaced with `except Exception`. Where
  TimeoutError needs distinct handling, it gets its own `except
  TimeoutError:` branch BEFORE the generic `except Exception:`.
- **REQ-43.2:** EVERY `logger.error("...", error=str(exc))` in the
  same file SHALL be replaced with either `logger.exception("...")`
  (includes traceback) OR `logger.warning("...", exc_info=True)` if the
  error is expected and the level is warning. See
  `.claude/rules/klai/projects/portal-logging-py.md` for the rule.
- **REQ-43.3:** /run greps the entire retrieval-api for the same pattern
  (`except (TimeoutError` and `error=str(exc)`) and applies the fix
  uniformly. Scope cap: retrieval-api only; other services get the
  same treatment in their own hygiene SPECs.
- **REQ-43.4:** ruff `TRY` rules SHALL be enforced in CI for retrieval-
  api's `pyproject.toml`. If already enforced, /run verifies they
  would have caught the original antipattern had it been introduced
  today.

### HY-44 — JWKS worker-DoS landmine on `jwt_auth_enabled=False`

**Current state:** `klai-retrieval-api/retrieval_api/middleware/auth.py:273-275`.
The auth middleware has two paths:

- `jwt_auth_enabled=True` (normal): JWKS URL is required; middleware
  validates tokens against it.
- `jwt_auth_enabled=False` (local-dev bypass): middleware skips JWT
  validation — BUT only skips if the `Authorization` header is absent.
  If the header IS present and `jwt_auth_enabled=False`, the middleware
  still attempts JWKS fetch, which under the dev default (`JWKS_URL=""`)
  produces TWO sequential `httpx.get(timeout=10.0)` attempts to an
  invalid URL. Each 10-second timeout means 20 seconds of worker time
  per malicious request — an unauthenticated attacker can send
  `Authorization: Bearer x` at N concurrent connections and pin N
  workers for 20 s each, exhausting the ASGI pool.

This is NOT exploitable today because production sets `ZITADEL_ISSUER`
(so `jwt_auth_enabled=True`), and the misconfigured-dev path is
dormant. It IS a landmine: one config drift (unset `ZITADEL_ISSUER` in
production env) + attacker knowledge of the auth path = DoS.

- **REQ-44.1:** `retrieval_api/middleware/auth.py` SHALL short-circuit
  to 401 WITHOUT JWKS fetch when `jwt_auth_enabled=False` AND the
  `Authorization` header is present. The current "silently bypass auth"
  is only valid when the header is ABSENT (legitimate dev traffic that
  hasn't attached a token).
- **REQ-44.2:** The JWKS-fetch path SHALL be gated on a non-empty
  `settings.jwks_url`. If the URL is empty AND `jwt_auth_enabled=True`,
  the service SHALL fail at startup (raise in Settings validator) —
  never at request-time.
- **REQ-44.3:** The httpx call inside the JWKS-fetch path SHALL have
  `timeout=3.0` max (down from 10s). JWKS endpoints respond in
  sub-second; 3 seconds is a generous upper bound.
- **REQ-44.4:** The JWKS response SHALL be cached in memory for
  15 minutes (keyed by `jwks_url`). Cache miss performs one fetch; all
  subsequent hits within the window reuse. This provides defense against
  a JWKS-endpoint slow-loris and reduces the per-request cost of a
  legitimate verification.
- **REQ-44.5:** A regression test SHALL assert that, with
  `jwt_auth_enabled=False` AND `Authorization: Bearer x`, the middleware
  returns 401 in < 100 ms (no JWKS fetch attempted).
- **REQ-44.6:** A second regression test SHALL assert that, with
  `jwt_auth_enabled=True` AND `settings.jwks_url=""`, the service
  refuses to start (Settings validator raises).

---

## klai-knowledge-mcp hygiene (v0.3.0)

Four items spanning FastMCP's DNS-rebinding disablement, page_path
validation gaps, absence of MCP-level rate-limit, and a guessable
personal-KB slug derivation.

### HY-45 — FastMCP DNS-rebinding protection disabled (landmine, docs-only)

**Current state:** `klai-knowledge-mcp/main.py:170-176` configures
FastMCP with `enable_dns_rebinding_protection=False`. Currently safe
because Caddy has no upstream route to the MCP service — the MCP is
only reachable via stdio/unix-socket from trusted local processes.

Landmine: if a future Caddy config adds an HTTP upstream (e.g. exposing
the MCP over the public internet for an IDE integration), DNS-rebinding
protection is a required defense against DNS-rebinding CSRF on the MCP
surface. Disabling it silently at the FastMCP layer means the future
Caddy operator has no obvious signal that they need to re-enable it.

- **REQ-45.1:** The `enable_dns_rebinding_protection=False` line SHALL
  be annotated with an MX:WARN and `@MX:REASON: safe today because MCP
  is not internet-reachable. If Caddy ever adds an HTTP upstream to
  klai-knowledge-mcp, this flag MUST be flipped to True. See
  SPEC-SEC-HYGIENE-001 REQ-45 and SPEC-MCP-TRANSPORT-001 (future).`
- **REQ-45.2:** The `deploy/caddy/Caddyfile` SHALL gain a comment at
  the top of the config block for "services that are NOT internet-
  reachable" listing klai-knowledge-mcp explicitly. Adding an upstream
  for MCP without removing this comment is a reviewer signal.
- **REQ-45.3:** No code test — this is docs-only. A grep-based test
  in `klai-knowledge-mcp/tests/test_mcp_hygiene.py` SHALL assert the
  MX:WARN annotation is present at the `enable_dns_rebinding_protection`
  line.

### HY-46 — `page_path` validation bypass via encoding (stub)

**Current state:** `klai-knowledge-mcp/main.py:337-339` validates
`page_path` by rejecting literal `..`, `\`, and leading `/`. Bypasses:

- URL-encoded: `%2e%2e` decodes to `..` after URL handling downstream.
- Fullwidth stops: `．．` is Unicode U+FF0E FULLWIDTH FULL STOP; some
  normalizers fold to `..`.
- Overlong UTF-8: historical bypass for `..` as `C0 AE C0 AE`.

**CANNOT-VERIFY:** the MCP passes `page_path` to klai-docs as a URL
path. Whether klai-docs route-handlers do their own path-traversal
check is OUT OF SCOPE for this SPEC — a klai-docs route-handler audit
is a follow-up research spike. Without that audit, the blast radius of
a bypass is unknown.

Per the scope cap, HY-46 is filed as `stub`. Detailed REQ-46 lands in
the follow-up split SPEC once the klai-docs audit is done.

- **REQ-46.1 (stub):** `page_path` validation SHALL be tightened to
  reject URL-encoded path-traversal (`%2e%2e`, `%2f`), fullwidth stops
  (`．．`), and any input that differs from its Unicode-NFKC-normalised
  form when the difference affects path characters. Full detail and
  test matrix deferred to the follow-up SPEC pending klai-docs audit.
- **REQ-46.2 (stub):** A klai-docs route-handler audit SHALL be filed
  as a research spike. Until that audit lands, the /run implementer
  SHALL apply the conservative rejection rule above (REQ-46.1) as a
  safe-by-default stopgap.
- **REQ-46.3 (stub):** Regression test coverage for the encoding
  bypasses listed above lands in the follow-up SPEC.

### HY-47 — No MCP-level rate-limit or per-tenant quota

**Current state:** MCP tools in `klai-knowledge-mcp/main.py` have no
rate-limit, no per-tenant token bucket, no concurrency cap. An
authenticated user can flood the MCP with `query_kb` / `list_sources`
/ `get_page_content` calls at line-rate. Per-tool cost varies but the
`query_kb` path includes dense+sparse embedding + rerank → measurable
GPU cost per call.

- **REQ-47.1:** A token-bucket rate limiter SHALL be added as a
  FastMCP middleware (if the framework supports it; else as a per-tool
  decorator). Default: 60 calls/min per authenticated identity for
  read-only tools (`list_*`, `get_*`), 30 calls/min for write tools.
- **REQ-47.2:** The limiter SHALL key on the authenticated Zitadel
  `sub` (the same user_id HY-34 now validates). When absent (should
  never happen in practice — MCP requires auth), the limiter SHALL
  reject with `-32000` JSON-RPC error `"authentication required"`.
- **REQ-47.3:** When the limit is exceeded, the response SHALL be a
  JSON-RPC error with code `-32001` and message `"rate limit
  exceeded"` — no leaking of the current rate or the limit value.
- **REQ-47.4:** The limiter SHALL use the same Redis backend as
  portal-api's `check_rate_limit` helper. Fail-open on Redis outage
  (same pattern as REQ-32.3 and REQ-19.4).
- **REQ-47.5:** A regression test SHALL call `list_sources` 61 times
  in one minute and assert the 61st call returns the rate-limit error.

### HY-48 — Personal-KB `kb_slug` trivially guessable (docs-only)

**Current state:** `klai-knowledge-mcp/main.py:234-243` derives the
personal-KB slug as `kb_slug = f"personal-{identity.user_id}"`. Once
an attacker learns a victim's Zitadel `sub` (e.g. via a separate
leak), they can reconstruct the personal-KB slug deterministically.
AND the MCP does NOT check membership between `user_id` and `org_id` —
an attacker with a valid session but a different `org_id` could
theoretically access a victim's personal KB if they could get the
slug past the org-scoping check. This is a partial overlap with
SPEC-SEC-IDENTITY-ASSERT-001 which covers the membership-check
structural fix.

HYGIENE-001 files the hygiene angle (guessable slug format); the
structural fix stays in IDENTITY-ASSERT-001.

- **REQ-48.1:** `main.py:234-243` SHALL gain a prominent MX:NOTE at
  the personal-KB slug construction site documenting: (a) the slug is
  deterministic from `user_id`, (b) membership enforcement between
  `user_id` and the KB is the responsibility of
  SPEC-SEC-IDENTITY-ASSERT-001, (c) this SPEC does NOT change the slug
  format because rotating the derivation strategy breaks every existing
  personal KB. The MX:NOTE references both SPECs by ID.
- **REQ-48.2:** No code change to the slug derivation. If
  IDENTITY-ASSERT-001 eventually migrates to an opaque slug format,
  that SPEC handles the migration — HYGIENE-001 does not.
- **REQ-48.3:** A grep-based test SHALL assert the MX:NOTE exists at
  the derivation site.

---

## klai-mailer hygiene (v0.3.0, defense-in-depth)

Two items. Both overlap with SPEC-SEC-MAILER-INJECTION-001; kept here
as a backup layer. If MAILER-INJECTION-001 explicitly claims them in
its /run, HYGIENE-001 closes them as "covered elsewhere" in its
closing PR.

### HY-49 — Signature error-taxonomy oracle

**Current state:** `_verify_zitadel_signature` (path confirmed during
/run) returns distinct `HTTPException(detail="...")` messages for
different verification failure phases:

- `"missing signature header"` — header absent.
- `"malformed signature"` — header present but parse failed.
- `"signature timestamp too old"` — timestamp older than tolerance.
- `"invalid signature"` — HMAC mismatch.

An attacker probing the endpoint can distinguish these cases and learn
(a) whether the target accepts Zitadel signatures at all, (b) the
tolerance window, (c) whether a replayed timestamp within window is
accepted — useful intel for a more targeted attack.

- **REQ-49.1:** ALL Zitadel-signature verification failures SHALL return
  the same response: `HTTPException(status_code=401,
  detail="unauthorized")`. No distinct error messages exposed to the
  caller.
- **REQ-49.2:** The distinct phase information SHALL be preserved in
  structlog at `logger.warning(...)` level with a structured
  `verification_phase` field (`missing_header`, `malformed`,
  `timestamp_too_old`, `hmac_mismatch`). Observability retains the
  signal; the caller loses it.
- **REQ-49.3:** A regression test SHALL send four malformed requests,
  one per phase, and assert all four receive identical response bodies.
- **REQ-49.4:** IF SPEC-SEC-MAILER-INJECTION-001 claims this fix in
  its /run, HY-49 is marked as "covered" in the HYGIENE-001 close-out
  PR. Same for HY-50.

### HY-50 — Permissive signature-version parser (speculative, docs-only)

**Current state:** The `ZITADEL-Signature` header parser accepts
unknown `vN=` fields. Zitadel currently emits only `v1=<hmac>`. A future
Zitadel upgrade to `v2=<better-hmac>` (speculative) might arrive in
parallel with `v1=<legacy>` during a transition window. If the parser
silently accepts `v2=...` but only VERIFIES `v1=...`, a downgrade
attack becomes possible during the transition.

This is speculative — there's no roadmap for a v2 signature. HY-50
documents the parse behaviour so a future Zitadel upgrade is
flagged as a SPEC-touch point.

- **REQ-50.1:** The signature parser SHALL be annotated with an MX:NOTE
  reading: `@MX:NOTE: v1-only verification. If Zitadel adds v2 in a
  future release, REVISIT this parser to verify ALL provided versions
  — not just v1. A strict post-v1 cutover, or verifying whichever is
  strongest, is the safe path. See SPEC-SEC-HYGIENE-001 REQ-50 and
  SPEC-SEC-MAILER-INJECTION-001.`
- **REQ-50.2:** No code change today. The docs-only annotation is the
  deliverable — the actual fix happens when v2 ships upstream.
- **REQ-50.3:** A grep-based test SHALL assert the MX:NOTE exists.
- **REQ-50.4:** IF SPEC-SEC-MAILER-INJECTION-001 claims this
  documentation item, HY-50 closes as "covered".

---

## Non-Functional Requirements

### v0.2.0 NFRs (unchanged)

- **Performance:** None of the eight v0.2.0 fixes SHALL add more than
  5 ms p95 to their respective endpoints. #19's Redis INCR + #22's
  zxcvbn call are the two with measurable cost; both SHALL be profiled
  during /run.
- **Observability:** Every new rejection / violation path SHALL emit a
  stable structlog event key (listed per-requirement above) so the audit
  of these fixes is doable via LogsQL alone.
- **Backwards compatibility:** No existing `/api/signup`, `/api/auth/*`,
  `/partner/v1/widget-config`, or `/docs` caller SHALL break. #24 is
  the one exception — it deliberately invalidates active widget sessions
  on deploy, documented in REQ-24.3.
- **Security Posture:** All rejections SHALL return generic error messages
  (no information leakage about why the input was rejected, except in
  the #22 password-strength case which must coach the user).
- **Test coverage:** Every finding with a code change SHALL have at least
  one regression test that would have caught the original finding.

### v0.3.0 NFRs (new)

- **Performance — connector + scribe:** HY-30 adds one import (zero
  cost). HY-32 adds Redis INCR per request (<1 ms). HY-33/34 add one
  regex match + one `Path.resolve()` per audio operation (~sub-ms).
  HY-35/36 are offline jobs, not on the hot path.
- **Performance — retrieval:** HY-39's `asyncio.to_thread` adds one
  thread context-switch per `/health` call (Caddy polls every 10 s →
  rounding error). HY-40's bounded queue removes worst-case OOM,
  bounds the hot-path allocation. HY-44's JWKS cache replaces two 10s
  httpx calls with zero for cache hits.
- **Observability — all services:** Every new REQ that introduces a log
  event uses a stable event key and is queryable in VictoriaLogs via
  `event:<key>`. Aggregate list of new keys lives in research.md.
- **Backward compatibility:** HY-30 turns 500s into 404s — a client
  that coded against 500-as-not-found breaks. Low risk (there's no
  good reason to code against 500). No other HY-3x..HY-5x change is
  user-visible under normal conditions.
- **Security posture:** Generic error messages across every new reject
  path (HY-37, HY-39, HY-49). Distinct phase info stays in structlog
  only.
- **Test coverage:** Every v0.3.0 finding with a code change has at
  least one regression test that would have caught the original
  finding. Docs-only findings have grep-based annotation tests
  (REQ-38, REQ-45, REQ-48, REQ-50).

---

## Cross-references

### v0.2.0 cross-refs (unchanged)

- **Tracker:** `.moai/specs/SPEC-SEC-AUDIT-2026-04/spec.md`
- **Companion P2:** `.moai/specs/SPEC-SEC-SESSION-001/` (cookie binding
  hardening — related but scoped separately)
- **Companion P2:** `.moai/specs/SPEC-SEC-INTERNAL-001/` (internal-endpoint
  rate-limit fail-closed — same pattern as REQ-19, different surface)
- **Reference impl:** `klai-portal/backend/app/api/partner_dependencies.py:191-199`
  (Redis sliding-window — copy this pattern for REQ-19.2)
- **Reference impl:** `klai-portal/backend/app/api/internal.py:537`
  (raw-SQL RLS insert pattern — not needed here but conceptually similar
  to the fire-and-forget logging in REQ-19.1)
- **Runbook reference (for REQ-19.6):** `docs/runbooks/provisioning-retry.md`
- **Runbook reference (for REQ-24.3):** to be added inline in
  `partner.py` docstring during /run; no separate runbook file required
  for a one-time deploy-invalidation event.

### v0.3.0 cross-refs (new)

- **Companion SPEC (partial overlap, REQ-48):**
  `.moai/specs/SPEC-SEC-IDENTITY-ASSERT-001/` — personal-KB
  membership-check structural fix.
- **Companion SPEC (partial overlap, REQ-49/50):**
  `.moai/specs/SPEC-SEC-MAILER-INJECTION-001/` — Zitadel webhook
  signature verification structural fix.
- **Companion SPEC (partial overlap, REQ-38):**
  `.moai/specs/SPEC-SEC-CORS-001/` — cross-service CORS hardening.
- **Future SPEC (REQ-42):** `SPEC-RETRIEVAL-RL-FAILCLOSED-001` —
  retrieval rate-limit circuit-breaker.
- **Future SPEC (REQ-45):** `SPEC-MCP-TRANSPORT-001` — FastMCP transport
  hardening.
- **Future SPEC (REQ-47):** `SPEC-MCP-QUOTAS-001` — per-tenant MCP quotas.
- **Reference impl (REQ-39):** `asyncio.to_thread` pattern per
  `.claude/rules/klai/lang/python.md` § "asyncio.to_thread() for sync
  SDKs".
- **Reference impl (REQ-41):** `logging_setup.py` RequestContextMiddleware
  in every klai service; see `.claude/rules/klai/infra/observability.md`.
- **Reference impl (REQ-43):** TRY401 rule per
  `.claude/rules/klai/projects/portal-logging-py.md` § "except blocks
  MUST capture traceback".
- **Pitfall reference (REQ-30):** ruff F821 enforcement per
  `.claude/rules/klai/lang/python.md` § "Tooling (ruff + pyright)".
- **Pitfall reference (REQ-32):** minimal-changes rule per
  `.claude/rules/klai/pitfalls/process-rules.md`.
