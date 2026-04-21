# SPEC-SEC-008 — Implementation Plan

This plan decomposes SEC-008 into four work streams (one per finding) plus a cross-cutting verification stream. Each stream is independently deployable; the sequence below prevents auth regressions by landing `hmac.compare_digest` and audience verification before any Caddy-layer change goes live.

Work affects multiple repos (klai-connector, klai-infra, klai-portal backend config, upstream vexa-bot-manager or local fork, klai-docs). Per rule **multi-file decomposition**, each stream is its own PR.

---

## Stream 1 — F-017: klai-connector hardening

**Scope:** `klai-connector/app/middleware/auth.py`, `klai-connector/app/core/config.py`, `klai-connector/docs/security.md` (new).

### Tasks

1. **Config field.** Add `zitadel_api_audience: str` to the klai-connector `Settings` class with a `model_validator(mode="after")` that raises on empty. Pattern mirrors SEC-012 changes in scribe/research-api.
2. **Introspection-layer aud check.** In `AuthMiddleware._introspect` (or in `dispatch` right after `claims = await self._introspect(...)`), verify `settings.zitadel_api_audience` against the `aud` claim of the introspection response. `aud` in Zitadel introspection responses can be a string or list — handle both (list membership check). Reject on mismatch; do **not** insert into `_token_cache`.
3. **Constant-time compare.** Replace `if self._portal_secret and token == self._portal_secret:` (line 75) with:
   - null-check on `self._portal_secret`,
   - `hmac.compare_digest(token.encode(), self._portal_secret.encode())`.
   This overlaps with SEC-004 F-009. Coordinate by:
   - SEC-008 owns the klai-connector change here.
   - SEC-004 will reference SEC-008 AC-2 as "already landed" when its own PR lands for focus/scribe/portal webhooks.
4. **Structured log on bypass.** Emit a `portal_bypass_used` structured log event (structlog) with `request_id` when the bypass branch executes. Never log secret or token values.
5. **Security runbook.** Create `klai-connector/docs/security.md` (or extend existing) with:
   - `portal_caller_secret` rotation schedule (quarterly minimum).
   - Rotation runbook: SOPS edit → `sops -d -i && vim && sops -e -i` → `mv` → coordinated redeploy of klai-connector and portal-api.
   - Cross-reference to SOPS decrypt-edit-reencrypt-in-place pattern per `.claude/rules/klai/pitfalls/process-rules.md` `follow-loaded-procedures`.
6. **Zitadel aud availability check (prework).** Before Task 2 ships, run a manual check: mint a token from the current Zitadel app, call introspection, inspect the response for a consistent `aud` claim. If absent/inconsistent, open a Zitadel app-config ticket, block Task 2 until resolved. Document outcome in `research.md` open items.

### Blast radius

- Any existing portal-api → klai-connector call that happens to share a token whose `aud` is another Klai service will break. Mitigation: confirm current token-mint flow in portal-api uses a connector-specific audience or (more likely) uses the `portal_caller_secret` bypass, not a Zitadel token.
- `_token_cache` continues to work; aud-check happens before cache write.

### Dependencies

- **Depends on:** SEC-012 pattern (audience-verification pydantic validator) for reference. SEC-012 does not need to ship first — the pattern is documented, not a shared library.
- **Coordinates with:** SEC-004 F-009 (constant-time compare across other services).

---

## Stream 2 — F-018: Dev environment gating

**Scope:** `core-01:/opt/klai/caddy/Caddyfile` (reference only — change via deploy pipeline), portal-api-dev configuration, Postgres dev-DB verification.

### Tasks

1. **Pick gate mechanism (annotation-cycle decision).** Present three options to the user:
   - (a) Caddy `basic_auth` on `@dev-host` — simplest, shared credential, fine for internal-only dev.
   - (b) Caddy IP allowlist (`remote_ip` matcher) on `@dev-host` — requires static egress IPs for Mark + office + CI.
   - (c) Distinct Zitadel app `klai-dev` with `aud=klai-dev` enforced by portal-api-dev.
   **Default recommendation:** (a) basic_auth for speed; (c) as a follow-up once SEC-012 audience-verification is standard.
