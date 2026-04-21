---
id: SPEC-AUTH-008
version: "1.0.0"
status: draft
created: "2026-04-19"
updated: "2026-04-19"
author: MoAI
priority: P1
supersedes: null
related: [SPEC-AUTH-006, SPEC-AUTH-007]
---

## HISTORY

| Date       | Version | Change                                                           |
|------------|---------|------------------------------------------------------------------|
| 2026-04-19 | 1.0.0   | Initial SPEC — BFF migration for portal auth                     |

# SPEC-AUTH-008: Backend-for-Frontend (BFF) migration for portal auth

## Context

The portal currently uses the "public client + tokens in `localStorage`" OAuth SPA pattern with `oidc-client-ts` v3 and `react-oidc-context`. SPEC-AUTH-007 documents the architectural pressure on that model: XSS token exfiltration, silent-renew fragility, unbounded `redirectUris` list, Chrome third-party cookie deprecation, per-origin token storage fragmenting state between `my.getklai.com` and `{tenant}.getklai.com`.

The post-SPEC-AUTH-006 fixes (`2d7abc2a`, `b41fe888`, `0caed060`) hardened the current pattern's correctness: typed errors, exponential backoff in provisioning, robust silent-renew error classification, Sentry fingerprinting. Those changes are correct for today and do not need to be reverted.

This SPEC migrates the portal off the public-SPA pattern to the **Backend-for-Frontend (BFF) pattern** recommended by OAuth 2.1 BCP (draft-ietf-oauth-browser-based-apps) and OWASP ASVS 5.0 for browser applications:

- Tokens live server-side (portal-api) in a Redis-backed session store — never in browser JS.
- The browser holds one `__Host-klai_session` cookie: `HttpOnly; Secure; SameSite=Lax; Domain=.getklai.com`. The cookie works across all tenant subdomains without per-origin re-authentication.
- portal-api attaches the session's access_token to downstream calls (knowledge-ingest, retrieval-api, research-api, scribe, connector). The frontend makes same-origin credentialed fetches that the cookie authorises.
- Silent renewal becomes a server-side refresh-token exchange. No iframe, no `prompt=none`, no cross-tab `localStorage` dance.
- The Zitadel portal app becomes a confidential client with a single `redirectUris: ["https://my.getklai.com/api/auth/oidc/callback"]`.

The migration is feasible as a hard cutover because the product has no paying tenants today (confirmed 2026-04-19). Adding a feature-flag dual-stack is deferred as a contingency.

---

## Scope

| Layer              | Changes                                                                                                    |
|--------------------|------------------------------------------------------------------------------------------------------------|
| DB / Store         | New Redis keyspace `klai:session:<sid>` for server-side session records. No Postgres schema change.         |
| Backend (portal-api) | New `SessionService` (create, load, renew, revoke); new `/api/auth/oidc/start`, `/api/auth/oidc/callback`, `/api/auth/refresh`, `/api/auth/logout`; `SessionMiddleware` replacing `HTTPBearer` dependency; `get_trace_headers` + downstream clients use session's access_token. |
| Backend (downstream) | No change. knowledge-ingest, retrieval-api, scribe, research-api, connector still validate Zitadel access_tokens as today; portal-api forwards the same bearer. |
| Frontend           | Drop `react-oidc-context` and `oidc-client-ts` dependencies. New `SessionContext` built on `/api/auth/session`. `apiFetch` switches to `credentials: 'include'`, drops `Authorization` header. New login page calls `/api/auth/oidc/start`. `/callback` becomes a thin marker page. Tenant hand-off unchanged (subdomain cookie is shared). |
| Zitadel            | Portal OIDC app type: `WEB` (confidential, client_secret) instead of `SPA`. `redirectUris` collapses to one entry: `https://my.getklai.com/api/auth/oidc/callback`. `postLogoutRedirectUris`: `https://my.getklai.com/logged-out`. `authMethodType: POST`. Refresh token lifetime: 30 days with rotation. |
| Caddy              | Per-tenant subdomain blocks: explicit `header_up Cookie` on `/api/*` upstream to portal-api (already default, verify). No CORS changes needed — same origin under `.getklai.com` suffix rules. |
| Tenant provisioning | `add_portal_redirect_uri` is deprecated — provisioning no longer mutates the Zitadel app. Existing call site in `klai-portal/backend/app/services/provisioning/orchestrator.py` removed. |
| Security           | CSRF protection via `__Host-klai_csrf` cookie (readable by JS) + matching `X-CSRF-Token` header on state-changing requests. Only `SameSite=Lax` on the session cookie is insufficient against top-level GET redirects with side effects, but portal-api has none — session endpoints are all POST. Double-submit token documented as defence-in-depth. |
| Observability      | Session lifecycle events emit to `product_events`: `auth.login`, `auth.logout`, `auth.refresh_failed`, `auth.session_invalid`. Sentry tags `{domain:auth, phase:bff-<operation>}`. |

