---
id: SPEC-SEC-008
version: 0.1.0
status: draft
created: 2026-04-19
updated: 2026-04-19
author: Mark Vletter
priority: high
---

# SPEC-SEC-008: Caddy Exposure Hardening

## HISTORY

### v0.1.0 (2026-04-19)
- Initial draft. Consolidates F-017, F-018, F-020, F-022 from Phase 3 Caddy-verify audit.
- Created as P1 group after Caddy config on `core-01:/opt/klai/caddy/Caddyfile` showed four public routes not documented in `klai-infra/SERVERS.md`.
- Paired with SEC-009 (doc drift) and SEC-004 (defense-in-depth) — see Out of Scope and Requirements for boundary.

---

## Goal

Bring the four publicly reachable Caddy entry points surfaced during the Phase 3 Caddy-verify step into a hardened, documented posture:

1. **klai-connector** (`connector.getklai.com`) — intentionally public, but currently relies on Zitadel introspection without explicit `aud` verification and on a non-constant-time portal-secret compare (F-017).
2. **portal-api-dev** (`dev.getklai.com`) — dev environment is reachable from the open internet without an additional auth gate, uses the same Zitadel app as prod, and dev-DB isolation is unverified (F-018).
3. **vexa-bot-manager** (`*.getklai.com/bots/*`) — reverse-proxied publicly with only a rate limit; auth middleware status is unknown (F-020).
4. **docs-app** (`*.getklai.com/docs/*`) — reverse-proxied publicly with only a rate limit; auth scope (marketing docs vs. logged-in KB reader) is unknown (F-022).

At the end of this SPEC, every public Caddy route is one of: (a) documented as intentionally anonymous, (b) gated by a verified auth middleware with audience verification, or (c) gated by a Caddy-layer control (basic auth, IP allowlist, or distinct Zitadel application).

## Success Criteria

- klai-connector validates the Zitadel `aud` claim against a configured audience on every introspected token (matches SEC-012 pattern).
- klai-connector uses `hmac.compare_digest` for `portal_caller_secret` comparison and has a documented rotation schedule.
- `dev.getklai.com` is no longer reachable from arbitrary internet clients: either Caddy basic auth, an IP allowlist on the `@dev-host` block, or a distinct Zitadel app with `aud=klai-dev` is in place (user picks in plan phase).
- `portal_api_dev` database isolation from prod is verified (separate DB OR demonstrably RLS-separated with no shared rows).
- vexa-bot-manager has documented Bearer-token auth; if missing, auth middleware is added before this SPEC closes.
- docs-app has documented auth posture (public marketing vs. authenticated KB reader); non-public paths require session auth.
- All four routes have end-to-end tests: anonymous → 401; wrong-audience token → 401; correctly scoped token → 200.
- `klai-infra/SERVERS.md` is updated or links to SEC-009 for the doc-drift fix (SEC-009 owns the actual doc rewrite).

---

## Environment

- **Caddy:** `core-01:/opt/klai/caddy/Caddyfile` (reference only — **do not modify via MCP**, change via deploy pipeline). Caddyfile already emits JSON logs to stdout per SPEC-INFRA-004.
- **klai-connector:** Python 3.13, FastAPI + Starlette middleware, Zitadel OIDC introspection; current auth in `klai-connector/app/middleware/auth.py` (Bearer token + `portal_caller_secret` fast-path).
- **portal-api-dev:** Same image as portal-api, `portal-api-dev:8010`, connected to `portal_api_dev` DB role.
- **vexa-bot-manager:** `klai-core-vexa-bot-manager-1` container, upstream `vexaai/vexa-lite` (source to be read during plan phase).
- **docs-app:** `klai-docs` repo, served at `docs-app:3010`.
- **Zitadel:** Introspection endpoint; currently one Zitadel app is shared across prod and dev. Audience-claim behavior via introspection response must be verified per `.moai/audit/04-3-prework-caddy.md` open items.

## Assumptions