2. **Implement gate.** Apply the chosen option:
   - (a) add `basicauth @dev-host { ... }` with a bcrypt-hashed credential stored in SOPS; update Caddyfile via deploy pipeline; restart Caddy.
   - (b) add `@dev-host { host dev.{$DOMAIN}; remote_ip <allowlist> }` matcher; restart Caddy.
   - (c) add SEC-012-style audience verification to portal-api-dev; mint a separate Zitadel app; rotate dev-portal clients.
3. **Observability header.** Add `header x-env dev` to the `@dev-host` block regardless of which gate option is picked.
4. **Dev-DB isolation verification.** Run on core-01:
   ```sql
   -- Confirm portal_api_dev has its own database.
   SELECT datname FROM pg_database WHERE datname LIKE 'klai%';
   -- Confirm portal_api_dev cannot reach prod rows.
   SET ROLE portal_api_dev; SELECT count(*) FROM klai.portal_orgs; -- expect permission denied or 0
   ```
   Record outcome in `research.md`. If shared DB is discovered, add a migration task (separate PR) before activating the Caddy gate.
5. **Log to VictoriaLogs.** Verify a request to `dev.getklai.com` surfaces in `service:portal-api-dev` with the correct `request_id` chain. Follow SEC-INFRA-004 log pipeline.
6. **Document revert path.** In PR body, describe how to roll back the gate if it blocks legitimate dev traffic.

### Blast radius

- Dev flow for engineers: anyone accessing dev.getklai.com must now authenticate. Communicate the new basic-auth credential (or allowlist IPs, or dev-Zitadel app credentials) to the team before the change lands.
- CI jobs that hit dev.getklai.com need their secrets updated.

### Dependencies

- **Does not depend on Stream 1.** Can land in parallel.
- **Coordinates with SEC-009** for the SERVERS.md entry update.

---

## Stream 3 — F-020: vexa-bot-manager audit and hardening

**Scope:** `vexaai/vexa-lite` (upstream) or local fork; deploy config for `klai-core-vexa-bot-manager-1`.

### Tasks

1. **Source audit.** Clone or inspect `vexaai/vexa-lite` at the pinned image tag used by `klai-core-vexa-bot-manager-1`. Identify:
   - HTTP framework (FastAPI? Express? Go net/http?).
   - Existing middleware (auth? rate limit? CORS?).
   - Endpoint list reachable via `/bots/*` per Caddy config.
   Record findings in `research.md`.
2. **Decide fork vs. upstream PR.**
   - If upstream is responsive and accepts auth middleware: upstream PR + pin new tag.
   - If not: local fork in Klai infra, vendor the Dockerfile, publish private image.
3. **Add Bearer auth middleware.** Match the `InternalSecretMiddleware` pattern used by knowledge-ingest:
   - Header: `X-Internal-Secret` (preferred) or standard `Authorization: Bearer`.
   - Env var: `VEXA_BOT_MANAGER_SECRET` (delivered via SOPS).
   - Exclude `/health`.
   - Fail-closed on empty env var at startup (`model_validator`).
4. **Caller audit.** Identify every caller of `/bots/*` in the Klai codebase. Search for `bots/` and `vexa-bot-manager` across klai-portal, klai-focus, klai-scribe, klai-connector. Record the full caller list in `research.md`.
5. **Caller update.** Update each caller to include the new secret header, using `get_trace_headers()` for request-id propagation (per `klai/projects/portal-logging-py.md`).
6. **Route scope question.** If the caller audit shows only internal callers, raise a flag for SEC-009: Caddy `/bots/*` route should move to an internal-only path. SEC-008 does not move the Caddy route — it adds auth. SEC-009 owns the Caddy topology cleanup.
7. **E2E test.** Curl `/bots/health` → 200. Curl `/bots/<any-real-endpoint>` without header → 401. Curl with valid header → 200.

### Blast radius

- All legitimate callers of `/bots/*` must roll forward with the new secret or they break. Plan a coordinated deploy.
- If the upstream vexa-bot-manager has no auth middleware today, the Caddy `10/min/IP` rate limit has been the only throttle — expect some noise in logs before the 401 wall shows up in production traffic.

### Dependencies

- **Does not depend on Streams 1 or 2.** Can land in parallel.
- **Upstream dependency on vexaai/vexa-lite** — may block if upstream is unresponsive; fork fallback is documented.

---

## Stream 4 — F-022: docs-app audit and hardening

