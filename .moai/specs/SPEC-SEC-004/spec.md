---
id: SPEC-SEC-004
priority: medium
status: draft
created: 2026-04-19
updated: 2026-04-19
author: Mark Vletter
version: 0.1.0
---

# SPEC-SEC-004: Defense-in-Depth Auth Middleware

## HISTORY

| Version | Date       | Author       | Change                                  |
|---------|------------|--------------|-----------------------------------------|
| 0.1.0   | 2026-04-19 | Mark Vletter | Initial draft from Phase 3 audit (F-005, F-006, F-009, F-017) |

## Goal

Add a defense-in-depth authentication layer to services that today rely solely on route-level `Depends(...)` for auth, and harden two known bypass-token comparison paths. Concretely:

1. Introduce an `AuthMiddleware` on `klai-focus/research-api` and `klai-scribe/scribe-api` as a safety-net, so that any future route added without `Depends(get_current_user)` still requires a valid Zitadel JWT.
2. Harden the Moneybird webhook in `klai-portal/backend`: fail-closed at startup when the webhook token is empty, use a constant-time comparison, return `401` on mismatch (not `200`), and log the source IP.
3. Fix the `portal_caller_secret` comparison in `klai-connector/app/middleware/auth.py` to use `hmac.compare_digest` (constant-time). This is now elevated because `klai-connector` is publicly exposed (see F-017).

## Success Criteria

- SC-1: Any unauthenticated request to `klai-focus/research-api` or `klai-scribe/scribe-api` (except `/health`) is rejected with `401` by middleware — without the request ever reaching a route handler.
- SC-2: Adding a new route to either service without `Depends(get_current_user)` still fails closed — verified by an integration test that registers a "trap" route and asserts `401`.
- SC-3: If `MONEYBIRD_WEBHOOK_TOKEN` is unset or empty at portal-api startup, the process fails to start (fail-closed).
- SC-4: Moneybird webhook with a wrong token returns `401` (not `200`) and logs the source IP at WARNING level.
- SC-5: Moneybird webhook token comparison and connector `portal_caller_secret` comparison both use `hmac.compare_digest`.
- SC-6: Existing valid flows are unaffected: portal → connector service-to-service calls still succeed; valid Zitadel JWTs still authenticate on focus/scribe.
- SC-7: No regression in observability: trace headers (`X-Request-ID`, `X-Org-ID`) continue to propagate.

## Environment

- **klai-focus/research-api** (FastAPI, Python 3.13) — currently registers only `CORSMiddleware` and `RequestContextMiddleware` in `app/main.py`; JWT validation is route-level via `Depends(get_current_user)`.
- **klai-scribe/scribe-api** (FastAPI, Python 3.13) — same pattern as focus.
- **klai-portal/backend** (FastAPI, Python 3.13) — Moneybird webhook handler in `app/api/webhooks.py`, lines 22-28.
- **klai-connector** (FastAPI, Python 3.13) — `AuthMiddleware` in `app/middleware/auth.py`, line 75: `token == self._portal_secret`.

## Dependencies

- **SPEC-SEC-012 must be in place first.** Focus and scribe `AuthMiddleware` instances need `ZITADEL_API_AUDIENCE` (and related Zitadel introspection env vars) configured for their service audiences. Without SEC-012, middleware introspection cannot validate audience correctly.
- Existing `klai-connector/app/middleware/auth.py` serves as the **reference implementation** for the middleware pattern on focus/scribe (Bearer parse, `/health` skip, Zitadel introspection, cache-by-hash, `request.state.org_id` binding).
- Existing `RequestContextMiddleware` in each service (from `logging_setup.py`) continues to run; `AuthMiddleware` must be registered **before** it so that `org_id`/`request_id` binding sees the authenticated context.

## Assumptions

- A-1: Zitadel introspection is already reachable from focus and scribe (same network as connector).
- A-2: focus and scribe expose a `/health` endpoint that must remain unauthenticated.
- A-3: The existing `get_current_user` FastAPI dependencies in focus/scribe are still required for user-object injection (org_id, user_id, roles) — middleware is additive, not a replacement.
- A-4: `klai-portal/backend/app/core/config.py` loads `moneybird_webhook_token` via `pydantic-settings`; the empty-string default pattern (per `klai/lang/python.md`) is used today.
- A-5: `klai-connector` already has `_portal_secret` loaded from config; the value is a sufficiently long secret for HMAC comparison.

## Out of Scope