- **klai-connector public exposure is intentional.** External integrations (webhook receivers, partner pings, SaaS OAuth callbacks) need a public entry. The task is hardening, not removing the route.
- The `portal_caller_secret` is stored in SOPS and delivered to klai-connector via env var; rotation is procedural, not code-side.
- Zitadel introspection returns a claims body that includes `aud` when the application is configured for it. If the current shared Zitadel app does not populate `aud` consistently, a config change in Zitadel is part of this SPEC.
- dev.getklai.com is one shared dev environment, not a per-PR preview. (To be verified in research.md; if wrong, REQ-2.x changes.)
- The vexa-bot-manager container runs a FastAPI-like HTTP server with code we can reach in `vexaai/vexa-lite` or a local fork.
- docs-app is assumed to be either a static Nextra/MkDocs site or a Next.js app; the audit will determine which.

## Out of Scope

- **SERVERS.md rewrite** — owned by SEC-009 (P3, doc-drift). This SPEC only links to SEC-009 and does not rewrite `klai-infra/SERVERS.md`.
- **retrieval-api / knowledge-ingest hardening** — owned by SEC-010 and SEC-011 respectively. Those services are not Caddy-exposed.
- **JWT audience verification for scribe and research-api** — owned by SEC-012. SEC-008 reuses the SEC-012 pattern for klai-connector but does not modify scribe/research-api.
- **Middleware refactor across focus/scribe/portal webhooks** — owned by SEC-004. The klai-connector `hmac.compare_digest` fix overlaps with SEC-004 F-009; coordinated delivery is addressed in plan.md.
- **Rate-limit tuning** on Caddy — accepted as-is except where a hardening requirement implies a change.
- **Removal of `connector.getklai.com`** — explicitly out of scope per Assumptions.
- **Retrofitting mTLS between portal-api and klai-connector** — tracked as a follow-up option in plan.md; only implemented in SEC-008 if the user selects it during the plan annotation cycle.

---

## Security Findings Addressed

| Finding | Severity | Source | Summary |
|---|---|---|---|
| F-017 | HIGH | `.moai/audit/04-3-prework-caddy.md` §F-017 | klai-connector is publicly routed via `connector.getklai.com`, contradicting SERVERS.md. Zitadel `aud` not verified explicitly; `portal_caller_secret` compared with `==` (not constant-time). Upgrades F-009 from MEDIUM to HIGH. |
| F-018 | MEDIUM | `.moai/audit/04-3-prework-caddy.md` §F-018 | `dev.getklai.com` → portal-api-dev is publicly reachable. Shared Zitadel app with prod allows cross-env token reuse. `portal_api_dev` role exists (per PRE-A) but DB isolation is unverified. |
| F-020 | MEDIUM | `.moai/audit/04-3-prework-caddy.md` §F-020 | `*.getklai.com/bots/*` → vexa-bot-manager. Rate limit 10/min/IP in Caddy; service-level auth unknown. Risk: unauthenticated bot spawning, resource burn, meeting-join manipulation. |
| F-022 | UNKNOWN | `.moai/audit/04-3-prework-caddy.md` §F-022 | `*.getklai.com/docs/*` → docs-app. Rate limit 60/min/IP in Caddy; service-level auth unknown. Severity depends on whether docs-app is a marketing site or the authenticated KB reader. |

Roadmap context: `.moai/audit/99-fix-roadmap.md` line 21 (SEC-008 row) and lines 171-199 (SEC-008 changes + SEC-009 split).

## Threat Model

### Common threats across all four routes

- **Public reachability:** any client on the internet can reach the TCP endpoint via Caddy. Defense is at the service layer, not the network layer.
- **Credential leak cascades:** a leaked shared secret, a leaked bypass token, or a stolen staff laptop credential turns a public route into a fully authenticated attacker.
- **Cross-environment token reuse:** if dev and prod share a Zitadel app and neither verifies `aud`, a token minted for dev works in prod and vice versa.

### F-017 — klai-connector

