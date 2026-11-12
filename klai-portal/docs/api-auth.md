# API Authentication

How every caller of portal-api proves who it is, which dependency or
middleware checks it, and what identity the handler receives. The browser
login itself (OIDC flow, session cookie, CSRF, logout) is described step by
step in [auth-flows.md](auth-flows.md); roles and products are in
[admin-roles.md](admin-roles.md).

Hostnames are placeholders (`example.com`). Secret names in this document are
configuration variable names, not values.

## Per surface

| Surface | How the caller proves itself | Checked by | Identity the handler receives |
|---|---|---|---|
| Portal SPA (browser) | `__Secure-klai_session` cookie; `X-CSRF-Token` header on state-changing methods | `SessionMiddleware` (`klai-portal/backend/app/middleware/session.py:144-174`), then `get_caller` (`klai-portal/backend/app/core/permissions.py:315-336`) | `UserPermissions` for the session's org (`permissions.py:62-108`) |
| BFF proxy to scribe-api and the docs app (`/api/scribe/*`, `/api/docs/*`) | The browser session; portal-api forwards the session's access token as Bearer plus portal-verified identity headers | `get_session` and `verify_bff_session_identity` (`klai-portal/backend/app/api/proxy.py:207-260`, `:375-394`) | Upstream receives `Authorization`, `X-Internal-Secret` and `X-Klai-Verified-User-Id/Org-Id/Org-Slug` (`proxy.py:180-189`) |
| Partner API `/partner/v1/*` | `Authorization: Bearer pk_live_<40 hex>` | `get_partner_key` (`klai-portal/backend/app/api/partner_dependencies.py:248-330`) | `PartnerAuthContext`: key id, org, permissions, KB access, rate limit (`partner_dependencies.py:54-68`) |
| Chat widget on a customer site | A widget session JWT (HS256, one hour) obtained from `GET /partner/v1/widget-config`, then sent as Bearer | `get_partner_key`, JWT branch `_auth_via_session_token` (`partner_dependencies.py:105-200`) | `PartnerAuthContext` for the widget's org and KBs |
| Tenant LibreChat | The tenant's internal-chat partner key (`KLAI_CHAT_API_KEY`) against `/partner/v1` | `get_partner_key` | `PartnerAuthContext` with the `internal_chat` permission |
| Internal services calling `/internal/*` | `Authorization: Bearer <INTERNAL_SECRET>`; one route may accept a second, narrower secret | `_require_internal_token` (`klai-portal/backend/app/api/internal.py:305-345`) | No user; the handler resolves the org from path or body and sets the tenant itself |
| Services verifying a user or tenant claim | `POST /internal/identity/verify`, `/internal/identity/verify-tenant`, `/internal/mcp-token/verify`, through `klai-libs/identity-assert` | `_require_internal_token` plus the identity verifier (`internal.py:2116`, `:2408`, `:2800`) | A verified or denied decision for the claimed user and org |
| portal-api calling knowledge-ingest and retrieval-api | `X-Internal-Secret` with a separate secret per target | The target service | `org_id` as the Zitadel org id string (`klai-portal/backend/app/services/retrieval_context.py:830-840`) |
| Docs app (klai-docs) | A Zitadel access token verified against the issuer's JWKS, or `X-Internal-Secret` with `DOCS_INTERNAL_SECRET` plus `X-User-ID` | `requireAuthOrService` and `requireOrgAccess` (`klai-docs/lib/auth.ts:34-85`, `:111-131`) | The token payload (`sub`) and the org row for the slug in the path |
| MCP clients (knowledge MCP) | An access token `klai_mcp_...` issued by portal-api acting as OAuth 2.1 authorisation server | `klai-knowledge-mcp` calls `/internal/mcp-token/verify` | The user, org and scope bound to the token row |
| Slack (klai-slack) | Towards portal-api: `INTERNAL_SECRET` on `/internal/slack/*`; for questions: a partner key with the `slack` permission | `_require_internal_token` (`klai-portal/backend/app/api/internal_slack.py:115-182`); `get_partner_key` | Org from the signed link token; `PartnerAuthContext` for chat |
| Shield browser extension | `Authorization: Bearer ks_live_...`, obtained through a one-time `ks_code_...` exchange | `get_shield_auth` (`klai-portal/backend/app/api/shield.py:162-200`) | The user in the platform org who created the token |
| klai-mailer | Zitadel calls `POST /notify` with a signature; portal-api calls `POST /internal/send` with a shared secret | `klai-mailer/app/main.py:164`, `:323` | No user or org; the mail request itself |
| Moneybird webhook | `webhook_token` in the body, compared in constant time | `klai-portal/backend/app/api/webhooks.py:151-185` | Org resolved from the Moneybird contact |

