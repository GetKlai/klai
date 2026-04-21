# SPEC-SEC-004 — Implementation Plan

Order matches the dependency graph: focus/scribe first (new files, no behavioral change for existing routes), then portal webhook, then one-line connector fix.

## 1. focus-api — new AuthMiddleware

**File (new):** `klai-focus/research-api/app/middleware/auth.py`

- Port the pattern from `klai-connector/app/middleware/auth.py` verbatim:
  - Parse `Authorization: Bearer <token>`; on missing/malformed → `JSONResponse({"error": "unauthorized"}, status_code=401)`.
  - Skip when `request.url.path == "/health"`.
  - SHA256-hash the token as cache key.
  - Call Zitadel introspection (`_introspect`) with `ZITADEL_API_AUDIENCE` for focus.
  - On success: bind `request.state.org_id = str(zitadel_org_id)` and call `call_next`.
- Do **not** copy the `_portal_secret` bypass branch (focus has no portal-to-focus call pattern today; if one is added later, add it explicitly via a separate SPEC).

**File (modified):** `klai-focus/research-api/app/main.py`

- Import the new `AuthMiddleware`.
- Register it **before** `RequestContextMiddleware`. Concretely, in Starlette middleware ordering the outer `add_middleware` call runs first; place `AuthMiddleware` last in code (innermost outer wrapper) so it executes before `RequestContextMiddleware` per Starlette LIFO semantics. Verify ordering with an integration test.
- Leave `CORSMiddleware` registration unchanged.

Keep existing `Depends(get_current_user)` on routes — still required for user-object injection.

## 2. scribe-api — new AuthMiddleware (mirror of focus)

**File (new):** `klai-scribe/scribe-api/app/middleware/auth.py`

- Same pattern as step 1. Configure audience via scribe-specific env (`ZITADEL_API_AUDIENCE` for scribe per SPEC-SEC-012).

**File (modified):** `klai-scribe/scribe-api/app/main.py`

- Register `AuthMiddleware` before `RequestContextMiddleware`. Same ordering rationale as focus.

## 3. portal webhook hardening

**File (modified):** `klai-portal/backend/app/core/config.py` (or equivalent startup validation)

- Add a post-init validator or startup check: if `settings.moneybird_webhook_token` is empty or unset, raise at process start (fail-closed). Follow the existing fail-closed precedent for other mandatory secrets in this file.

**File (modified):** `klai-portal/backend/app/api/webhooks.py`

Current (lines 22-28):
- `if settings.moneybird_webhook_token:` → remove guard (now guaranteed non-empty by startup check).
- `if token != settings.moneybird_webhook_token:` → replace with `if not hmac.compare_digest(token.encode(), settings.moneybird_webhook_token.encode()):`.
- `return Response(status_code=200)` on mismatch → replace with `return Response(status_code=401)`.
- Add `logger.warning("Moneybird webhook: invalid token", source_ip=request.client.host)` via structlog (the handler already logs via the module logger; switch to structlog per `klai/projects/portal-logging-py.md` if not already).
- Add `import hmac` at the top of the module.

The `_get_caller_org` / route-dependency pattern mentioned in the SPEC Out-of-Scope remains untouched; only the Moneybird path changes.

## 4. klai-connector — one-line constant-time fix

**File (modified):** `klai-connector/app/middleware/auth.py`

Line 75:
- Before: `if self._portal_secret and token == self._portal_secret:`
- After: `if self._portal_secret and hmac.compare_digest(token.encode(), self._portal_secret.encode()):`

Add `import hmac` if not already imported. Leave the surrounding guard (`self._portal_secret and ...`) intact to preserve fail-closed behavior when the secret is unset.

## 5. Tests per service

- **focus-api / scribe-api** — add `tests/test_auth_middleware.py` per service:
  - `/health` returns 200 without auth.
  - Missing `Authorization` header returns 401 from middleware.
  - Malformed `Authorization` header (`Basic ...`, `token xyz`) returns 401.
  - Valid Zitadel token binds `request.state.org_id` and reaches the handler (mock `_introspect`).
  - Trap route registered in test fixture without `Depends(get_current_user)` still returns 401 when unauthenticated — proves the safety-net property.

- **portal-api** — extend `tests/test_webhooks.py` (or add `test_moneybird_webhook_auth.py`):
  - Empty `moneybird_webhook_token` at startup raises on app load.
  - Wrong token returns 401 and emits a WARNING log containing `source_ip`.
  - Correct token continues to process the event exactly as today.
  - AST/grep assertion: `hmac.compare_digest` is called in `webhooks.py`.

- **klai-connector** — extend `tests/test_auth_middleware.py`:
  - `_portal_secret` set, matching token → bypass path taken.
  - `_portal_secret` set, non-matching token → falls through to introspection path.
  - `_portal_secret` empty → bypass path never taken regardless of token.
  - AST/grep assertion: `hmac.compare_digest` is called on the portal-secret comparison.

## 6. Deployment order

1. Ensure SPEC-SEC-012 env vars (`ZITADEL_API_AUDIENCE` for focus and scribe) are deployed first.
2. Deploy focus-api and scribe-api middleware additions (no-op for existing routes, safety net for future ones).
3. Deploy klai-connector one-line fix (zero behavior change for correct secrets).
4. Deploy portal-api webhook change last — requires `MONEYBIRD_WEBHOOK_TOKEN` to be set in prod env before rollout (fail-closed).
