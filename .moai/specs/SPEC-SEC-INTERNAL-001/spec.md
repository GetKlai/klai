---
id: SPEC-SEC-INTERNAL-001
version: 0.3.0
status: draft
created: 2026-04-24
updated: 2026-04-24
author: Mark Vletter
priority: high
tracker: SPEC-SEC-AUDIT-2026-04
---

# SPEC-SEC-INTERNAL-001: Internal-Secret Surface Hardening

## HISTORY

> **Amendment notice (v0.3.0)**: The concurrent audits on klai-mailer,
> klai-connector, klai-scribe, and klai-knowledge-mcp have completed. They
> confirmed that every finding the v0.2.0 Cornelis audit surfaced against
> klai-portal has a sibling occurrence in at least one other service. This
> SPEC is therefore reframed from "portal-api internal-secret hardening"
> to **service-wide internal-secret hardening**. The shared
> `sanitize_response_body` utility promised by REQ-4 is elevated from a
> portal-local module to a new **`klai-libs/log-utils/`** shared library so
> every Python service can import it. Priority raised from `medium` to
> `high` because one of the new findings (Finding 11 — knowledge-mcp
> echoes upstream response body directly into the chat UI) is a live
> user-visible secret-exposure channel, not a latent path.

### v0.3.0 (2026-04-24)
- Scope expanded from klai-portal to ALL klai Python services that load
  an `INTERNAL_SECRET`-shaped env var: klai-portal, klai-mailer,
  klai-connector, klai-scribe, klai-knowledge-mcp.
- Seven new findings (6 through 12) catalogued in the Findings table.
- REQ-1 reframed: ast-grep rule targets every klai-* service tree.
- REQ-4 reframed: sanitizer moves to `klai-libs/log-utils/` and is
  adopted across five services.
- Three new requirement groups added: REQ-8 (no upstream response body
  echoed to user-facing response), REQ-9 (fail-closed startup on empty
  outbound secret), REQ-10 (sanitize error-body columns before persistence).
- Assumption list extended with mailer / connector / scribe / knowledge-mcp
  specifics.
- Priority raised to `high`.

### v0.2.0 (2026-04-24)
- Expanded from stub into full EARS SPEC with research and acceptance artifacts
- 7 REQ groups defined: constant-time token checks, FLUSHALL replacement,
  proxy header blocklist extension, response-body sanitization utility,
  rate-limit fail-mode configuration, ast-grep regression rule, explicit
  SPEC-SEC-005 dependency

### v0.1.0 (2026-04-24)
- Stub created from SPEC-SEC-AUDIT-2026-04 (Cornelis audit 2026-04-22)
- Priority P2 — extends SPEC-SEC-005 scope to cover newly-identified leak paths

---

## Findings addressed

Findings 14/18/A2/A3/A4 were catalogued in v0.2.0 against klai-portal. The
internal-wave audit (2026-04-24) added findings 6 through 12 from sibling
services. All are addressed by the same family of fixes but across a wider
file surface; hence the REQ expansion.

| # | Finding | Severity | REQ |
|---|---|---|---|
| 14 | `/internal/*` rate-limit fails open on any Redis exception | MEDIUM | REQ-5 |
| 18 | `/internal/librechat/regenerate` runs `FLUSHALL` | HIGH | REQ-2 |
| A2 | `_require_internal_token` in taxonomy.py uses `!=` | MEDIUM | REQ-1 |
| A3 | BFF proxy header-blocklist does not include `x-internal-secret` | MEDIUM | REQ-3 |
| A4 | `exc.response.text` logged in 20+ sites (header reflection) | LOW-MEDIUM | REQ-4 |
| 6 | mailer `_validate_incoming_secret` uses `!=` on INTERNAL_SECRET | HIGH | REQ-1 |
| 7 | mailer `WEBHOOK_SECRET` accepted as empty string — forgeable signatures | HIGH | REQ-9 |
| 8 | connector outbound `Authorization: Bearer ""` silent-empty-string bypass | MEDIUM latent | REQ-9 |
| 9 | scribe/knowledge-mcp silently omit `X-Internal-Secret` header when env empty | MEDIUM latent | REQ-9 |
| 10 | mailer / scribe `resp.text[:200]` error-body log reflection | MEDIUM | REQ-4 |
| 11 | knowledge-mcp `resp.text[:300]` returned verbatim to chat UI | HIGH | REQ-8 |
| 12 | connector `sync_run.error_details` persists upstream `resp.text[:500]` to DB + portal UI | MEDIUM latent | REQ-10 |

---

## Goal

Reduce the blast radius of an `INTERNAL_SECRET` compromise and eliminate the
residual recovery paths that SPEC-SEC-005 did not cover. Originally scoped
(v0.2.0) to four portal-only paths; v0.3.0 expands to the full klai service
constellation after concurrent audits confirmed each of those paths has
sibling occurrences in mailer, connector, scribe, and knowledge-mcp.

Portal-surface paths (v0.2.0 carry-over):

1. **Timing side channel** on string-equality token checks outside internal.py.
2. **Co-tenant collateral** from `FLUSHALL` inside the `/internal/librechat/regenerate`
   code path (blows away every Redis key in the portal namespace, not only
   cached LibreChat yaml).
3. **Header passthrough** at the BFF proxy: a client-supplied
   `X-Internal-Secret` header survives the hop-by-hop filter and reaches
   retrieval-api / scribe-api, which trust the header.
4. **Reflection into logs** via `exc.response.text` — when an upstream error
   message echoes a request header back in its body, the raw secret lands in
   VictoriaLogs.

Internal-wave paths (v0.3.0 additions):

5. **Timing side channel in mailer** — `_validate_incoming_secret` in
   `klai-mailer/app/main.py:182` compares `INTERNAL_SECRET` with `!=`,
   exactly the same shape taxonomy.py has. Same class as Finding A2.