## Out of Scope

- LibreChat per-tenant OIDC apps (`librechat-{slug}`). They remain separate confidential OIDC apps and authenticate against Zitadel directly; no session-cookie involvement.
- Partner API (`/partner/*`) — continues to use bearer-token auth for external integrations. Unaffected.
- Widget bundle `klai-chat.js` — uses its own per-widget auth token. Unaffected.
- Scribe, Research, Retrieval APIs — continue to validate Zitadel access_tokens server-to-server. Only the hop from the browser to portal-api changes.
- Social login (Google/Microsoft IDP intents) — handled inside the new `/api/auth/oidc/callback` flow; no change to the IDP intent mechanism itself.
- Multi-factor (TOTP) enforcement — no change to the policy; MFA status is still read from `/api/me`.

---

## Requirements

### R1: Redis-backed session store

**WHEN** the portal creates a new authenticated session for a user,
**THEN** `SessionService` SHALL persist the session in Redis under key `klai:session:<sid>` with a TTL equal to the refresh-token lifetime.

Session record schema (JSON, encrypted at rest via existing Fernet key):

```
{
  "sid": "<opaque 32-byte random, URL-safe base64>",
  "zitadel_user_id": "...",
  "org_id": 42,
  "access_token": "<Zitadel AT>",
  "refresh_token": "<Zitadel RT>",
  "access_token_expires_at": <unix_seconds>,
  "id_token": "<Zitadel IDT, for post-logout hint>",
  "created_at": <unix_seconds>,
  "last_seen_at": <unix_seconds>,
  "user_agent_hash": "<sha256 of UA at creation>",
  "ip_hash": "<sha256 of creation IP, truncated /24>"
}
```

**Constraints:**
- C1.1: `sid` is 256 bits of CSPRNG randomness, base64url-encoded (43 chars). Never derived from user data.
- C1.2: Redis TTL matches `refresh_token_expires_in` from Zitadel (typically 30 days). On access-token refresh the TTL is bumped.
- C1.3: The session record is encrypted with the existing Fernet key (`FERNET_KEY` env) before `SET`. Decrypted after `GET`.
- C1.4: `user_agent_hash` and `ip_hash` are advisory (detect session theft by dramatic change); not enforced blockers.
- C1.5: Redis connection uses the existing portal-api Redis pool (`REDIS_URL`). Keyspace is shared with rate-limit and SSO-pending caches — naming prefix prevents collision.
- C1.6: On revocation (logout, admin force-logout), delete the Redis key AND call Zitadel's `/oauth/v2/revoke` for both access and refresh tokens.

### R2: OIDC authorisation-code flow initiation

**WHEN** an unauthenticated browser hits `/` on `my.getklai.com` and clicks Login,
**THEN** the frontend SHALL navigate to `GET /api/auth/oidc/start?return_to=<path>` which:

1. Generates a `state` (CSPRNG 32 bytes, base64url) and a PKCE `code_verifier` (RFC 7636).
2. Stores `{state, code_verifier, return_to, created_at, user_agent_hash}` in Redis under `klai:oidc_pending:<state>` with 10-minute TTL.
3. Computes `code_challenge = SHA256(code_verifier)` and builds the Zitadel authorisation URL.
4. Responds with HTTP 302 to `https://auth.getklai.com/oauth/v2/authorize?...`.