### Browser session

The cookie is resolved on every request by `SessionMiddleware`, which also
refreshes the access token and enforces the CSRF check. Route handlers take
one of three dependencies:

- `get_session` (`klai-portal/backend/app/api/session_deps.py:16-24`) returns
  the `SessionContext` (session id, Zitadel user id, access token, CSRF token,
  expiry, org id; `klai-portal/backend/app/core/session.py:35-66`) or answers
  401 `cookie_required`.
- `get_current_user_id` (`klai-portal/backend/app/api/auth.py:620-633`) returns
  only the Zitadel user id after a userinfo check. The MFA enrolment routes use
  it.
- `get_caller` and the gates built on it (`get_caller_at_least`,
  `require_capability`, `require_product`, `require_platform_unlocked`,
  `require_platform_admin`; `permissions.py:360-476`) return `UserPermissions`
  and set the row-level-security tenant for the request.

All three get their credential from the session. `bearer()` turns the session
into `Authorization: Bearer <access token>` for code that expects credentials;
a client-supplied Authorization header is not used
(`klai-portal/backend/app/api/bearer.py:21-32`).

### Partner API keys

A partner key is `pk_live_` followed by 40 hex characters; only its SHA-256 is
stored (`klai-portal/backend/app/services/partner_keys.py:15-30`). Admins
create keys under `/api/admin/api-keys`. `get_partner_key` looks the key up by
hash, compares in constant time, loads the org and sets the tenant, then
checks the org's platform unlock: `partner_api` for an ordinary key, `slack`
for the Slack key, none for the tenant's internal-chat key
(`partner_dependencies.py:286-297`). KB access comes from the key's
`partner_api_key_kb_access` rows, limited to the org's KBs and the creator's
own personal KB (`partner_dependencies.py:299-310`). A per-key requests-per-minute
limit answers 429 with `Retry-After` (`partner_dependencies.py:311-320`).

### Widget

`GET /partner/v1/widget-config?id=<widget_id>`
(`klai-portal/backend/app/api/partner_widget_config.py:216`) looks up the
widget, checks the browser's `Origin` against the widget's `allowed_origins`
to decide which sites may embed it, and returns the configuration with a
session token. The token is an HS256 JWT valid for one hour, carrying
`wgt_id`, `org_id`, `kb_ids` and `jti`, signed with a per-tenant key derived
by HKDF from `WIDGET_JWT_SECRET` and the tenant slug
(`klai-portal/backend/app/services/widget_auth.py:35`, `:82-100`, `:145-170`).
The widget sends it as Bearer to `/partner/v1/chat/completions`;
`get_partner_key` routes any Bearer value that does not start with `pk_live_`
to `_auth_via_session_token`, which verifies the token with the derived key,
requires the `widgets` unlock and re-reads the widget's KB access from the
database on every call.

### Internal service-to-service calls

Every `/internal/*` handler starts with `_require_internal_token`
(`internal.py:305-345`): the secret is compared with `hmac.compare_digest`,
then a per-caller-IP rate limit applies, and each handler writes an audit row
once it knows the org. An unset `INTERNAL_SECRET` answers 503.

Services that act on behalf of a user verify the user's claim with portal-api
before they trust it. `klai-libs/identity-assert` wraps the three verify
endpoints: it caches a positive answer for 60 seconds, never caches a denial,
and treats an unreachable portal as a denial
(`klai-libs/identity-assert/klai_identity_assert/client.py:1-21`). The
connector, knowledge-ingest, knowledge MCP, retrieval-api and scribe-api
depend on it. Membership in `portal_users` is the source of truth for which
org a user belongs to.

`klai-libs/service-auth` contains an OAuth client-credentials token client
(`klai-libs/service-auth/klai_service_auth/client.py:1-25`). The only runtime
import is `project_role_scopes`, used by retrieval-api to read scopes
(`klai-retrieval-api/retrieval_api/middleware/auth.py:41`).

### Docs app

The portal SPA reaches the docs app only through the BFF proxy at
`/api/docs/*`; the reverse proxy answers 404 for the docs app's own API path
and serves only the public reader directly (`deploy/caddy/Caddyfile:504-520`).
Inside the docs app, `requireAuthOrService` accepts `X-Internal-Secret`
matching `DOCS_INTERNAL_SECRET` with an `X-User-ID`, which portal-api's
provisioning client uses (`klai-portal/backend/app/services/docs_client.py:40`),
and otherwise verifies the Bearer token with `jose` against the issuer's JWKS.
`requireOrgAccess` then resolves the organisation from the slug in the path.
The public reader needs no login and returns 404 for personal KBs and KBs with
`visibility = private`
(`klai-docs/app/(reader)/[...path]/page.tsx:116`).