6. **Forgeable webhook signatures** — `klai-mailer/app/config.py:18`
   declares `webhook_secret: str` without `min_length` validation and
   `hmac.new(b"", ...).hexdigest()` produces a deterministic output. An
   operator who ships with `WEBHOOK_SECRET=""` turns Zitadel webhook
   auth into a constant string anyone can forge.
7. **Silent-empty-string outbound auth** — connector's
   `PortalClient._headers()` at `portal_client.py:52` returns
   `"Bearer "` (literal trailing space, empty secret) when
   `settings.portal_internal_secret == ""`; knowledge-ingest and scribe
   inbound code paths use `compare_digest` against the configured secret,
   but if BOTH sides misconfigure to `""` the comparison succeeds. The
   outbound side should refuse to start rather than quietly send empty
   auth headers.
8. **Knowledge-mcp upstream body exposed to end user** —
   `klai-knowledge-mcp/main.py:430` returns
   `f"Error: klai-docs returned HTTP {status}. Details: {resp.text[:300]}"`
   as the MCP tool response, which becomes a message in the user's chat
   UI. If klai-docs ever echoes request headers in a 5xx body (many
   FastAPI middleware stacks do this by default under
   `ServerErrorMiddleware` with `debug=True`), the
   `DOCS_INTERNAL_SECRET` lands in the end-user chat transcript.
9. **Upstream body persisted to database + portal UI** —
   `klai-connector/app/services/sync_engine.py:530-539` stores
   `exc.response.text[:500]` into `sync_runs.error_details` JSONB AND
   forwards the same field to portal via `report_sync_status`. The
   portal connector-management UI renders `error_details` as plain text.
   A reflected secret therefore lands in Postgres (long-term) and is
   visible to anyone with access to the connector UI.
10. **Identical `resp.text[:200]` log reflection** in
    `klai-mailer/app/main.py:65-66` and
    `klai-scribe/scribe-api/app/services/providers.py:115-125` — the
    same pattern Cornelis caught in portal-api's auth.py (Finding A4).

This SPEC complements SPEC-SEC-005 (audit trail + rate-limit +
rotation runbook) and does not redo that work.

---

## Success Criteria

- Every `_require_internal_token` / `_validate_incoming_secret` /
  inbound-shared-secret compare across **all klai services** —
  klai-portal, klai-mailer, klai-connector, klai-scribe,
  klai-knowledge-mcp — uses `hmac.compare_digest`. Enforced by an
  ast-grep rule that blocks `!=` / `==` on any variable ending in
  `_secret`, `_token`, `internal_*`, evaluated against every klai-*
  service tree, not only portal-api.
- `/internal/librechat/regenerate` no longer calls `flushall()`; it invalidates
  only the key(s) it owns (`configs:*` per LibreChat upstream default, subject
  to verification at implementation time — see Assumption A3).
- `proxy.py:_build_upstream_headers` blocklist rejects `x-internal-secret`,
  `x-klai-internal-*`, and any header name matching the regex
  `(?i)(secret|internal-auth|internal-token)`. A regression test exercises
  an injection attempt and asserts the header never reaches the upstream.
- `exc.response.text` / `resp.text[...]` is never logged raw in any klai
  service. A new utility `sanitize_response_body(exc_response) -> str`
  — shipped from the new shared library `klai-libs/log-utils/` — truncates
  to 512 chars AND strips any substring that matches the union regex of
  all known secret env-var values loaded by the service's `Settings` at
  boot. All current call sites are rewritten to use it: portal-api (26),
  mailer (1-2), scribe (1-2), knowledge-mcp (2), connector (1 log +
  1 persisted). See research.md Internal-wave additions section for the
  full inventory.
- No upstream HTTP-error response body is echoed verbatim to a
  user-facing response (chat UI, HTTP error JSON, UI field). Where
  upstream-error context is surfaced to a user, it is a generic status
  code plus a correlation ID that operators can grep in VictoriaLogs.
- Every service that loads an outbound `*_INTERNAL_SECRET` /
  `*_WEBHOOK_SECRET` / equivalent fails-closed at startup on empty value
  if the consumer of the secret is called. Silent-empty-string auth is
  eliminated.
- `connector.sync_runs.error_details` JSONB and any analogous persisted
  error-body column is written via `sanitize_response_body()` before
  persistence.
- `/internal/*` rate-limit fail-mode is configurable via
  `INTERNAL_RATE_LIMIT_FAIL_MODE=open|closed`. Production defaults to
  `closed` (503 on Redis outage); development/staging remain `open` for
  availability. REQ-5 supersedes the SPEC-SEC-005 REQ-1.3 fail-open-only
  behaviour.
- ast-grep rule lives at `rules/no-string-compare-on-secret.yml` and runs
  in the portal-api workflow (`.github/workflows/portal-api.yml`) via
  `ast-grep/action`, failing CI on violation.
- Regression tests:
  - Timing benchmark on internal-token compare (taxonomy.py after fix) shows
    the mean delta between valid and near-valid tokens falls within the 2·σ
    jitter envelope of `hmac.compare_digest` on a constant-length input.
  - `FLUSHALL` is never called by any handler reachable from HTTP.
  - Attacker-supplied `X-Internal-Secret` on a BFF hop returns HTTP 200 at
    retrieval-api only when portal-api itself injects the correct secret;
    the client-supplied header never reaches the upstream.
  - `exc.response.text` containing a literal secret value is scrubbed in
    the structlog entry before it reaches VictoriaLogs.
  - With Redis pool mocked to unavailable and `INTERNAL_RATE_LIMIT_FAIL_MODE=closed`,
    an `/internal/*` call returns HTTP 503; with `=open` it returns HTTP 200
    and emits the `internal_rate_limit_redis_unavailable` warning.

---

## Environment

