# SPEC-SEC-008 — Research

Short-form research notes supporting SEC-008. Source documents:

- `.moai/audit/04-3-prework-caddy.md` — Caddy-verify findings (F-017 through F-022).
- `.moai/audit/99-fix-roadmap.md` §SEC-008 — roadmap entry and scope.
- `klai-connector/app/middleware/auth.py` — current connector middleware.
- `core-01:/opt/klai/caddy/Caddyfile` — live Caddy routing (reference only).
- `.claude/rules/klai/infra/observability.md` — cross-service trace correlation pattern.

---

## Finding snapshots

### F-017 — klai-connector is public contra SERVERS.md

- `connector.getklai.com` is a dedicated public subdomain in Caddy, reverse-proxied to `klai-connector:8200`.
- SERVERS.md (line 110 per audit) lists klai-connector as `klai-net` = internal. This is doc drift — the service is genuinely reachable from the internet.
- Auth layers today:
  1. Zitadel introspection via `AuthMiddleware` (`klai-connector/app/middleware/auth.py:44-97`).
  2. Portal-caller bypass via `portal_caller_secret` env var (lines 75-78).
- **Audience claim.** Current middleware extracts `urn:zitadel:iam:user:resourceowner:id` but does not verify `aud`. Whether the Zitadel introspection response even includes a consistent `aud` claim needs a manual probe against the currently-configured Zitadel application. **Open item:** run `curl -u $CID:$CSECRET <introspection_url> -d token=<real_token> | jq .aud` against the prod Zitadel app before Stream 1 Task 2 ships.
- **Constant-time compare.** Line 75 uses `token == self._portal_secret`. Python string `==` is short-circuit; not constant-time. With a leaked `portal_caller_secret`, this is academic, but in defense-in-depth terms `hmac.compare_digest` is cheap and correct.

### F-018 — dev.getklai.com is publicly reachable

- Caddy block: `dev.{$DOMAIN}` → `portal-api-dev:8010` + dev-SPA static asset serving.
- **Open items:**
  - Is dev env a single shared instance or per-PR preview? (assumed: single shared, to be verified).
  - Does dev use the same Zitadel application as prod? (likely yes; SPEC requires aud split if (c) is chosen).
  - Does `portal_api_dev` connect to a separate database or share `klai`? PRE-A (per `.moai/audit/04-3-prework-caddy.md`) confirms the role exists with `bypassrls=false`; database target not yet confirmed.
- Dev-environment drift risk is structural: debug endpoints, feature flags, test users. A public dev env with shared Zitadel means any dev weakness becomes a prod-adjacent attack.

### F-020 — vexa-bot-manager `/bots/*` unknown auth

- Caddy: `*.getklai.com/bots/*` → `vexa-bot-manager:8000`, rate limit `10/min/IP`.
- Source is in `vexaai/vexa-lite` upstream (image tag pinned by Klai deploy). **Not yet read during Phase 3 audit.**
- Risk modeling: if no auth, unauthenticated bot spawn → compute burn, meeting-join abuse, spoofed recordings.
- **Open item:** identify legitimate callers. If only portal-api calls `/bots/*`, the right fix is not "add auth and keep public" but "remove the public Caddy route entirely" (SEC-009 scope).

### F-022 — docs-app `/docs/*` unknown auth

- Caddy: `*.getklai.com/docs/*` → `docs-app:3010`, rate limit `60/min/IP`.
- Source repo: `klai-docs`. Framework unknown pre-audit.
- Severity is UNKNOWN because the classification (marketing / authenticated / hybrid) drives everything.
- **Open item:** read `klai-docs` source; classify per plan.md Stream 4 Task 2.

---

## Connector audience-check dependency

The SEC-012 pattern (audience-verification pydantic validator + middleware check) assumes the service uses `python-jose` to decode a signed JWT and inspect `aud` directly. klai-connector does **not** do that — it uses OIDC token introspection (RFC 7662), which returns a JSON body with claims. Two implementation details differ:

- **Signature is not checked** in klai-connector (Zitadel is trusted to return `active=true` only for valid tokens).
- **`aud` presence in introspection response** depends on Zitadel application configuration. Some Zitadel configs omit `aud` from the introspection response; others include it as a single string; others as a list.

Before writing the `aud` check, verify the actual response shape. The plan prework task (Stream 1 Task 6) covers this.

---

## Cross-reference to SEC-012, SEC-004

- **SEC-012** (JWT audience verification for scribe + research-api) establishes the *pattern* but runs in JWT-decode context (`python-jose`). SEC-008 adapts the pattern for the introspection-response shape. Both SPECs can land in either order.
- **SEC-004** (defense-in-depth middleware) includes `hmac.compare_digest` fixes across focus, scribe, portal webhooks, and connector. SEC-008 lands the connector piece; SEC-004 lands the rest and references SEC-008 AC-2.

---

## SERVERS.md doc drift (SEC-009 boundary)

SERVERS.md lists `klai-net` for klai-connector, implying internal-only. Caddy says otherwise. The full rewrite (every route listed with auth layer + rate limit + purpose) is SEC-009's job. SEC-008 only adds a one-line link to SEC-009 to avoid leaving actively misleading documentation in place while SEC-008 is in flight.

---

## Open items for implementation

1. **Zitadel introspection `aud` probe.** Blocker for Stream 1 Task 2. Manual curl + jq against prod Zitadel app.
2. **Dev env shape.** Blocker for Stream 2 option choice. Confirm single-instance vs. per-PR; confirm dev-DB isolation.
3. **vexa-bot-manager source read.** Blocker for Stream 3. Decide fork vs. upstream PR after reading the code.
4. **docs-app classification.** Blocker for Stream 4. Determines whether any code change is needed at all.
5. **Legitimate caller audit for `/bots/*`.** Drives whether Caddy keeps the public route or moves it internal (SEC-009 coordination).

All five are research-shaped tasks, not code changes. Resolve before committing to Stream 2-4 implementation choices.