**Constraints:**
- C2.1: `return_to` is validated against an allowlist of internal paths (must start with `/`, no protocol, no `//`). Invalid → ignored, default `/app`.
- C2.2: PKCE `code_challenge_method = S256` (Zitadel requires it).
- C2.3: Scopes: `openid profile email offline_access urn:zitadel:iam:user:metadata`.
- C2.4: The response clears any existing `__Host-klai_session` cookie on the domain to prevent session-fixation.

### R3: OIDC callback — code-for-token exchange + session creation

**WHEN** Zitadel redirects back to `GET /api/auth/oidc/callback?code=...&state=...`,
**THEN** portal-api SHALL:

1. Load `klai:oidc_pending:<state>` from Redis. If missing or expired → 400 `invalid_state`.
2. Validate the `user_agent_hash` matches the current request's UA hash (loose — just-log-if-mismatch, do not block; Safari rewrites UA).
3. Exchange the code at Zitadel's `/oauth/v2/token` endpoint using the stored `code_verifier`, the portal client_id, and `client_secret` from env. Include `grant_type=authorization_code`.
4. Validate the returned `id_token` signature against Zitadel's JWKS (`jwt_validator` util). Extract `sub`.
5. Fetch user info via Zitadel's userinfo endpoint and locate the `portal_users` row.
6. Create a new Redis session per R1 (`sid`, tokens, metadata).
7. Set cookies on the response (see R4).
8. Delete `klai:oidc_pending:<state>`.
9. Emit `auth.login` product event.
10. HTTP 302 to the stored `return_to` (or `/app`).

**Constraints:**
- C3.1: All failure modes return HTTP 302 to `https://my.getklai.com/logged-out?reason=<code>` with a structured reason code (`invalid_state`, `token_exchange_failed`, `id_token_invalid`, `no_portal_user`, `server_error`). Frontend renders a matching friendly message.
- C3.2: The callback endpoint lives at `/api/auth/oidc/callback` on `my.getklai.com` only. It is NOT registered for tenant subdomains — tenant subdomains inherit the parent-domain session cookie.
- C3.3: IDP-intent (Google/Microsoft social login) callbacks land on the same `/api/auth/oidc/callback` path because they complete through a Zitadel session, producing a normal `code`.
- C3.4: If the authenticated Zitadel user has no `portal_users` row AND no domain-allowlist match (see SPEC-AUTH-006 R4), redirect to `/no-account` with the session cookie unset — user is authenticated at Zitadel but not a Klai member.

### R4: Session + CSRF cookies

**WHEN** the backend creates or renews a session,
**THEN** it SHALL set two cookies on the HTTP response:

| Cookie name            | Attributes                                                                                      | Value                |
|------------------------|-------------------------------------------------------------------------------------------------|----------------------|
| `__Host-klai_session`  | `HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=<rt_ttl>` (no Domain — `__Host-` is origin-bound) | The `sid` (opaque)   |
| `klai_csrf`            | `Secure; SameSite=Lax; Path=/; Domain=.getklai.com; Max-Age=<rt_ttl>` (readable by JS)           | CSPRNG 32 bytes b64url |

Wait — `__Host-` prefix forbids `Domain`, which would scope the cookie to only `my.getklai.com`. That breaks tenant subdomain access. Resolution:

**Chosen design:** use `__Secure-klai_session` (not `__Host-`) with `Domain=.getklai.com` so the cookie is shared across all subdomains.

Revised table:

| Cookie name              | Attributes                                                                    | Scope                    | Reason                                |
|--------------------------|-------------------------------------------------------------------------------|--------------------------|---------------------------------------|
| `__Secure-klai_session`  | `HttpOnly; Secure; SameSite=Lax; Domain=.getklai.com; Path=/; Max-Age=<ttl>`  | all getklai.com subdomains | Shared session across tenant subdomains |
| `__Secure-klai_csrf`     | `Secure; SameSite=Lax; Domain=.getklai.com; Path=/; Max-Age=<ttl>`            | all getklai.com subdomains | JS reads; frontend mirrors in header   |

