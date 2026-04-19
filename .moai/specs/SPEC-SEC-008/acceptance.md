# SPEC-SEC-008 — Acceptance Criteria

All criteria are written in EARS format (WHEN / WHILE / IF / WHERE ... THE system SHALL ...). Each criterion is independently testable. Findings F-017, F-018, F-020, F-022 are referenced explicitly per criterion.

---

## AC-1: klai-connector audience verification (F-017)

- **WHEN** klai-connector starts **IF** `zitadel_api_audience` is empty or unset **THE** service **SHALL** log an error and exit with a non-zero status code (matches SEC-012 pattern).
- **WHEN** klai-connector receives a request with a Bearer token **AND** Zitadel introspection returns `active=true` **THE** service **SHALL** verify that the `aud` claim contains `settings.zitadel_api_audience` before accepting the request.
- **WHEN** the `aud` claim of an introspected token does not match the configured audience **THE** service **SHALL** return HTTP 401 and **SHALL NOT** insert the claims into `_token_cache`.
- **WHEN** a valid token with correct audience is presented **THE** service **SHALL** proceed to attach `request.state.org_id` and call the downstream handler as it does today.

## AC-2: klai-connector constant-time secret compare (F-017)

- **WHEN** klai-connector compares an incoming Bearer token against `self._portal_secret` **THE** comparison **SHALL** use `hmac.compare_digest` with both operands encoded to bytes, replacing the current `==` comparison at `klai-connector/app/middleware/auth.py:75`.
- **WHEN** `self._portal_secret` is empty or unset **THE** bypass branch **SHALL NOT** execute; the request **SHALL** proceed to Zitadel introspection.
- **WHILE** the portal-secret bypass path is active **THE** middleware **SHALL** log a structured event `portal_bypass_used` with `request_id` (no token value, no secret value).

## AC-3: Portal-caller-secret rotation documentation (F-017)

- **WHEN** the SEC-008 PR is reviewed **THE** klai-connector repo **SHALL** contain written documentation of the `portal_caller_secret` rotation schedule (minimum: quarterly) and a step-by-step rotation runbook that covers SOPS edit, re-encryption, and coordinated redeploy of klai-connector and portal-api.

## AC-4: dev.getklai.com gating (F-018)

- **WHILE** `dev.getklai.com` is routed in Caddy **THE** `@dev-host` handle block **SHALL** enforce at least one of: (a) Caddy `basic_auth`, (b) IP allowlist via `remote_ip` matcher, or (c) a distinct Zitadel application with `aud=klai-dev` enforced by portal-api-dev.
- **WHEN** an unauthenticated request hits `dev.getklai.com` (anonymous, no basic_auth header, disallowed IP, or missing Zitadel session) **THE** system **SHALL** return HTTP 401 (or 403 when IP-blocked) before any dev-application handler runs.
- **WHEN** option (c) is selected **AND** portal-api-dev receives a Bearer token **IF** the token's `aud` claim is not `klai-dev` **THE** service **SHALL** return HTTP 401.

## AC-5: Dev database isolation (F-018)

- **WHEN** the SEC-008 implementation verifies DB isolation **THE** audit output **SHALL** confirm that `portal_api_dev` connects to a database that shares no rows with the production `klai` database (separate database preferred; at minimum, documented full RLS separation with a test).
- **IF** verification shows that `portal_api_dev` uses the same `klai` database as production **THEN** the SEC-008 PR **SHALL** include or link to a migration plan to a separate dev database before the Caddy-layer gate is activated.

## AC-6: Dev env observability (F-018)

- **WHEN** Caddy serves a response from the `@dev-host` block **THE** response **SHALL** include the header `x-env: dev`.
- **WHEN** VictoriaLogs is queried with `service:portal-api-dev` **THE** result set **SHALL** be non-empty for a request generated from dev.getklai.com within the last hour of a reference test.

## AC-7: vexa-bot-manager auth audit (F-020)

- **WHEN** SEC-008 research.md is submitted **THE** document **SHALL** contain a concrete description of vexa-bot-manager's current authentication posture based on a source-code read (reference: container `klai-core-vexa-bot-manager-1`, upstream `vexaai/vexa-lite`).
- **WHEN** vexa-bot-manager receives a request to any path other than `/health` **IF** no valid Bearer token is present in the `Authorization` header **THE** service **SHALL** return HTTP 401.
- **WHEN** vexa-bot-manager starts **IF** the expected `INTERNAL_SECRET` (or equivalent auth env var) is empty **THE** service **SHALL** log an error and fail to start.

## AC-8: vexa-bot-manager caller scope (F-020)

- **WHEN** SEC-008 completes **THE** PR **SHALL** document the legitimate caller set for `/bots/*` (e.g., "only portal-api"). **IF** no external caller is identified **THEN** the Caddy configuration **SHALL** be updated (in coordination with SEC-009) to move `/bots/*` to an internal-only route.

## AC-9: docs-app audit and hardening (F-022)

- **WHEN** SEC-008 research.md is submitted **THE** document **SHALL** contain a concrete description of docs-app's purpose (marketing vs. authenticated KB reader vs. hybrid) and its authentication posture, based on a source-code read of `klai-docs`.
- **WHEN** docs-app receives a request to a path that exposes tenant-scoped content **IF** no valid Zitadel session is present **THE** service **SHALL** return HTTP 401 for API paths or redirect to portal login for SPA paths.
- **WHEN** docs-app is determined to be purely public marketing content **THE** research.md **SHALL** record this explicitly; in that case, no code change to docs-app is required, and SEC-009 updates SERVERS.md accordingly.

## AC-10: End-to-end route verification

- **WHEN** SEC-008 CI runs **THE** test suite **SHALL** include, for each of `connector.getklai.com`, `dev.getklai.com`, `/bots/*`, and `/docs/*`:
  - an **anonymous** request that produces the documented outcome (401 for auth-required routes, 200 for public-marketing routes);
  - a **wrong-audience-token** request (where audience verification applies) that produces HTTP 401;
  - a **correctly scoped token** request that produces HTTP 200.
- **WHEN** any of the above tests fail **THE** SEC-008 PR **SHALL NOT** be mergeable.

## AC-11: Documentation cross-links

- **WHEN** the SEC-008 PR is opened **THE** PR body **SHALL** reference F-017, F-018, F-020, F-022 by ID and link to `.moai/audit/04-3-prework-caddy.md` and `.moai/audit/99-fix-roadmap.md#sec-008`.
- **WHEN** SEC-008 closes **THE** `klai-infra/SERVERS.md` file **SHALL** either be updated in this PR or contain an explicit `See SEC-009` reference for the doc-drift rewrite.

## AC-12: Observability of auth decisions

- **WHEN** klai-connector, portal-api-dev, vexa-bot-manager, or docs-app rejects a request for auth reasons **THE** service **SHALL** emit a structured log line containing `service`, `request_id`, `org_id` (when available), `status_code=401`, and a short `reason` tag (`missing_token`, `bad_audience`, `bad_secret`, `revoked`).
- **WHEN** a VictoriaLogs query `service:klai-connector AND status:401 AND reason:bad_audience` is executed **THE** query **SHALL** return non-empty results after the negative test in AC-10 has run.