- **Portal-secret leak (T1).** If `portal_caller_secret` leaks (logs, git history, env-dump, backup), any internet client gains full portal-caller privileges inside klai-connector. Current `token == self._portal_secret` compare (`auth.py:75`) is a non-constant-time dependency — timing signal is narrow but exists.
- **Token confusion (T2).** If a Zitadel token minted for another Klai service (scribe, research-api) is presented to klai-connector and the connector does not verify `aud`, it may accept the token and grant introspection-level access with another service's user identity.
- **Introspection availability (T3).** klai-connector hard-depends on Zitadel introspection. An introspection outage + cached-token window (`_CACHE_TTL = 300`) means 5 minutes of stale auth decisions — acceptable for SPEC-008 but documented.

### F-018 — dev.getklai.com

- **Dev weak-auth escalation (T4).** Dev environments historically carry debug endpoints, test users with weak passwords, feature flags that skip billing/ACL checks, and looser logging. Public exposure turns any of these into production-adjacent attack surface.
- **Dev-token → prod reuse (T5).** Shared Zitadel app across dev/prod without `aud` split means a token intended for dev is valid at prod, and vice versa.
- **Dev-DB leak of prod data (T6).** If `portal_api_dev` connects to the same `klai` database but relies only on `org_id` separation, a SQL injection or RLS-bypass in dev code exposes prod tenant data.

### F-020 — vexa-bot-manager

- **Unauthenticated bot spawn (T7).** If `/bots/*` has no Bearer auth, an internet client can spawn Vexa recording bots. Consumes compute, may join meetings without consent, produces spoofed recordings.
- **Lateral meeting access (T8).** If a spawned bot can be addressed via the same API, attackers may be able to hijack or query active meeting sessions.

### F-022 — docs-app

- **Credentialed content leak (T9).** If docs-app is the authenticated KB reader (per-org markdown rendering), unauthenticated access leaks tenant knowledge bases.
- **User-data endpoint exposure (T10).** If docs-app has write/edit endpoints (e.g., a Gitea reverse-proxy path) those may be internet-reachable.

---

## Requirements

### REQ-1: klai-connector hardening (F-017)

**REQ-1.1:** The klai-connector Settings class SHALL expose a `zitadel_api_audience` configuration field. WHEN the service starts IF `zitadel_api_audience` is unset or empty THEN the service SHALL fail to start with a clear error log (matches SEC-012 pattern).

**REQ-1.2:** WHEN klai-connector introspects a Bearer token AND the introspection response is `active=true`, the middleware SHALL verify that the `aud` claim of the introspection response contains `settings.zitadel_api_audience`. IF the audience does not match THEN the middleware SHALL return HTTP 401 with body `{"error": "unauthorized"}` and SHALL NOT cache the claims.

**REQ-1.3:** WHEN klai-connector compares an incoming Bearer token against `self._portal_secret`, the comparison SHALL use `hmac.compare_digest` (byte-level) instead of `==`. This requirement is coordinated with SEC-004 F-009 — SEC-008 owns the klai-connector change; SEC-004 owns equivalent fixes in focus/scribe/portal webhooks.

**REQ-1.4:** The klai-connector repo SHALL contain a `SECURITY.md` (or section in `docs/security.md`) documenting the `portal_caller_secret` rotation schedule (quarterly minimum) and the rotation runbook (SOPS edit → re-encrypt → redeploy connector + portal-api).

**REQ-1.5:** IF Zitadel introspection does not reliably return an `aud` claim (to be verified in research.md), THEN a Zitadel app-config change SHALL be applied before REQ-1.2 ships, and the dependency SHALL be noted in the klai-connector deployment runbook.

### REQ-2: Dev environment gating (F-018)

**REQ-2.1:** WHILE `dev.getklai.com` is routed in Caddy, the `@dev-host` handle block SHALL enforce at least one of the following additional gates (selected during plan-phase annotation; default recommendation: IP allowlist):
- (a) Caddy `basic_auth` directive scoped to `@dev-host`,
- (b) Caddy IP-range allowlist (`remote_ip` matcher) scoped to `@dev-host`,
- (c) a distinct Zitadel application with `aud=klai-dev`, enforced by portal-api-dev at the app layer.