**Constraints:**
- C4.1: Both cookies use `__Secure-` prefix (requires `Secure`). `__Host-` is rejected because it forbids `Domain`.
- C4.2: `SameSite=Lax` allows top-level navigation cross-site (needed for the Zitadel → `/api/auth/oidc/callback` redirect to carry the cookie if any pre-existed). `SameSite=Strict` would break the callback.
- C4.3: Both cookies are cleared on logout by setting `Max-Age=0` with the same attributes (Domain + Path must match).
- C4.4: The `klai_csrf` value is NOT the `sid`. Separate 256-bit random, generated at session creation and persisted in the session record for verification.
- C4.5: Cookie names are constants in `app.core.config` — never re-typed in handler code.

### R5: SessionMiddleware

**WHEN** an HTTP request arrives at portal-api on any `/api/*` path,
**THEN** the middleware SHALL resolve the session before the route handler runs:

1. Read `__Secure-klai_session` cookie.
2. If missing → set `request.state.session = None`, continue. Routes that require auth reject downstream.
3. If present → load `klai:session:<sid>` from Redis.
4. If not found → clear cookie, set `request.state.session = None`, continue.
5. If access_token expired → attempt refresh (R7). On success, rewrite the session in Redis, continue. On failure, clear cookie, continue.
6. Bind structlog contextvars: `org_id`, `user_id`, `session_id` (for logs correlation).
7. Set `request.state.session = SessionContext(zitadel_user_id, org_id, access_token, ...)`.
8. For state-changing methods (POST, PUT, PATCH, DELETE): verify `X-CSRF-Token` header matches the session's stored `csrf` value. Reject with 403 `csrf_invalid` if missing or mismatched.

**Constraints:**
- C5.1: CSRF check applies ONLY when a session cookie is present. Endpoints intentionally unauthenticated (e.g. `/api/auth/oidc/start`, `/api/public/*`) are exempt via a route-level `@csrf_exempt` decorator.
- C5.2: A dependency `get_session(request: Request) -> SessionContext` replaces `HTTPBearer` everywhere in routes. Routes that call it get 401 `no_session` if `request.state.session is None`.
- C5.3: The middleware MUST run BEFORE `LoggingContextMiddleware` so `org_id` / `user_id` land in every log entry.
- C5.4: Bearer-token auth remains for `/partner/*` and internal service-to-service; the SessionMiddleware is wired only to the `/api/*` router.

### R6: /api/me and downstream endpoints read the session

**WHEN** a route requires the current user's identity,
**THEN** it SHALL use the `get_session` dependency instead of `HTTPBearer`.

Every existing endpoint under `/api/` that uses `Depends(bearer)` migrates. Impact footprint (auditable via grep):

```
grep -rln 'HTTPBearer\|Depends(bearer)' klai-portal/backend/app/api/
```

**Constraints:**
- C6.1: `get_session` returns a `SessionContext` dataclass with `zitadel_user_id`, `org_id`, `access_token`, `csrf_token`. Handlers use those fields directly; no raw cookie access.
- C6.2: Downstream HTTPX clients (`knowledge-ingest`, `retrieval-api`, `scribe`, `research-api`, `connector`, `klai-mailer`) receive `session.access_token` as their `Authorization: Bearer <…>` header. No schema change for those services.
- C6.3: `get_trace_headers()` is unchanged — it already propagates `X-Request-ID` and `X-Org-ID` from the middleware.

### R7: Server-side refresh

**WHEN** `SessionMiddleware` detects an expired access_token,
**THEN** it SHALL attempt a refresh:

1. POST to Zitadel `/oauth/v2/token` with `grant_type=refresh_token`, the stored `refresh_token`, client_id, client_secret.
2. On success: update session record with new access_token, expires_at, and (if rotated) refresh_token. Extend Redis TTL.
3. On failure (`invalid_grant`, network, etc.): delete the Redis record, return to the caller with `session = None`.