- **Services in scope**:
  - `klai-portal/backend` (primary, v0.2.0 carry-over)
  - `klai-mailer` — REQ-1, REQ-4, REQ-9
  - `klai-connector` — REQ-4, REQ-9, REQ-10
  - `klai-scribe/scribe-api` — REQ-4, REQ-9
  - `klai-knowledge-mcp` — REQ-4, REQ-8, REQ-9
  - `klai-retrieval-api` — verification of header strip only
  - **new shared library** — `klai-libs/log-utils/` (hosts
    `sanitize_response_body`, one source of truth)
- **Language/runtime**: Python 3.13, FastAPI, httpx, structlog, redis.asyncio.
- **Files edited (portal-api, v0.2.0 carry-over)**:
  - `klai-portal/backend/app/api/taxonomy.py` (`_require_internal_token`,
    lines 399-405) — REQ-1
  - `klai-portal/backend/app/api/internal.py` (`regenerate_librechat_configs`,
    lines 959-1049, specifically the `await redis_client.flushall()` at
    line 1030) — REQ-2
  - `klai-portal/backend/app/api/proxy.py` (`_HOP_BY_HOP` at lines 51-68,
    `_build_upstream_headers` at lines 113-121) — REQ-3
  - `klai-portal/backend/app/api/auth.py` (24 `exc.response.text` sites
    listed in research.md) — REQ-4
  - `klai-portal/backend/app/services/docs_client.py` (2 sites) — REQ-4
  - `klai-portal/backend/app/core/config.py` (new
    `internal_rate_limit_fail_mode` setting) — REQ-5
  - `klai-portal/backend/app/api/internal.py` (`_check_rate_limit_internal`
    at lines 99-143) — REQ-5
  - `rules/no-string-compare-on-secret.yml` (new ast-grep rule, sibling to
    `rules/no-exec-run.yml`) — REQ-6
  - `.github/workflows/portal-api.yml` (wire the ast-grep rule) — REQ-6
- **Files edited (internal-wave, v0.3.0 additions)**:
  - `klai-libs/log-utils/` (new package: `pyproject.toml`,
    `log_utils/__init__.py`, `log_utils/sanitize.py`,
    `log_utils/settings_scan.py`, `tests/test_sanitize.py`) — REQ-4
  - `klai-mailer/app/main.py` (`_validate_incoming_secret` around line
    182 — REQ-1; `_verify_zitadel_signature` at lines 69-83 —
    webhook-secret boot guard in REQ-9; `resp.text[:200]` log sites —
    REQ-4)
  - `klai-mailer/app/config.py` (WEBHOOK_SECRET / internal_secret /
    portal_internal_secret min-length validators — REQ-9)
  - `klai-connector/app/services/portal_client.py` (line 49 load,
    line 52 `_headers()` — REQ-9)
  - `klai-connector/app/clients/knowledge_ingest.py` (lines 117-119
    header conditional — REQ-9)
  - `klai-connector/app/core/config.py` (lines 34, 45, 46 empty-string
    defaults → fail-closed validators — REQ-9)
  - `klai-connector/app/services/sync_engine.py` (lines 530-539, error
    body into `sync_run.error_details` — REQ-10)
  - `klai-scribe/scribe-api/app/services/knowledge_adapter.py`
    (lines 51-60 silent-omit — REQ-9)
  - `klai-scribe/scribe-api/app/services/providers.py` (lines 115-125
    `resp.text[:200]` log — REQ-4)
  - `klai-knowledge-mcp/main.py` (lines 143-157 outbound silent-omit —
    REQ-9; lines 360-365 `resp.text[:200]` log — REQ-4; lines 430-431
    `resp.text[:300]` returned to USER — REQ-8)
  - ast-grep rule file `rules/no-string-compare-on-secret.yml` scope
    extended to run against every klai-* service tree — REQ-6
  - Per-service CI workflows (`.github/workflows/mailer.yml`,
    `connector.yml`, `scribe.yml`, `knowledge-mcp.yml`) — ast-grep
    step added mirroring portal-api — REQ-6
- **Observability**: new stable log keys:
  - `internal_rate_limit_fail_closed` (warning when Redis outage denies a call)
  - `response_body_sanitized` (debug, emitted when a secret substring was
    stripped before logging — alert on volume)
- **Secret storage**: unchanged — SOPS-encrypted `.env` files in `klai-infra/`.

## Assumptions

- **A1** — SPEC-SEC-005 lands first or concurrently. Specifically, the
  `_require_internal_token` at `internal.py:237` (already constant-time since
  SEC-005) is the canonical shape REQ-1 propagates to every other site. If
  SPEC-SEC-005 regresses, REQ-1 tests will still pass (both sites use
  `hmac.compare_digest`) but the audit log coverage promised in SEC-005 is a
  hard dependency for the runbooks referenced from REQ-5.
- **A2** — Switching `flushall()` to a targeted `delete` does not break the
  LibreChat regeneration flow. LibreChat caches `librechat.yaml` with no TTL
  when `USE_REDIS=true` (see `.claude/rules/klai/platform/librechat.md`).
  Deleting only the matching key(s) and restarting the container is expected
  to produce the same observable outcome as `FLUSHALL`.
- **A3** — The exact Redis key pattern used by LibreChat for the cached yaml
  is `configs:*` by default (LibreChat uses `keyv` with a `configs`
  namespace). This MUST be verified against the live Redis state before
  implementation lands; if the namespace differs per version, the SPEC
  accepts either the LibreChat-documented pattern or an equivalent targeted
  SCAN+DEL enumeration. Verification command:
  `docker exec redis redis-cli --scan --pattern 'configs:*'` before and after
  a regeneration. See acceptance.md AC-2.2.