- Replacing the existing `Depends(get_current_user)` pattern on focus/scribe routes. Route-level auth remains the primary check for user-object injection; middleware is a safety net, not a replacement. Portal-api already uses a route-dependency pattern (`_get_caller_org`) and is explicitly out of scope for F-005.
- Rewriting or refactoring the klai-connector `AuthMiddleware` beyond the `hmac.compare_digest` line. The broader introspection/cache path is already audited and acceptable.
- Changes to Zitadel configuration, audience registration, or introspection endpoints (covered by SPEC-SEC-012).
- Adding per-tenant rate limits, IP allowlists, or WAF rules around the Moneybird webhook beyond 401 + IP logging.
- Adding middleware to klai-mailer, klai-crawler, or knowledge-ingest (not part of this audit scope).

## Security Findings Addressed

This SPEC closes three Phase 3 audit findings. See `.moai/audit/99-fix-roadmap.md` §SEC-004 and `.moai/audit/04-tenant-isolation.md` for full finding detail.

- **F-005** (`04-tenant-isolation.md`) — focus-api and scribe-api have no auth middleware; only route dependencies protect endpoints. A future route added without `Depends(get_current_user)` would be unauthenticated. Severity: MEDIUM. Route dependencies are the primary control; middleware is the defense-in-depth safety net this SPEC adds.
- **F-006** (`04-tenant-isolation.md`) — Moneybird webhook token check in `klai-portal/backend/app/api/webhooks.py:22-28` is fail-open when `settings.moneybird_webhook_token` is empty (the `if` guard is skipped entirely), uses a non-constant-time `!=` comparison, and returns `200` on mismatch instead of `401`. Severity: MEDIUM.
- **F-009** (`04-tenant-isolation.md`) — klai-connector compares `token == self._portal_secret` at `app/middleware/auth.py:75`, which is non-constant-time. Severity was LOW while the connector was internal-only; elevated to HIGH because **F-017** (`04-3-prework-caddy.md`) shows klai-connector is publicly exposed via Caddy. A timing attack against `_portal_secret` is now remotely exploitable.

## Requirements

### REQ-1: AuthMiddleware on focus-api and scribe-api (F-005)

- **REQ-1.1** — A new `AuthMiddleware` class SHALL be added to each of:
  - `klai-focus/research-api/app/middleware/auth.py`
  - `klai-scribe/scribe-api/app/middleware/auth.py`
  Each middleware SHALL follow the reference implementation in `klai-connector/app/middleware/auth.py` (Bearer token parse → skip `/health` → Zitadel introspection → cache-by-SHA256 → bind `request.state.org_id`).

- **REQ-1.2** — Each service's `app/main.py` SHALL register `AuthMiddleware` **before** `RequestContextMiddleware` so that the authenticated `org_id` is available when the request-context logger binds contextvars.

- **REQ-1.3** — `AuthMiddleware` SHALL skip authentication only for `request.url.path == "/health"`. All other paths (including `/docs`, `/openapi.json` in production builds) SHALL require a valid Bearer token.

- **REQ-1.4** — Existing `Depends(get_current_user)` usages in routes SHALL remain unchanged. Middleware-level authentication does not replace route-level user-object injection; both layers run.

- **REQ-1.5** — A failed introspection or missing/invalid Bearer token SHALL return HTTP `401` with JSON body `{"error": "unauthorized"}`.

### REQ-2: Moneybird webhook hardening (F-006)

- **REQ-2.1** — At portal-api startup, if `settings.moneybird_webhook_token` is empty or unset, the process SHALL fail to start with a clear error message. Fail-closed replaces the current fail-open behavior.

- **REQ-2.2** — The token comparison in `klai-portal/backend/app/api/webhooks.py` SHALL use `hmac.compare_digest(token.encode(), settings.moneybird_webhook_token.encode())` instead of `token != settings.moneybird_webhook_token`.

- **REQ-2.3** — On token mismatch, the handler SHALL return HTTP `401` (currently returns `200`).

- **REQ-2.4** — On token mismatch or missing token, the handler SHALL log a WARNING including the source IP via `request.client.host` (via structlog, per `klai/projects/portal-logging-py.md`):
  ```
  logger.warning("Moneybird webhook: invalid token", source_ip=request.client.host)
  ```

### REQ-3: klai-connector `portal_caller_secret` constant-time compare (F-009)

- **REQ-3.1** — Line 75 of `klai-connector/app/middleware/auth.py` SHALL replace `token == self._portal_secret` with `hmac.compare_digest(token.encode(), self._portal_secret.encode())`.

- **REQ-3.2** — The surrounding guard `if self._portal_secret and ...` SHALL remain: when `_portal_secret` is unset, the bypass path SHALL NOT be taken, regardless of the supplied token (no fail-open).

- **REQ-3.3** — Behavior of valid portal service-to-service calls SHALL be unchanged: `request.state.from_portal = True` and `request.state.org_id = None` continue to be set.