**Constraints:**
- C7.1: Refresh attempts are coalesced per `sid`: concurrent requests hitting an expired token all wait on one in-flight refresh (Redis advisory lock with 5s timeout). Prevents a thundering herd on Zitadel.
- C7.2: Refresh failures are logged at `warning` and emit an `auth.refresh_failed` product event. Not captured to Sentry (routine, high-volume).
- C7.3: A 5-minute clock-skew tolerance: the middleware refreshes proactively if `now() > expires_at - 60s`.
- C7.4: After refresh, the response still carries the same `__Secure-klai_session` cookie — only the server-side record changes.

### R8: Frontend — remove react-oidc-context

**WHEN** the migration is complete,
**THEN** the frontend SHALL have zero references to `react-oidc-context` or `oidc-client-ts`.

Affected files (inventoried pre-implementation):
- `src/lib/auth.tsx` — rewrite as a thin session-aware context.
- `src/lib/apiFetch.ts` — drop `Authorization` header, add `credentials: 'include'` + CSRF header for mutations.
- 60+ route files that currently call `useAuth()` for the access_token — migrate to a new `useSession()` hook that returns `{ user, csrfToken, refetch, signOut }`.
- `src/routes/callback.tsx` — becomes a thin "just wait for backend redirect" page (the backend does the heavy lifting now).
- `src/routes/provisioning.tsx` — unchanged logically, but fetches `/api/me` via same-origin cookie auth.
- `src/routes/index.tsx` — calls `window.location.href = '/api/auth/oidc/start?return_to=...'` instead of `auth.signinRedirect()`.
- `src/routes/logged-out.tsx` — unchanged UX; auth state now comes from the session hook.

**Constraints:**
- C8.1: The new `SessionContext` loads `/api/auth/session` on mount (single HTTP call returning `{user, csrf}` or 401). No localStorage. No silent-renew handling in the client.
- C8.2: `apiFetch` automatically includes CSRF header: `headers['X-CSRF-Token'] = readCookie('__Secure-klai_csrf')`. A helper `useAuthenticatedFetch()` exposes the same API.
- C8.3: `package.json` loses `react-oidc-context` and `oidc-client-ts` deps. Bundle size check: expect ~40KB gzip reduction.

### R9: Frontend — SessionContext API

**WHEN** a component needs the current user,
**THEN** it SHALL call `useSession()` which returns:

```ts
interface SessionValue {
  status: 'loading' | 'authenticated' | 'unauthenticated'
  user: MeResponse | null    // same shape as today's /api/me response
  csrfToken: string | null
  refetch: () => Promise<void>
  signOut: () => Promise<void>
}
```

**Constraints:**
- C9.1: The context fetches `/api/auth/session` on mount and invalidates on `401` responses from any `apiFetch` call (global listener on `apiFetch`).
- C9.2: `signOut()` POSTs to `/api/auth/logout`, then calls `window.location.href = '/logged-out'` on success.
- C9.3: `useCurrentUser` becomes a thin wrapper over `useSession()` for backward compat — callers migrate over time.
- C9.4: No more `auth.user?.access_token` — tokens never touch the client.

### R10: Zitadel app reconfiguration

**WHEN** this SPEC is deployed,
**THEN** the portal OIDC app in Zitadel SHALL be reconfigured by a one-shot migration script `klai-infra/scripts/zitadel-bff-migration.py`:

1. Update app type: `APP_TYPE_WEB` (confidential).
2. Set `authMethodType: OIDC_AUTH_METHOD_TYPE_POST`.
3. Collapse `redirectUris` to one entry: `https://my.getklai.com/api/auth/oidc/callback`.
4. Set `postLogoutRedirectUris` to `https://my.getklai.com/logged-out`.
5. Rotate the client secret and emit to SOPS (`ZITADEL_PORTAL_CLIENT_SECRET`).
6. Set `accessTokenType: OIDC_TOKEN_TYPE_JWT` (unchanged).
7. Set `refreshTokenLifetime: 30 days` (rotation enabled).