- **A4** — Every secret that might reflect into an upstream body is loaded
  as a pydantic-settings field on `Settings`. If a secret is read via raw
  `os.environ[...]` elsewhere, REQ-4 misses it. Current grep confirms all
  `INTERNAL_SECRET`, `ZITADEL_*`, `SOPS_*`, `RETRIEVAL_API_INTERNAL_SECRET`
  reads go through Settings. Re-run the grep at implementation time.
- **A5** — Hop-by-hop header filter REQ-3 covers only the portal-api BFF
  proxy in `klai-portal/backend/app/api/proxy.py`. Other services that
  forward headers (knowledge-ingest → retrieval-api, LiteLLM hooks)
  construct outgoing httpx requests explicitly and do not pass through
  arbitrary client headers, so they are not affected. This is
  re-verified in the concurrent audits listed in HISTORY.
- **A6** (mailer) — `WEBHOOK_SECRET` and `INTERNAL_SECRET` in
  `klai-mailer/app/config.py` are production-critical. Fail-closed
  behaviour (REQ-9) means a container started with missing env will
  crash on import rather than silently accept forged webhooks. The SOPS
  `.env` file in `klai-infra/` already sets non-empty values; this
  only protects future misconfiguration. Mailer is a single replica —
  no partial-outage window during the rollout.
- **A7** (connector) — The silent-empty-string fallback in
  `PortalClient._headers()` and `KnowledgeIngestClient._headers()` was
  historically a migration convenience (services could be brought up in
  either order during bootstrap). Post-SPEC-SEC-005 the shared-secret
  bootstrap is no longer ad-hoc, so fail-closed is safe. Operators
  bringing up a fresh connector environment MUST set
  `KNOWLEDGE_INGEST_SECRET` and `PORTAL_INTERNAL_SECRET` before the
  first sync job fires. This matches the runbook already referenced by
  SPEC-SEC-005 REQ-3.
- **A8** (connector persisted error bodies) — Existing rows in
  `connector.sync_runs.error_details` may already contain reflected
  upstream bodies (from before this SPEC). REQ-10 applies to writes
  only; a backfill scrub is OUT of scope. If an audit of existing rows
  is needed, a one-off migration may run `regexp_replace` against the
  JSONB column using the same secret-value regex the sanitizer uses at
  runtime. Tracking only — this SPEC does not ship that migration.
- **A9** (knowledge-mcp) — The MCP tool return value is shown verbatim
  in the LibreChat / ChatGPT-compatible chat UI. REQ-8 forbids echoing
  upstream bodies; the replacement contract returns
  `f"Error saving to docs: upstream returned HTTP {status}. Request ID: {request_id}"`
  so operators can still trace the failure in VictoriaLogs without
  leaking the body. Verified tolerable in practice — end users rarely
  need the body to retry; operators do, and they have log access.
- **A10** (shared library) — `klai-libs/log-utils/` is a new monorepo
  package. It ships as a path-dependency from each consuming service's
  `pyproject.toml` (`log-utils = { path = "../klai-libs/log-utils" }`).
  The Docker build context for each service already includes the
  monorepo root (see `klai-infra/deploy/`); no CI-image-build changes
  are required beyond adding the path-dep. If a consuming service is
  later extracted from the monorepo, the package can be published to
  an internal PyPI index without API breakage (REQ-4 contract is
  frozen at v0.3.0).

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Targeted Redis delete misses a cache namespace variant across LibreChat version upgrades | MEDIUM | HIGH — stale yaml served to every tenant | Alert on post-regenerate probe returning the previous yaml hash; keep `FLUSHALL` as a last-resort documented manual runbook step only, never in code |
| `sanitize_response_body` over-redacts (e.g. strips substrings that coincidentally match a secret prefix) | LOW | LOW — degraded log readability | Use full-value match only (not prefix), minimum length 8, and emit `response_body_sanitized` at debug with the truncation offset for forensic reconstruction from VictoriaLogs |
| Fail-closed rate-limit default takes down internal traffic during a Redis outage | LOW (Redis is HA on core-01) | HIGH — mailer / LibreChat patch / LiteLLM hook all fail | Staging defaults to `open`; production rollout via `INTERNAL_RATE_LIMIT_FAIL_MODE=closed` env var flip after a documented dry-run with `=open` + alert on `internal_rate_limit_redis_unavailable` volume |
| ast-grep rule false positives block unrelated PRs | LOW | LOW — developer friction | Rule scopes to variable names matching the secret/token regex AND operator `!=`/`==`; allow-list regression-guard tests under `klai-portal/backend/tests/` |
| BFF header-blocklist regex overreaches and strips a legitimate header | LOW | MEDIUM — downstream 4xx | Scope the regex to a curated deny-list (enumerated names) + one catch-all `(?i)(x-)?klai-internal-*` prefix; unit test covers 10 known good headers passing through |

---

## Cross-references

- Tracker: [SPEC-SEC-AUDIT-2026-04](../SPEC-SEC-AUDIT-2026-04/spec.md)
- Upstream SPEC (extended): [SPEC-SEC-005](../SPEC-SEC-005/spec.md) — REQ-1 (rate
  limit), REQ-2 (audit trail), REQ-3 (rotation runbook). This SPEC adds REQ-1
  through REQ-7 on top, addressing a disjoint set of residual paths.
- Related rules:
  - `.claude/rules/klai/pitfalls/process-rules.md` — `search-broadly-when-changing`
    (for the 24 `exc.response.text` rewrite sweep)
  - `.claude/rules/klai/projects/portal-logging-py.md` — structlog kwargs
    (sanitizer integration point)
  - `.claude/rules/klai/platform/docker-socket-proxy.md` — protocol clients
    over docker exec (FLUSHALL replacement context)
  - `.claude/rules/klai/platform/librechat.md` — Redis cache semantics
    (REQ-2 target invalidation)

---

## Out of scope

- Replacing `INTERNAL_SECRET` with mTLS (future infra SPEC; SPEC-SEC-005
  explicitly defers this, and the residual paths this SPEC addresses all
  disappear once mTLS lands anyway).
