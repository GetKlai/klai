# SPEC-SEC-004 — Acceptance Criteria

EARS format. Each criterion is independently testable.

## Defense-in-depth middleware (F-005)

- **WHILE** `klai-focus/research-api` AND `klai-scribe/scribe-api` receive any request **IF** the path is not `/health` **THE** `AuthMiddleware` **SHALL** verify a valid Zitadel JWT before the handler runs.

- **WHEN** a new route is registered in either service WITHOUT `Depends(get_current_user)` **AND** the middleware is in place **THE** service **SHALL** still return `401` for requests to that route with no or invalid Bearer token — verified by an integration test that registers a "trap" route and asserts `401`.

- **WHEN** `AuthMiddleware` and `RequestContextMiddleware` are both registered **THE** middleware stack order **SHALL** ensure `AuthMiddleware` runs first so that `request.state.org_id` is available when `RequestContextMiddleware` binds logging contextvars.

- **WHEN** a request to `/health` is received **THE** `AuthMiddleware` **SHALL** pass it through without requiring a Bearer token.

## Moneybird webhook hardening (F-006)

- **WHEN** portal-api starts up **IF** `MONEYBIRD_WEBHOOK_TOKEN` is empty or unset **THE** process **SHALL** fail to start (fail-closed) and log a clear error.

- **WHEN** `klai-portal/backend` receives a Moneybird webhook **IF** the supplied token does not match the configured token **THE** endpoint **SHALL** return `401` AND log the source IP at WARNING level with key `source_ip`.

- **WHEN** the webhook token comparison runs **THE** comparison **SHALL** use `hmac.compare_digest` (constant-time), verified by a unit test that inspects the code path or asserts timing invariance on large token sets.

- **WHEN** a valid Moneybird webhook is received **THE** existing event-handling logic **SHALL** remain unchanged (subscription creation, mandate updates, etc.).

## Connector portal-secret constant-time (F-009)

- **WHEN** `klai-connector` compares a Bearer token against `_portal_secret` **THE** comparison **SHALL** use `hmac.compare_digest` (constant-time).

- **WHEN** `_portal_secret` is empty or unset **THE** middleware **SHALL NOT** take the portal-bypass path regardless of the supplied token (no fail-open).

- **WHEN** a valid portal service-to-service call succeeds **THE** middleware **SHALL** set `request.state.from_portal = True` and `request.state.org_id = None` exactly as before.

## Additional test coverage

- A regression test asserts that `request.state.from_portal` is not set when `_portal_secret` is empty, even if the caller sends `Bearer <empty-string>` or `Bearer null`.
- A unit test on the Moneybird webhook exercises all three failure modes (empty token at startup, wrong token, missing `webhook_token` in payload) and asserts `401` with IP logged.
- A Playwright or integration test adds a temporary route `/_trap` to focus-api and scribe-api (wired only in the test fixture) and asserts that an unauthenticated request returns `401` from middleware — not a Starlette `200` or `500`.
- A constant-time comparison test confirms that both `webhooks.py` and `klai-connector/app/middleware/auth.py` import `hmac` and call `hmac.compare_digest` at the comparison site (AST-based or grep-based check).