### LibreChat

Provisioning writes each tenant's LibreChat environment with its own Zitadel
OIDC client (`OPENID_*`) for user login, and with `KLAI_CHAT_API_KEY` and
`KLAI_CHAT_BASE_URL` pointing at portal-api's partner API
(`klai-portal/backend/app/services/provisioning/generators.py:36-38,333-334`). The key is the tenant's internal-chat key, minted during
provisioning (`klai-portal/backend/app/services/internal_chat_keys.py:41`).
Chat turns from LibreChat therefore arrive at `/partner/v1/chat/completions`
like any partner call, and the `internal_chat` permission routes them to the
knowledge path (`klai-portal/backend/app/api/partner.py:544-546`).

### MCP clients: the portal as OAuth server

portal-api is an OAuth 2.1 authorisation server for MCP clients
(`klai-portal/backend/app/api/mcp_oauth.py:1-13`):

| Endpoint | Purpose |
|---|---|
| `GET /.well-known/oauth-authorization-server` | Server metadata (RFC 8414) |
| `POST /oauth/register` | Dynamic client registration (RFC 7591) |
| `GET /oauth/authorize` | Consent page for the signed-in user; PKCE S256 is required |
| `POST /oauth/authorize` | Consent submit, protected by its own form CSRF token stored in Redis |
| `POST /oauth/token` | Code exchange and refresh |

The consent page requires a BFF session. Approval issues an authorisation
code (60 seconds) that the client exchanges, with its PKCE verifier, for an
access token `klai_mcp_...` and a refresh token `klai_mcp_rt_...`, scope
`mcp:knowledge` (`klai-portal/backend/app/services/mcp_oauth.py:60-80`). The
knowledge MCP verifies each token through `/internal/mcp-token/verify`, whose
answers are cached in Redis for 60 seconds. Users list and revoke their
connected clients under `/api/me/mcp-tokens`
(`klai-portal/backend/app/api/me_mcp_tokens.py:34`, `:55`, `:99`).

### Slack

klai-slack calls `/internal/slack/link-oauth` when a Slack installation that
an admin started from `/api/admin/slack` completes, and
`/internal/slack/link/{team_id}` when it is removed. The signed link token in
the request decides the org; portal-api mints a partner key with the `slack`
permission for it (`internal_slack.py:1-12`). klai-slack stores that key
encrypted with AES-256-GCM (`klai-slack/app/crypto.py`) and uses it for every
question it forwards to the partner API, where the `slack` unlock gates it.

### Shield browser extension

The extension is for platform administrators. It opens
`GET /api/app/shield/extension/login`, which sends a visitor without a session
through `/api/auth/oidc/start` and, for a platform-org admin, redirects back to
the extension with a one-time `ks_code_` code
(`shield.py:309-345`). `POST /api/app/shield/extension/exchange` swaps the
code for a `ks_live_` token (`shield.py:348`). Tokens are stored as SHA-256
hashes (`klai-portal/backend/app/services/shield_tokens.py:9-37`) and checked
on `/api/shield/*` by `get_shield_auth`, which requires an unrevoked,
unexpired token whose user is an active admin in the platform org (`klai-portal/backend/app/api/shield.py:187-196`).

## Endpoint reference: `/api/auth/*`

All of these are served by portal-api on the login host and the tenant hosts.

### Session

| Method | Path | Notes |
|---|---|---|
| GET | `/api/auth/oidc/start?return_to=<path>` | Starts the OIDC flow; 302 to Zitadel (`auth_bff.py:151`) |
| GET | `/api/auth/oidc/callback` | Driven by Zitadel; creates the session and sets cookies (`auth_bff.py:186`) |
| GET | `/api/auth/session` | 200 `{authenticated, zitadel_user_id, csrf_token, access_token_expires_at}` or 401 `cookie_required` (`auth_bff.py:83`) |
| POST | `/api/auth/bff/logout` | 204; may carry `X-Post-Logout-Redirect` (`auth_bff.py:105`) |

### POST /api/auth/login

Request `{email, password, auth_request_id}`; `auth_request_id` is the
`authRequest` value Zitadel put on `/login`.