**REQ-2.2:** The `portal_api_dev` Postgres role and its connection target SHALL be verified to connect to a separate database (`klai_dev` or equivalent) from the production `klai` database. IF verification shows shared-database usage, THEN a migration to a separate database SHALL be added to plan.md before the Caddy change ships.

**REQ-2.3:** IF option (c) is selected for REQ-2.1, THEN portal-api-dev SHALL verify `aud=klai-dev` on every Zitadel token (reuse SEC-012 pattern).

**REQ-2.4:** The dev Caddy block SHALL emit a `x-env: dev` response header so that observability dashboards (VictoriaLogs, Grafana) can distinguish dev-origin traffic in shared log streams.

### REQ-3: vexa-bot-manager audit and hardening (F-020)

**REQ-3.1:** The vexa-bot-manager source (in `vexaai/vexa-lite` upstream or local fork) SHALL be read end-to-end, and its current authentication posture SHALL be documented in `research.md` (public? Bearer? internal-secret? no auth?).

**REQ-3.2:** IF vexa-bot-manager has no auth middleware OR relies only on network isolation THEN a Bearer-token auth middleware (matching the `InternalSecretMiddleware` pattern used elsewhere in Klai) SHALL be added before SEC-008 closes. Token delivered via SOPS-managed env var.

**REQ-3.3:** WHEN vexa-bot-manager receives a request to any path except `/health`, the service SHALL require a valid Bearer token. IF the token is missing or invalid THEN the service SHALL return HTTP 401.

**REQ-3.4:** The caller set of `/bots/*` SHALL be documented: who legitimately calls vexa-bot-manager publicly? If the answer is "nobody; only portal-api calls it," then Caddy SHALL move the `/bots/*` route to an internal-only path (coordinated with SEC-009 SERVERS.md updates).

### REQ-4: docs-app audit and hardening (F-022)

**REQ-4.1:** The `klai-docs` repo SHALL be read end-to-end, and its current authentication posture SHALL be documented in `research.md` (marketing/public, session-authenticated KB reader, or hybrid).

**REQ-4.2:** IF docs-app contains any endpoint returning tenant-scoped data (KB contents, org-specific docs, user-generated markdown) THEN those endpoints SHALL require Zitadel session authentication before SEC-008 closes.

**REQ-4.3:** IF docs-app is purely public marketing content, THEN SEC-008 SHALL document this in research.md and close the F-022 finding as "intentional, documented" (no code change beyond SEC-009 doc update).

**REQ-4.4:** WHEN docs-app receives a request to a path that requires auth AND the session is missing or invalid, the service SHALL return HTTP 401 (API path) or redirect to the portal login (SPA path).

### REQ-5: End-to-end verification and documentation

**REQ-5.1:** For each of the four routes, the SEC-008 PR SHALL include a verified end-to-end test:
- Anonymous request → 401 (or 200 for public-marketing case, documented as intentional).
- Token minted for a different audience → 401 (where audience verification applies).
- Correctly scoped token → 200.

**REQ-5.2:** `klai-infra/SERVERS.md` SHALL either be updated to reflect SEC-008 outcomes OR SEC-008 SHALL leave a one-line link to SEC-009 indicating the doc rewrite is owned there. SEC-008 does not own the full SERVERS.md rewrite.

**REQ-5.3:** The SEC-008 PR SHALL reference findings F-017, F-018, F-020, F-022 by ID in the PR body and link to `.moai/audit/04-3-prework-caddy.md`.

**REQ-5.4:** Observability: each hardened entry point SHALL log auth-decision outcome (200/401) with `request_id`, `org_id` (when available), and `service` fields so a VictoriaLogs query `service:klai-connector AND status:401` can reveal auth regressions.