**Constraints:**
- C10.1: The script is idempotent — re-running is a no-op.
- C10.2: The old tenant-specific `redirectUris` entries (added per-tenant by `add_portal_redirect_uri`) are removed. `add_portal_redirect_uri` is deleted from `services/zitadel.py`; the one call in `provisioning/orchestrator.py` is removed.
- C10.3: After migration, the per-tenant LibreChat OIDC apps (`librechat-{slug}`) are untouched.
- C10.4: The new client secret is delivered via SOPS: `klai-infra/config.sops.env` gets `ZITADEL_PORTAL_CLIENT_SECRET=...` and portal-api reads it via `settings.zitadel_portal_client_secret`.

### R11: Caddy forwards cookies to portal-api

**WHEN** a request arrives on any `*.getklai.com` subdomain to `/api/*`,
**THEN** Caddy SHALL forward the `Cookie` header unchanged to `portal-api:8010`.

This is the Caddy default (no header stripping). Verification: Caddy's `reverse_proxy` preserves all incoming headers by default.

**Constraints:**
- C11.1: No Caddy config change expected. An integration test asserts the cookie round-trip works.
- C11.2: The existing `X-Request-ID` propagation (SPEC-INFRA-004) is unaffected.
- C11.3: `Vary: Cookie` is already the default on JSON responses; no additional CDN caching concerns.

### R12: Migration runbook

**WHEN** this SPEC is deployed,
**THEN** the operations runbook `klai-infra/runbooks/bff-migration.md` SHALL describe the hard-cutover sequence:

1. Merge SPEC-AUTH-008 implementation to `main`.
2. Deploy portal-api (backend changes, with BFF endpoints live and working ALONGSIDE legacy bearer auth — both work).
3. Deploy portal frontend (new `SessionContext`, cookie-based apiFetch).
4. Run `klai-infra/scripts/zitadel-bff-migration.py` with `--dry-run` to preview changes.
5. Run again without `--dry-run` during low-traffic window (requires all existing browser sessions to be re-established).
6. Flip feature flag `BFF_ENFORCE_COOKIES=true` on portal-api — legacy bearer paths start returning 401.
7. Monitor Sentry dashboards for 24h.
8. After 7 days stability, remove legacy bearer code paths (separate cleanup PR).

**Constraints:**
- C12.1: Step 2 must ship BEFORE step 3. Frontend that expects cookies against a backend that doesn't issue them is a self-DoS.
- C12.2: The feature flag default is `false` during soak. Only at step 6 it flips to `true`.
- C12.3: Active browser sessions at the time of cutover are invalidated — users must re-login once. This is acceptable (zero paying tenants, per 2026-04-19 confirmation). Announce via email 24h ahead.

### R13: Rollback

**WHEN** post-deploy metrics show critical auth failures,
**THEN** rollback SHALL be possible within 10 minutes:

1. Set `BFF_ENFORCE_COOKIES=false` on portal-api → legacy bearer paths work again.
2. Redeploy the previous portal frontend container (tag + SHA pinned in GitHub Actions artifact).
3. (Optional) Re-expand the Zitadel app's `redirectUris` to include tenant subdomains via the old `add_portal_redirect_uri` flow.

**Constraints:**
- C13.1: Rollback step 2 requires that the previous frontend bundle is still deployable. GitHub Actions retains build artifacts for 90 days.
- C13.2: The Zitadel client secret created in R10.5 remains valid after rollback — the old public-client app type is not re-enabled automatically. Document manual steps in the runbook.
- C13.3: Redis session records created during the BFF period are orphaned on rollback; they expire naturally within 30 days.

### R14: Backward compatibility during soak

**WHEN** `BFF_ENFORCE_COOKIES=false` (soak period),
**THEN** portal-api SHALL accept BOTH cookie and bearer authentication, preferring cookie when both are present.

**Constraints:**
- C14.1: `get_session` dependency checks `request.state.session` first; if absent AND `Authorization: Bearer` header is present, falls back to the legacy validator.
- C14.2: A log warning `legacy_bearer_auth_used` is emitted whenever the bearer fallback fires, with `user_id` and `path`. Tracks migration progress.
- C14.3: Once the legacy fallback count reaches zero for 48h, it is removed entirely in a follow-up commit.

---

## Architecture

### Current (per SPEC-AUTH-006 + post-fixes)

