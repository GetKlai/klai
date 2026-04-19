# SPEC-SEC-004 — Research Notes

## F-005 — focus/scribe lack auth middleware

Source: `.moai/audit/04-tenant-isolation.md` §F-005.

Both `klai-focus/research-api/app/main.py` and `klai-scribe/scribe-api/app/main.py` register only `CORSMiddleware` and `RequestContextMiddleware`. All authentication is route-level via `Depends(get_current_user)` FastAPI dependencies. This is not a live vulnerability — every current route uses the dependency — but it is a latent one: a developer adding a new route who forgets the dependency creates an unauthenticated endpoint.

Route dependencies remain the **primary** control. They inject the full user object (org_id, roles) that handlers need, and keep per-endpoint auth explicit and auditable. Middleware is strictly a **safety net**: it ensures that even a forgotten `Depends` cannot expose a route, and that `request.state.org_id` is populated uniformly for logging/tracing.

## F-006 — Moneybird webhook fail-open + non-constant-time

Source: `.moai/audit/04-tenant-isolation.md` §F-006. Code: `klai-portal/backend/app/api/webhooks.py:22-28`.

Three independent issues in one block:

1. **Fail-open when unset.** The guard `if settings.moneybird_webhook_token:` means an empty or unset token disables the token check entirely — the webhook accepts any payload. An ops mistake (rotating the secret, re-deploying without the env var) silently opens the endpoint.
2. **Non-constant-time compare.** `token != settings.moneybird_webhook_token` is a Python `!=` string comparison. A remote attacker can, in principle, mount a timing attack to recover the token byte-by-byte.
3. **Wrong status on mismatch.** Returns `200` on mismatch — logs a warning but tells Moneybird (or an attacker) that the request was accepted. This masks misconfigurations and removes the signal that a 4xx would give to monitoring.

Fix is to fail-closed at startup (raise when unset), compare with `hmac.compare_digest`, return `401` on mismatch, and log the source IP so abuse is traceable via VictoriaLogs (`service:portal-api AND level:warning AND source_ip:*`).

## F-009 — connector `portal_caller_secret` non-constant-time

Source: `.moai/audit/04-tenant-isolation.md` §F-009. Code: `klai-connector/app/middleware/auth.py:75`.

The connector's `AuthMiddleware` has a bypass branch for portal-originated service-to-service calls:

```python
if self._portal_secret and token == self._portal_secret:
    request.state.from_portal = True
    ...
```

The `token == self._portal_secret` compare is not constant-time. Originally rated LOW because the connector was assumed internal-only.

**F-017** (`.moai/audit/04-3-prework-caddy.md`) changed that assumption: Caddy proxies `klai-connector` on a public hostname so that browser OAuth redirects for Notion / Google Drive / Microsoft can reach the connector callback. Any remote attacker can now attempt a timing attack against `_portal_secret`. This elevates F-009 to HIGH severity within the SEC-004 bundle.

The one-line fix is to swap `==` for `hmac.compare_digest(token.encode(), self._portal_secret.encode())`. The surrounding `if self._portal_secret and ...` guard stays — it prevents the bypass path from being taken when the secret is unset (fail-closed).

## Reference implementation: klai-connector AuthMiddleware

The connector's `AuthMiddleware` at `klai-connector/app/middleware/auth.py` is the template for focus/scribe middleware. It already handles:

- Bearer parse and 401 on missing/malformed header.
- `/health` skip.
- Portal-secret bypass branch (focus/scribe do not need this branch today).
- Zitadel introspection with `audience` validation.
- Token-hash cache (`_cache_get`/`_cache_put`) to avoid round-tripping introspection on every request.
- `request.state.org_id` binding from the `urn:zitadel:iam:user:resourceowner:id` claim.

The only bit that needs service-specific parameterization is the Zitadel audience — which SPEC-SEC-012 puts in place as `ZITADEL_API_AUDIENCE` per service.

## Middleware ordering

Starlette runs middlewares in LIFO order of registration (the middleware registered **last** is the **outermost** wrapper and runs **first**). `AuthMiddleware` must run before `RequestContextMiddleware` so that `request.state.org_id` is set when contextvars bind for logging. Practically: register `RequestContextMiddleware` first (via `add_middleware`), then register `AuthMiddleware` second — or use the equivalent explicit ordering in each service's `app/main.py`. Verify with an integration test that inspects log contextvars on a 401 vs a 200 response.

## Related existing patterns

- `klai-portal/backend` already uses a route-dependency pattern (`_get_caller_org` / `_get_current_user`) and a split RLS model for tenant isolation. Portal is explicitly out of scope for the middleware addition — its dependency pattern is considered sufficient and is tracked under SPEC-SEC-010/003.
- The constant-time compare pattern is standard: `hmac.compare_digest(a.encode(), b.encode())` on bytes. Both sides must be the same type.
- Source-IP logging uses `request.client.host` (FastAPI `Request`). When the service is behind Caddy, `X-Forwarded-For` forwarding must be trusted — this is already the case in the Caddy config per `.moai/audit/04-3-prework-caddy.md` and the existing trace-correlation setup in `.claude/rules/klai/infra/observability.md`.