**Scope:** `klai-docs` repo, deploy config for `docs-app:3010`.

### Tasks

1. **Source audit.** Clone or inspect `klai-docs`. Identify:
   - Purpose (marketing site? authenticated KB reader? internal engineering docs?).
   - Framework (Nextra? MkDocs? Next.js?).
   - Existing middleware or session handling.
   - Endpoints reachable under `/docs/*`.
   Record in `research.md`.
2. **Classify.** Based on audit, classify docs-app as one of:
   - **(P) Public marketing** — no auth needed; close F-022 as intentional-and-documented; SEC-009 updates SERVERS.md.
   - **(A) Authenticated KB reader** — add Zitadel session check; non-authed users get 401 (API) or redirect to portal login (SPA).
   - **(H) Hybrid** — split routes: marketing paths public, tenant paths authenticated.
3. **Implement based on classification.**
   - (P): no code change; update SERVERS.md via SEC-009.
   - (A) or (H): add middleware at the Next.js/Express layer that checks for a Zitadel session cookie (reuse portal session mechanism where possible).
4. **E2E test per classification.**
   - (P): all paths anonymous → 200.
   - (A): anonymous → 401/redirect; authed → 200.
   - (H): marketing → 200 anonymous; tenant → 401 anonymous.

### Blast radius

- For (A)/(H): readers without a Zitadel session (e.g., public Google indexing of tenant content, if any) lose access. If any tenant-docs URL is currently indexed by search engines, audit SEO impact before landing.

### Dependencies

- **Does not depend on other streams.**
- **Coordinates with SEC-009** for SERVERS.md update.

---

## Stream 5 — Cross-cutting verification and documentation

**Scope:** PR bodies, CI test additions, `.moai/audit/99-fix-roadmap.md` status updates, minimal SERVERS.md link.

### Tasks

1. **Per-route E2E test matrix.** Add CI integration tests (one per route) covering AC-10: anonymous, wrong-audience token, correctly scoped token.
2. **Observability spot check.** After each stream lands in prod, run a VictoriaLogs query to confirm AC-12 log fields are emitted.
3. **SERVERS.md minimal link.** Add a one-line `> See SPEC-SEC-009 for the full route/auth matrix rewrite.` near the top of `klai-infra/SERVERS.md`. The full rewrite is SEC-009's responsibility.
4. **Close findings in roadmap.** Update `.moai/audit/99-fix-roadmap.md` SEC-008 row to status `done` once all four streams merge.
5. **Post-implementation review.** Per Rule 3, list potential regressions:
   - Token-confusion across Klai services after `aud` verification — validate all legitimate callers have correctly configured audiences.
   - `hmac.compare_digest` byte-encoding pitfalls with non-ASCII secrets (ensure SOPS secrets are ASCII/hex).
   - Dev-env basic_auth credential sharing — rotation needed if it leaks.
   - vexa-bot-manager Caddy rate limit still applies; verify it doesn't mask a 401 flood after the auth gate lands.

---

## Execution order

Because Streams 1-4 are independent, the ideal order is:

1. **Parallel prework** (all four): source audits + research.md entries.
2. **Stream 1** lands first — no external surface change, reduces F-017 severity immediately.
3. **Streams 3 and 4** land in parallel with Stream 2. Stream 2's Caddy edit is the only one that affects external reachability of a whole subdomain; coordinate with team communication.
4. **Stream 5** closes the SPEC: CI matrix, roadmap update, SERVERS.md link to SEC-009.

---

## Risk log

- **R1 (F-017).** Zitadel introspection does not consistently return `aud`. Mitigation: prework check in Stream 1 Task 6 before writing code.
- **R2 (F-018).** Picking basic_auth for dev creates a single shared credential that leaks easily. Mitigation: IP allowlist or Zitadel-dev app as a follow-up; document rotation.
- **R3 (F-020).** Upstream vexa-bot-manager is unresponsive to auth-middleware PR. Mitigation: local fork + private image.
- **R4 (F-022).** docs-app turns out to expose tenant content to search engines (SEO regression on closing). Mitigation: SEO audit before landing (A)/(H) classification.
- **R5 (SEC-004 overlap).** `hmac.compare_digest` in klai-connector (SEC-008 AC-2) conflicts with a later SEC-004 sweep. Mitigation: land SEC-008 first; SEC-004 skips klai-connector file.