| Status | Body | Meaning |
|---|---|---|
| 200 | `{"callback_url": "...", "status": "ok"}` | Done; the browser follows `callback_url` |
| 200 | `{"status": "totp_required", "temp_token": "..."}` | Continue with `/api/auth/totp-login` within 300 seconds |
| 200 | `{"status": "select_workspace", "ref": "..."}` | Continue at `/select-workspace?ref=...` |
| 401 | `{"detail": "Email address or password is incorrect"}` | Wrong credentials |
| 409 | `{"detail": "auth_request_stale"}` | Zitadel already handled this auth request; restart at `/` |
| 400 | `{"detail": "Login request expired, please try again"}` | Zitadel no longer knows the auth request |
| 503 | | Zitadel unavailable for the user or policy lookup |

### POST /api/auth/totp-login

Request `{temp_token, code, auth_request_id}`. Answers like `/login` on
success. A wrong code answers 400 `Invalid code, please try again`; the fifth
wrong code ends the pending login with 429; an unknown or expired
`temp_token` answers 400 `Session expired, please log in again`
(`auth.py:1569-1699`).

### POST /api/auth/sso-complete

Request `{auth_request_id}`, with the `klai_sso` cookie. 200 with
`callback_url` when the stored Zitadel session is still valid; 401 when there
is no cookie, the cookie cannot be decrypted or Zitadel rejects the session
(`auth.py:1702-1750`).

### Second-factor enrolment

All require a signed-in user.

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/api/auth/totp/setup` | none | `{uri, secret}` |
| POST | `/api/auth/totp/confirm` | `{code}` | 204 |
| POST | `/api/auth/passkey/setup` | none | `{passkey_id, options}` |
| POST | `/api/auth/passkey/confirm` | `{passkey_id, public_key_credential, passkey_name}` | 204 |
| POST | `/api/auth/email-otp/setup` | none | 204, Zitadel mails a code |
| POST | `/api/auth/email-otp/resend` | none | 204 |
| POST | `/api/auth/email-otp/confirm` | `{code}` | 204 |

### Password and e-mail

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/api/auth/password/reset` | `{email}` | Always 204 |
| POST | `/api/auth/password/set` | `{user_id, code, new_password}` | 204; also finishes an invitation |
| POST | `/api/auth/verify-email` | `{user_id, code, org_id}` | 204 |
| GET | `/api/auth/password-policy` | | The password rules the forms display (`klai-portal/backend/app/api/auth_password_policy.py:22`) |

### External identity providers and workspaces

| Method | Path | Notes |
|---|---|---|
| POST | `/api/auth/idp-intent` | `{idp_id, auth_request_id}` → `{auth_url}` |
| GET | `/api/auth/idp-callback` | Driven by Zitadel after the provider login |
| POST | `/api/auth/idp-intent-signup` | `{idp_id, locale, invite_token}` → `{auth_url}` |
| GET | `/api/auth/idp-signup-callback` | Driven by Zitadel |
| GET | `/api/auth/pending-session` | Lists the choices behind a `ref` (`klai-portal/backend/app/api/auth_select.py:146`) |
| POST | `/api/auth/select-workspace` | Consumes the `ref` (`auth_select.py:188`) |
| POST | `/api/auth/join-request` | Signed-in user without a workspace asks to join one (`klai-portal/backend/app/api/auth_join.py:38`) |

## Other user endpoints

### Subject access request (GDPR art. 15)

`POST /api/me/sar-export` (`klai-portal/backend/app/api/me.py:532`) returns
the caller's own data as JSON: `generated_at`, `request_user_id`,
`klai_portal` (identity from Zitadel, account, group memberships, KB access,
the caller's own audit events, usage events, LibreChat conversations) and
`external_systems`, which names what Moneybird, LibreChat and the CRM hold
(`me.py:289-331`). Only self-service export exists.

### Products

| Method | Path | Notes |
|---|---|---|
| GET | `/api/admin/products` | Admin; the caller's effective products (`klai-portal/backend/app/api/admin/products.py:63`) |
| GET | `/api/admin/users/{zitadel_user_id}/effective-products` | Admin; a user's products, each with source `account_type` or `addon` (`products.py:78`) |
| GET | `/internal/users/{zitadel_user_id}/products` | Internal secret; `{products, capabilities}`, an empty list for an unknown user (`internal.py:435-465`) |

The former per-user and per-group product assignment endpoints
(`POST`/`DELETE`/`GET /api/admin/users/{id}/products`,
`/api/admin/products/summary`, `/api/admin/groups/{id}/products`) answer
410 Gone (`products.py:138-153`; `klai-portal/backend/app/api/groups.py:435-447`).

The user lifecycle endpoints (invite, suspend, reactivate, offboard, delete)
are listed in [admin-roles.md](admin-roles.md).