```
Browser (my.getklai.com)
  ├── localStorage: {access_token, refresh_token}
  ├── fetch /api/me with Authorization: Bearer <at>
  └── silent renew: oidc-client-ts iframe or refresh_token POST

Zitadel (auth.getklai.com)
  └── portal OIDC app (SPA, public, N redirectUris)

portal-api
  └── Depends(HTTPBearer) → validate access_token → act
```

### Target (BFF)

```
Browser (my.getklai.com OR {tenant}.getklai.com)
  ├── cookie __Secure-klai_session=<sid>   (Domain=.getklai.com, HttpOnly)
  ├── cookie __Secure-klai_csrf=<csrf>     (Domain=.getklai.com, readable)
  ├── fetch /api/me  (credentials: 'include', X-CSRF-Token: <csrf>)
  └── no tokens, no silent renew, no oidc-client-ts

portal-api
  ├── Redis: klai:session:<sid> → {access_token, refresh_token, ...}
  ├── SessionMiddleware: cookie → Redis → SessionContext on request.state
  ├── /api/auth/oidc/start      → PKCE + Zitadel authorize URL
  ├── /api/auth/oidc/callback   → code exchange → session → cookie → /app
  ├── /api/auth/refresh         → (usually middleware does it automatically)
  └── /api/auth/logout          → revoke + clear cookie

Zitadel (auth.getklai.com)
  └── portal OIDC app (WEB, confidential, ONE redirect_uri)
```

---

## Test Plan

### Unit (portal-api pytest)

- `test_session_service.py` — create, load, renew, revoke; fernet round-trip; TTL handling.
- `test_oidc_callback.py` — happy path, invalid state, token exchange failure, no portal_user, CSRF cookie set correctly.
- `test_session_middleware.py` — cookie missing, cookie valid, cookie invalid, access_token expired + refresh success, refresh failure, CSRF check pass/fail, exempt routes.
- `test_legacy_fallback.py` — bearer auth still works while `BFF_ENFORCE_COOKIES=false`; 401 when flag flipped.

### Unit (frontend vitest)

- `session-context.test.tsx` — mount loads /api/auth/session, handles 401, exposes signOut.
- `api-fetch.test.ts` — credentials:'include', CSRF header attached on POST, no Authorization header sent.
- Existing `fetch-errors.test.ts` + `oidc-error.test.ts` remain unchanged (lib is transport-agnostic).

### Integration (pytest + httpx + redis test container)

- `test_bff_integration.py` — end-to-end: mock Zitadel authorize → callback → session cookie set → subsequent request carries cookie → /api/me returns user.
- Cross-subdomain: cookie on `.getklai.com` honoured by request to `{tenant}.getklai.com/api/me`.

### E2E (Playwright, optional but recommended)

- `klai-portal/tests/e2e/bff-login.spec.ts` — Chrome + Brave (ad-block aware), full OIDC round trip, verify cookies, verify /api/me call, verify logout clears cookies.
- Run against a docker-compose stack with a Zitadel test tenant.

### Load / soak

- 1h load test at 10× current traffic against `/api/me` + CSRF-protected mutations. Verify Redis keyspace stays bounded (session TTLs firing correctly).
- Clock-skew simulation: freeze access_token expiry, verify coalesced refresh does exactly one Zitadel round-trip even under 100 concurrent requests.

---

## Acceptance Criteria

The SPEC is complete when all of the following hold:

1. All requirements R1–R14 implemented and documented.
2. 100% of portal-api routes under `/api/` use `get_session` — zero remaining `HTTPBearer` usages (verifiable via grep in CI).
3. Frontend bundle contains no `react-oidc-context` / `oidc-client-ts` imports (verifiable via `rollup-plugin-visualizer`).
4. All existing unit tests pass, plus new BFF-specific tests added in Test Plan.
5. Manual QA checklist in runbook executed and signed off.
6. Sentry dashboard for 7 days post-cutover shows:
   - Zero `auth.silent_renew_failed` events (endpoint removed).
   - Zero `legacy_bearer_auth_used` warnings.
   - `auth.login` / `auth.logout` rates match historical user sessions.