- Rotating the secret (SPEC-SEC-005 REQ-3 runbook).
- Broadening the ast-grep rule beyond secret-adjacent comparisons (e.g.
  banning `!=` on arbitrary strings).
- Extending the response-body sanitizer to non-`exc.response.text` log sites
  (e.g. `repr(response)`). REQ-4 is scoped to the 26 sites that currently
  log raw upstream bodies.
- Changing the retrieval-api / scribe-api / docs-app authentication header
  format. REQ-3 strips the attacker-controlled header; it does not change
  how the legitimate injection works.

---

## Requirements

### REQ-1: Constant-time internal-token comparison everywhere

The system SHALL compare internal-secret tokens using `hmac.compare_digest`
in every file that reads the secret, not only `klai-portal/backend/app/api/internal.py`.

- **REQ-1.1**: WHEN `_require_internal_token` is invoked in
  `klai-portal/backend/app/api/taxonomy.py:399` THE implementation SHALL
  validate the Authorization header with `hmac.compare_digest(token, expected)`
  and SHALL NOT use `!=` or `==` string equality.
- **REQ-1.2**: WHEN a new internal-endpoint handler is added in any file
  under `klai-portal/backend/app/api/` THE contributor SHALL reuse the
  canonical implementation from `internal.py:237-258` rather than copying
  the old `!=`-based pattern.
- **REQ-1.3**: THE ast-grep rule from REQ-6 SHALL fail CI on any new `==`
  or `!=` comparison where one operand's variable name matches the regex
  `(?i)(secret|internal_token|bearer_token|api_key)` — catching both the
  taxonomy.py regression and future copy-paste mistakes.
- **REQ-1.4**: WHEN `settings.internal_secret` is empty (not configured)
  THE taxonomy.py handler SHALL return HTTP 503 (matching the internal.py
  precedent) and SHALL NOT short-circuit to 401. Constant-time is not
  required on the empty-secret path; the branch is taken before any
  comparison.
- **REQ-1.5**: WHEN `klai-mailer/app/main.py:182`
  `_validate_incoming_secret` (or whatever the equivalent inbound helper
  is named at implementation time) is invoked THE implementation SHALL
  use `hmac.compare_digest` on equal-length inputs and SHALL NOT use
  `!=` / `==` on the raw header value. (Finding 6.) THE ast-grep rule
  from REQ-6 SHALL catch a regression.
- **REQ-1.6**: THE audit SHALL confirm, at implementation time, that no
  sibling klai service (connector, scribe-api, knowledge-mcp) contains an
  inbound `!=` / `==` comparison against an INTERNAL_SECRET-shaped
  variable. Research.md Inventory F enumerates the verified-clean set
  at v0.3.0 draft time; REQ-6's ast-grep rule is the regression-proofing
  for that claim going forward.
- **REQ-1.7**: THE canonical inbound-secret-compare helper SHALL be
  extracted into `klai-libs/log-utils/` as `verify_shared_secret(header_value, configured) -> bool`
  (returns True on match, False on mismatch, raises ValueError if
  configured is empty). Every service's route guard SHALL call this
  helper rather than implement its own `hmac.compare_digest` inline.
  Consolidation is NOT a hard requirement for acceptance — services
  that keep their existing inline constant-time call pass AC-1 — but
  the helper is provided so new services default to the correct shape.

### REQ-2: FLUSHALL replacement with targeted delete

The system SHALL invalidate only the Redis keys that `regenerate_librechat_configs`
owns, not every key in the database.

- **REQ-2.1**: WHEN `POST /internal/librechat/regenerate` runs THE handler
  SHALL NOT call `redis.flushall()` directly or indirectly. Enforcement is
  grep-based in CI (forbidden-string rule added to
  `.github/workflows/portal-api.yml`).
- **REQ-2.2**: WHEN `POST /internal/librechat/regenerate` needs to invalidate
  the LibreChat yaml cache THE handler SHALL enumerate the target keys via
  `SCAN MATCH <librechat-cache-pattern>` and delete each matching key with
  `UNLINK` (non-blocking delete).
- **REQ-2.3**: WHERE the exact cache pattern is version-dependent on the
  LibreChat image THE implementation SHALL read the pattern from
  `settings.librechat_cache_key_pattern` (new pydantic-settings field,
  default `configs:*`) so a LibreChat upgrade that renames the namespace
  can be tracked in SOPS without a code change.
- **REQ-2.4**: IF the SCAN/UNLINK sequence raises `RedisError` THE handler
  SHALL append the failure to the existing `errors` list (preserving the
  current response contract) AND SHALL log at level `warning` with
  `event="librechat_cache_invalidation_failed"`, `pattern=<pattern>`,
  `exc_info=True`.
- **REQ-2.5**: THE handler SHALL continue the container-restart step even
  if cache invalidation fails — the restart is the belt-and-braces recovery
  for a partial invalidation (LibreChat re-reads the yaml from disk on
  startup).

### REQ-3: BFF proxy header-injection blocklist

The system SHALL strip all secret-bearing headers from inbound client
requests before forwarding to upstream services through the BFF proxy.

- **REQ-3.1**: THE `_HOP_BY_HOP` frozenset in
  `klai-portal/backend/app/api/proxy.py:51-68` SHALL include at minimum:
  `x-internal-secret`, `x-klai-internal-secret`, `x-retrieval-api-internal-secret`,
  `x-scribe-api-internal-secret`, in addition to the existing RFC 7230 §6.1
  hop-by-hop headers.
- **REQ-3.2**: WHEN `_build_upstream_headers(request, session)` iterates
  inbound headers THE function SHALL additionally drop any header whose
  name matches the regex `(?i)^(x-)?(klai-internal|internal-auth|internal-token)`
  — a conservative catch-all for future secret-bearing header names.
- **REQ-3.3**: WHEN a client attempts to inject a matching header THE
  proxy SHALL emit a structlog entry at level `info` with
  `event="proxy_header_injection_blocked"`, `header=<lowercased header name>`,
  `service=<upstream service slug>`. The value SHALL NOT be logged.
- **REQ-3.4**: THE strip SHALL happen before the Bearer token is injected
  via `headers["Authorization"] = f"Bearer {session.access_token}"`, so
  that the Authorization header cannot be influenced by the inbound
  header set either.
- **REQ-3.5**: Regression test SHALL send a request with
  `X-Internal-Secret: attacker-guess` to `/api/scribe/foo` and assert
  the upstream mock at scribe-api received headers that do NOT contain
  the `X-Internal-Secret` key.

### REQ-4: Response-body sanitization utility (shared library, service-wide)

The system SHALL sanitize upstream HTTP-error response bodies before logging
them, to eliminate the header-reflection leak path — in every klai service
that makes outbound httpx calls, not just portal-api.

- **REQ-4.1**: THE system SHALL provide a utility
  `sanitize_response_body(exc_or_response, *, max_len: int = 512) -> str`
  in the new shared library `klai-libs/log-utils/` (module path
  `log_utils.sanitize.sanitize_response_body`) that:
  1. Extracts `.text` from either an `httpx.HTTPStatusError` (via
     `exc.response.text`) or a raw `httpx.Response`.
  2. Truncates to `max_len` characters.
  3. Strips every occurrence of any non-empty secret-env-var value from
     the result, replacing it with the literal `<redacted>`.
  The utility is pure Python, has no dependency on any specific service's
  Settings class — the caller passes in the list of secret values, or
  calls the convenience wrapper `sanitize_from_settings(settings_obj, exc_or_response)`
  which scans the Settings object for secret-shaped fields and applies
  the sanitizer. Each service wires its own Settings into a thin wrapper
  (see REQ-4.4 below).
- **REQ-4.2**: THE list of secret-env-var values SHALL be assembled at
  module-import time from the caller's `Settings` fields whose name
  matches the regex `(?i)(secret|password|token|pat|api_key)` and whose
  value is a non-empty string of length >= 8 (shorter values are
  ignored to avoid over-redaction of common substrings). The helper
  `log_utils.settings_scan.extract_secret_values(settings_obj) -> set[str]`
  implements the scan and is reused across services.