7. Bundle size reduction confirmed: frontend gzip drop ≥ 30KB.
8. Zitadel app type verified `WEB` (confidential) via Zitadel console.

---

## Risks & Open Questions

| # | Risk                                                             | Mitigation                                                                                                         |
|---|------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------|
| 1 | Redis outage blocks all authenticated traffic                    | Redis is already a critical dep (rate-limit, SSO cache). Acceptable. Alert on Redis up/down already exists.         |
| 2 | Cookie-less fetches (sendBeacon, tracking) break                 | `product_events` endpoints remain intentionally unauthenticated (SPEC-GRAFANA-METRICS). Verify list pre-cutover.    |
| 3 | Third-party `klai-chat.js` widget mistakenly reads session cookie | Widget has no same-origin context with the portal. CORS blocks cross-origin cookie reads anyway.                    |
| 4 | SameSite=Lax breaks a niche OAuth flow                           | All current flows are top-level navigation. If a future popup-flow is added, switch to SameSite=None.               |
| 5 | Refresh-token rotation means a stale token presented twice fails | Acceptable — Zitadel's security-best-practice default. Coalesce refreshes per R7.1 to avoid self-inflicted races.   |
| 6 | Clock skew between portal-api and Zitadel                        | 60s tolerance (C7.3). NTP on core-01 already configured.                                                            |
| 7 | Safari ITP flags the cookie as "third-party" in iframe embeds    | Portal is never iframed by a different origin. CSP `frame-ancestors 'self' https://*.getklai.com` already enforces. |
| 8 | Developer local dev loses tokens more often                      | Dev mode retains `VITE_AUTH_DEV_MODE=true` mock auth; BFF path only activates when `AUTH_DEV_MODE=false`.           |

Open questions requiring a decision before `/moai run`:

- **Q1:** Do we keep `useCurrentUser` alongside `useSession` as a compatibility shim, or migrate all callers in the same PR? **Recommend:** migrate in the same PR — no dual code path.
- **Q2:** Feature-flag the frontend bundle too (env-based switch), or hard-cut only at deploy? **Recommend:** hard-cut at deploy. Rollback is "redeploy previous bundle".
- **Q3:** Do we take this opportunity to migrate `apiFetch.ts` to use typed `FetchError`/`UnauthorizedError` from `lib/fetch-errors.ts`? **Recommend:** yes — R8 already touches `apiFetch`; folding the type unification in is one less followup.
- **Q4:** Short-circuit for `/logged-out`: should the logout endpoint clear server-side session first, then redirect, or do a Zitadel-level `end_session` with `id_token_hint`? **Recommend:** both — server-clears + `end_session` so the user is also logged out of Zitadel (and any other OIDC clients sharing that session). Requires storing `id_token` on the session (already in R1).

---

## Dependencies / Prerequisites

- Redis cluster is already deployed and consumed by portal-api (no new infra).
- Fernet key for session encryption already exists in SOPS (`FERNET_KEY`).
- Zitadel management PAT valid (SPEC-AUTH-006 prerequisite, already in place).
- GitHub Actions retention keeps previous frontend artifacts 90 days (already configured).

No third-party library additions on the backend. Frontend removes two dependencies.

---

## References

- SPEC-AUTH-006 (SSO self-service — joined design context)
- SPEC-AUTH-007 (research memo — why BFF)
- [OAuth 2.0 for Browser-Based Applications (BCP draft)](https://datatracker.ietf.org/doc/html/draft-ietf-oauth-browser-based-apps)
- [OWASP ASVS 5.0 — V3 Session Management](https://owasp.org/www-project-application-security-verification-standard/)
- [Zitadel — Confidential OIDC app type](https://zitadel.com/docs/guides/integrate/login/oidc/web-keys)
- [MDN — `__Secure-` and `__Host-` cookie prefixes](https://developer.mozilla.org/en-US/docs/Web/HTTP/Cookies#cookie_prefixes)
- [Philippe De Ryck — "Token Handler pattern for SPAs"](https://pragmaticwebsecurity.com/articles/oauthoidc/token-handler-spa.html)