- **REQ-4.3**: WHEN `sanitize_response_body` redacts at least one
  substring THE utility SHALL emit a structlog entry at level `debug` with
  `event="response_body_sanitized"`, `redaction_count=<int>`,
  `original_length=<int>`, `service=<service-slug>` (bound via
  `structlog.contextvars` — the utility does not know its own service
  name; the caller's `setup_logging("<name>")` already binds it).
- **REQ-4.4**: ALL raw `exc.response.text` / `resp.text[...]` call sites
  across the klai Python services SHALL be rewritten to pass the
  response through `sanitize_response_body` before logging. The
  comprehensive inventory lives in research.md §"Internal-wave additions".
  Per-service wrapper modules:
  - `klai-portal/backend/app/utils/response_sanitizer.py` (wraps
    `log_utils.sanitize_response_body` with portal-api Settings)
  - `klai-mailer/app/sanitize.py` (thin wrapper)
  - `klai-connector/app/core/sanitize.py` (thin wrapper)
  - `klai-scribe/scribe-api/app/core/sanitize.py` (thin wrapper)
  - `klai-knowledge-mcp/sanitize.py` (thin wrapper — note:
    knowledge-mcp's "settings" are module-level globals, so the wrapper
    builds the secret set from those globals directly)
  A codemod is acceptable; per-site manual review is not required beyond
  reading the surrounding logger call.
- **REQ-4.5**: THE utility SHALL be idempotent and safe on `None` /
  empty-body responses (returns empty string, emits no warning).
- **REQ-4.6**: Regression test SHALL construct an `httpx.Response` whose
  body contains the configured internal secret AND assert
  `sanitize_response_body(resp)` does NOT contain the secret substring.
  The test SHALL run against each consuming service's wrapper, not only
  the shared library (so that "my Settings scan forgot a field" is caught
  by CI).
- **REQ-4.7**: THE shared library `klai-libs/log-utils/` SHALL be a
  Python package with its own `pyproject.toml`, unit-tested in
  isolation, and consumed by each service via a path dependency
  (`log-utils = { path = "../klai-libs/log-utils" }`). Version 0.1.0 at
  ship time; semver-bumped on any breaking change to the public API.

### REQ-5: Rate-limit fail-mode configuration

The system SHALL make the `/internal/*` rate-limit behaviour on Redis outage
configurable, defaulting to fail-closed in production.

- **REQ-5.1**: THE `Settings` class SHALL expose a new field
  `internal_rate_limit_fail_mode: Literal["open", "closed"] = "closed"`,
  with env alias `INTERNAL_RATE_LIMIT_FAIL_MODE`.
- **REQ-5.2**: WHEN `get_redis_pool()` returns `None` OR the `check_rate_limit`
  call raises any exception AND `settings.internal_rate_limit_fail_mode ==
  "closed"` THE handler SHALL raise
  `HTTPException(status_code=503, detail="Internal rate limit backend unavailable")`
  AND SHALL log at level `warning` with
  `event="internal_rate_limit_fail_closed"`, `caller_ip=<ip>`, `exc_info=True`.
- **REQ-5.3**: WHEN the same conditions apply AND
  `settings.internal_rate_limit_fail_mode == "open"` THE handler SHALL
  preserve the current SPEC-SEC-005 REQ-1.3 behaviour — allow the request,
  log `event="internal_rate_limit_redis_unavailable"` — unchanged.
- **REQ-5.4**: THE production env file in `klai-infra/` SHALL set
  `INTERNAL_RATE_LIMIT_FAIL_MODE=closed`; staging and development SHALL set
  `open` (or leave unset — the default of `closed` applies only when the
  service reads the production env). Deploy ordering: env var flip AFTER
  the code lands, to avoid 503s during the rollout.
- **REQ-5.5**: IF a legitimate caller (mailer, LiteLLM hook, LibreChat
  patch) receives 503 because Redis is down AND fail-mode is closed THE
  caller SHALL retry with the exponential-backoff pattern already used
  for partner-API 429s (no code change in callers — they already retry
  on 5xx).

### REQ-6: ast-grep regression rule

The system SHALL prevent regressions of REQ-1 via a mechanical CI rule.

- **REQ-6.1**: THE repository SHALL contain a rule file at
  `rules/no-string-compare-on-secret.yml` (sibling to the existing
  `rules/no-exec-run.yml` from SPEC-SEC-024) that matches Python code
  patterns equivalent to: `$SECRET_VAR == $RHS` OR `$SECRET_VAR != $RHS`
  where `$SECRET_VAR` identifier-name matches the regex
  `(?i)(secret|internal_token|bearer_token|api_key)`.
- **REQ-6.2**: THE rule SHALL run on every portal-api PR via
  `ast-grep/action` in `.github/workflows/portal-api.yml`, failing the
  job on any match.
- **REQ-6.3**: THE rule SHALL exempt test files under
  `klai-portal/backend/tests/` so characterisation tests that intentionally
  pass a wrong-secret negative case via `==` for assertion purposes do not
  break CI. The exemption uses the same allow-list pattern as
  `no-exec-run.yml` (SPEC-SEC-024-R7).
- **REQ-6.4**: THE rule SHALL have a severity of `error` (exit code non-zero)
  and a human-readable message pointing to this SPEC: "Use
  `hmac.compare_digest` for secret/token comparisons — see
  SPEC-SEC-INTERNAL-001 REQ-1."

### REQ-7: Dependency on SPEC-SEC-005

This SPEC depends on SPEC-SEC-005 and SHALL NOT redo the work covered there.

- **REQ-7.1**: THE rate-limit primitive introduced by SPEC-SEC-005 REQ-1
  (`_check_rate_limit_internal` at `internal.py:99-143`) SHALL be extended
  by REQ-5 in-place. REQ-5 modifies the fail-mode branch; it does NOT
  introduce a second limiter.
- **REQ-7.2**: THE audit-log writes introduced by SPEC-SEC-005 REQ-2
  (`_log_internal_call`, `_audit_internal_call`) SHALL remain untouched.
  REQ-4 (`sanitize_response_body`) applies to upstream error bodies only,
  not to audit-row content.
- **REQ-7.3**: THE rotation runbook from SPEC-SEC-005 REQ-3 SHALL be
  referenced by any SOPS-level change this SPEC introduces (namely the
  new `INTERNAL_RATE_LIMIT_FAIL_MODE` env var). No runbook edits are
  required by this SPEC beyond a single sentence pointing to REQ-5.
- **REQ-7.4**: IF SPEC-SEC-005 implementation is interrupted or reverted
  THEN this SPEC's acceptance tests SHALL still pass on the REQ-1, REQ-3,
  REQ-4, REQ-6, REQ-8, REQ-9, REQ-10 groups (they are independent of
  the audit trail). REQ-2 and REQ-5 depend on SPEC-SEC-005 shipping; the
  acceptance criteria for those groups SHALL be evaluated against the
  SEC-005-hardened code path only.

### REQ-8: No upstream response body echoed to a user-facing response

The system SHALL NEVER surface an upstream HTTP response body verbatim to
a user-facing output channel (chat UI return value, public API error JSON,
UI-rendered field). Upstream-error context SHALL be reduced to a generic
status code plus a correlation ID that operators can query in VictoriaLogs.

- **REQ-8.1**: THE `save_to_docs` MCP tool in
  `klai-knowledge-mcp/main.py:430-431` SHALL NOT return
  `f"... Details: {resp.text[:300]}"` or any variant that embeds
  upstream body bytes. THE replacement return value SHALL be
  `f"Error saving to docs: upstream returned HTTP {status}. Request ID: {request_id}. Operator: check VictoriaLogs."`
  where `request_id` is the propagated `X-Request-ID` (or a newly-minted
  UUID if missing).
- **REQ-8.2**: THE same rule SHALL apply to every other MCP tool return
  path, every FastAPI `HTTPException(detail=...)` that today embeds
  upstream body bytes, and every `return JSONResponse(content={"error": ...})`
  constructed from `resp.text`. A codemod SHALL rewrite the identified
  sites; the inventory is in research.md §"Internal-wave additions".
- **REQ-8.3**: WHEN an operator needs to recover the upstream body for
  debugging THE server-side log record produced by REQ-4 SHALL contain
  the sanitized body keyed by the same `request_id` the user sees.
  This preserves operator debuggability without exposing the body to
  the end user.
- **REQ-8.4**: Regression test SHALL confirm that
  `save_to_docs(...)` when the upstream returns a body containing the
  configured `DOCS_INTERNAL_SECRET` value DOES NOT return a string that
  contains the secret. The test mocks klai-docs to echo the auth
  header in its 500-body.
- **REQ-8.5**: THE contract applies to production AND development modes.
  There is no debug-mode escape hatch — an on-by-default flag is a
  regression waiting to happen. Operators debugging locally read
  structlog output directly.

### REQ-9: Fail-closed startup on empty outbound secrets

The system SHALL refuse to start (or SHALL refuse to issue the call) when a
service is configured to make an outbound call authenticated by a shared
secret but that secret is empty. Silent-empty-string auth is eliminated.

- **REQ-9.1**: WHEN `klai-mailer` loads `Settings` AND
  `settings.webhook_secret == ""` THE process SHALL raise
  `pydantic.ValidationError` at import time (via a `min_length=8`
  validator). No defaulting, no fallback. Finding 7.
- **REQ-9.2**: WHEN `klai-mailer` loads `Settings` AND
  `settings.internal_secret == ""` OR
  `settings.portal_internal_secret == ""` AND the respective consumer is
  used (internal-send endpoint, portal-language lookup) THE process
  SHALL EITHER refuse startup via `min_length` validator OR refuse the
  outbound/inbound call at runtime with a clear 503 / startup-error
  message. The implementation MAY choose startup-refusal (simpler,
  matches REQ-9.1) if a future deployment-order ambiguity does not
  forbid it.
- **REQ-9.3**: WHEN `klai-connector` loads `Settings` AND
  `settings.portal_internal_secret == ""` OR
  `settings.knowledge_ingest_secret == ""` THE process SHALL raise
  `pydantic.ValidationError` at import time. `PortalClient._headers()`
  SHALL NOT return an empty-string Bearer header. Finding 8.
  (Exception: if a build mode "config-only" is needed for smoke tests,
  it MUST be gated behind an explicit `ALLOW_EMPTY_OUTBOUND_SECRETS=1`
  env var AND the service SHALL log a warning `event="outbound_auth_disabled"`
  on every outbound call. Production deploys MUST NOT set this flag.)
- **REQ-9.4**: WHEN `klai-scribe/scribe-api` loads `Settings` AND
  `settings.knowledge_ingest_secret == ""` THE process SHALL raise
  `pydantic.ValidationError` at import time. The silent-omit pattern
  at `knowledge_adapter.py:51-60` SHALL be replaced by an unconditional
  header injection. Finding 9.
- **REQ-9.5**: WHEN `klai-knowledge-mcp` loads env AND either
  `KNOWLEDGE_INGEST_SECRET` or `DOCS_INTERNAL_SECRET` is empty THE
  process SHALL refuse to start (module-level assertion). The
  `if KNOWLEDGE_INGEST_SECRET:` header-injection guard at
  `main.py:144-145` SHALL become unconditional after the startup guard
  ensures the value is non-empty. Finding 9.
- **REQ-9.6**: THE `pydantic-settings`-based validators SHALL use
  `@field_validator("<name>", mode="after")` with a
  `min_length(v: str) -> str: assert len(v) >= 8` body. The threshold
  matches REQ-4.2's 8-char-minimum (anything shorter cannot be a
  real 256-bit secret). Strings shorter than 8 chars at startup cause
  the validator to raise — failing the pod startup loudly.
- **REQ-9.7**: Acceptance test SHALL attempt to boot each of the five
  services with an empty value for each affected env var AND assert
  the process exits non-zero within 5 seconds with a `ValidationError`
  or equivalent startup error. Five services × N vars = the full
  matrix; see acceptance.md AC-9.

### REQ-10: Sanitize error-body persisted columns

The system SHALL NEVER persist a raw upstream response body to a
database column, JSONB field, or any durable store. Persisted error-body
columns SHALL be written through `sanitize_response_body()` first.

- **REQ-10.1**: THE `klai-connector/app/services/sync_engine.py`
  write at lines 530-539 that sets
  `error_details = [{"error": f"http_{status_code}", "service": ..., "detail": enqueue_err.response.text[:500]}]`
  SHALL be rewritten to pass `enqueue_err.response` through the
  connector's wrapper of `sanitize_response_body()` BEFORE building the
  dict. The `max_len=500` truncation stays; the sanitizer layer is added
  on top. Finding 12.
- **REQ-10.2**: THE same rewrite SHALL apply to every other
  `enqueue_err.response.text[...]` or `exc.response.text[...]` write
  across connector/portal/mailer/scribe/knowledge-mcp that lands in
  ANY `error_details`-shaped JSONB column OR any log payload forwarded
  to a downstream service's DB. Research.md Inventory G enumerates the
  persisted-body sites.
- **REQ-10.3**: THE Acceptance test for `sync_engine` SHALL mock
  knowledge-ingest to return an HTTPStatusError with a body containing
  `settings.knowledge_ingest_secret` AND assert that the resulting
  `sync_runs.error_details` row in Postgres does NOT contain the
  verbatim secret substring. Run via SQLAlchemy fixture + fakeredis.
- **REQ-10.4**: THE companion `report_sync_status` call in
  `portal_client.py` — which forwards `error_details` to portal for
  display in the connector-management UI — SHALL be verified to NOT
  leak the secret even when the portal-side storage path is tested
  end-to-end. Integration test in acceptance.md AC-10.
- **REQ-10.5**: Existing rows in `connector.sync_runs.error_details`
  may already contain reflected upstream bodies from before this SPEC
  (see Assumption A8). A backfill scrub migration is OUT of scope; a
  one-off `regexp_replace` may be run manually if an audit requires it.

---

## Non-Functional Requirements

- **Performance**: REQ-4 sanitization SHALL add no more than 100 µs p95
  to a logged error response (dominated by string search over at most
  10 secrets × 512-byte body). REQ-2 targeted delete SHALL complete in
  under 50 ms p95 against a Redis instance holding 10 000 keys
  (SCAN+UNLINK with a `COUNT 100` hint).
- **Observability**: new stable log keys (`internal_rate_limit_fail_closed`,
  `response_body_sanitized`, `proxy_header_injection_blocked`,
  `librechat_cache_invalidation_failed`) SHALL be queryable in VictoriaLogs.
- **Backward compatibility**: No internal caller contracts change. REQ-5
  is the only behavioural change visible to callers, and production
  defaults to fail-closed only after a staged rollout (see REQ-5.4).
- **Security**: The strip in REQ-3 runs ONCE per request in the BFF proxy;
  the taxonomy.py fix (REQ-1) runs once per internal call. No hot-path
  regressions.
