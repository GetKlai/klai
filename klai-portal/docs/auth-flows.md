# Authentication Flows

How a person signs in to the Klai portal, what the browser and the server hold
afterwards, and how the session ends. The portal is a backend-for-frontend
(BFF): portal-api runs the OIDC authorisation-code flow with PKCE against
Zitadel, keeps every token server-side, and gives the browser an opaque
session cookie plus a CSRF token. The browser never sees a Zitadel token.

Related documents: [api-auth.md](api-auth.md) covers how every other caller
(partner keys, widget, internal services, MCP clients, Slack, the browser
extension, the docs app) authenticates; [admin-roles.md](admin-roles.md)
covers roles, products and the user lifecycle.

Hostnames below are placeholders: `my.example.com` is the login host
(`FRONTEND_URL`), `acme.example.com` a tenant subdomain, and `.example.com`
the configured `DOMAIN`.

## Components

| Component | Role |
|---|---|
| Zitadel | Identity provider: users, Zitadel sessions, passwords, second factors, OIDC tokens. Its Login V2 sends every authorisation request to the portal's own login page. |
| portal-api | FastAPI backend: OIDC client, session store (Redis), CSRF check, the login endpoints that drive the Zitadel session API. |
| portal-frontend | React SPA: reads the session, starts login by navigating to portal-api, renders the login page. |
| Redis | Holds the encrypted session records, the pending OIDC state and the pending TOTP state. |

## 1. Browser login, step by step

This is the password path for a user who arrives without a session.

1. The SPA mounts `BffAuthProvider`, which calls `GET /api/auth/session` with
   `credentials: 'include'` (`klai-portal/frontend/src/lib/auth.tsx:73-107`).
   Without a valid session the backend answers 401 `cookie_required`
   (`get_session`, `klai-portal/backend/app/api/session_deps.py:16-24`).
2. The start page sees no session and calls `signinRedirect()`
   (`klai-portal/frontend/src/routes/index.tsx:15-20`), which navigates the
   browser to `/api/auth/oidc/start?return_to=<path>` (`redirectToStart`,
   `klai-portal/frontend/src/lib/auth-context.ts:87-91`). A protected route
   without a session first navigates to `/` (`useProtectedRoute`,
   `klai-portal/frontend/src/hooks/useProtectedRoute.ts:117-120`).
3. `oidc_start` (`klai-portal/backend/app/api/auth_bff.py:151-178`) reduces
   `return_to` to a path on the portal's own origin (`_safe_return_to`, `:434-475`; the
   frontend applies the same rule in `safeReturnTo`, `auth-context.ts:86-98`), generates
   `state` and a PKCE verifier with its S256 challenge, stores
   `{state, verifier, return_to, user-agent hash}` in Redis for 600 seconds
   (`klai-portal/backend/app/services/oidc_pending.py:24`), and redirects to
   the Zitadel authorize URL with scope `openid profile email offline_access`
   (`build_authorize_url`, `klai-portal/backend/app/services/bff_oidc.py:64-86`).
   The redirect URI is `<FRONTEND_URL>/api/auth/oidc/callback`
   (`auth_bff.py:368-375`).
4. Zitadel Login V2 sends the browser to the portal login page
   `/login?authRequest=<id>` (`klai-portal/backend/app/api/auth.py:15-16`).
   This is where the custom login page takes part in the identity provider's
   flow: everything the page does ends with portal-api finalising that Zitadel
   auth request. The page first tries a silent completion,
   `POST /api/auth/sso-complete` with the `klai_sso` cookie, aborting after
   4 seconds and showing the form after 5 seconds at most
   (`klai-portal/frontend/src/routes/login.tsx:43-88`). See section 3.
5. Without a reusable Zitadel session the page shows the form and posts
   `POST /api/auth/login {email, password, auth_request_id, accepts_email_code}`
   (`login.tsx:131-177`; the page always sends `accepts_email_code: true`,
   `login.tsx:140`). The handler `login`
   (`klai-portal/backend/app/api/auth.py:1411-1578`):
   - looks the user up by e-mail in Zitadel; a 5xx or network error answers
     503 (`auth.py:1417-1446`);
   - creates a Zitadel session with the password; wrong credentials answer a
     uniform 401 (`auth.py:1448-1480`);
   - reads the second factors the user has registered and applies the
     organisation's second-factor policy (`_resolve_and_enforce_mfa`,
     `auth.py:804-933`, see section 4);
   - records the `login` product event and an `auth.login` audit row
     (`auth.py:1494-1507`).
6. A registered second factor is always asked for, under every policy. When
   `login_second_factors` lists one, the handler takes the first: an
   authenticator code (`totp`) before an e-mail code (`otpEmail`)
   (`auth.py:1509-1512`). For the e-mail code it first has Zitadel mail a code
   through a session challenge (`send_session_email_code`, `zitadel.py:914`);
   a page that did not send `accepts_email_code` is told with a 409 to reload
   and sign in again, and a failed send answers 503 (`auth.py:1514-1533`). The
   handler stores the pending Zitadel session in Redis
   (`totp_pending:<token>` plus a failure counter, the factor in the field
   `check`, TTL `totp_pending_ttl_seconds` = 300,
   `klai-portal/backend/app/core/config.py:81`) and answers
   `{"status": "totp_required", "temp_token": ...}` or
   `{"status": "otp_email_required", "temp_token": ...}`
   (`auth.py:1542-1554`). The page then posts `POST /api/auth/totp-login` for
   both factors, with a code of 6 digits for the authenticator and 8 for the
   e-mail code (`login.tsx:157-159`, `:227`); the handler adds the code to the
   Zitadel session as the factor stored in `check`
   (`update_session_with_otp`, `zitadel.py:931`). The fifth wrong code deletes
   the pending state and answers 429 (`_TOTP_MAX_FAILURES`, `auth.py:224`,
   `auth.py:1581-1714`).
7. If the user has no portal membership yet but a verified e-mail whose domain
   matches a workspace's `primary_domain`, the answer is
   `{"status": "select_workspace", "ref": ...}` and the browser goes to
   `/select-workspace` (`_maybe_select_workspace_ref`, `auth.py:941`;
   `auth.py:1556-1570`; `login.tsx:165-168`). `POST /api/auth/select-workspace`
   then finalises for an existing member, adds a `portal_users` row when the
   workspace has `auto_accept_same_domain`, or files a join request
   (`klai-portal/backend/app/api/auth_select.py:1-11`, `:188`).
8. Otherwise `_finalize_and_set_cookie` (`auth.py:659-702`) finalises the
   Zitadel auth request with the session, sets the `klai_sso` cookie (section 3)
   and returns a callback URL that `_validate_callback_url` (`auth.py:559`)
   accepts only for localhost, the apex and login host, active tenant
   subdomains and the approved per-tenant prefixes. A request Zitadel already
   handled (back button, double submit) answers 409 `auth_request_stale`, on
   which the page restarts at `/` (`auth.py:674-680`, `login.tsx:146-150`).
9. The browser follows the callback URL. Zitadel redirects to
   `GET /api/auth/oidc/callback` (`oidc_callback`, `auth_bff.py:186-291`),
   which consumes `state` from Redis, exchanges the code with the stored
   verifier, verifies the ID token (`verify_id_token`, `bff_oidc.py:238`) and
   resolves the workspace from `portal_users` by `sub`: one row binds the
   session to that org, several rows need `selected_org_id` (otherwise the
   redirect carries `workspace_selection_required`), no row leaves the session
   unbound. It then creates the session record (step 10), sets the cookies and
   redirects to the SPA route `/callback?return_to=<path>`. An unknown `state`
   gets one silent retry through `/` with a 30-second retry cookie; other
   failures go to `/logged-out?reason=<code>` (`auth_bff.py:73-75`, `:378-401`).
10. `SessionService.create` (`klai-portal/backend/app/services/bff_session.py:210-246`)
    stores the record under `klai:session:<sid>`, Fernet-encrypted with
    `BFF_SESSION_KEY`, with TTL `bff_session_ttl_seconds` (30 days,
    `config.py:90`). `set_session_cookies` (`auth_bff.py:299-326`) sets
    `__Secure-klai_session` (the sid, HttpOnly) and `__Secure-klai_csrf`
    (readable by JavaScript), both Secure, SameSite=Lax, Domain=`.example.com`,
    max-age 30 days.
11. The SPA `/callback` page fetches `/api/me` and decides where to go
    (`klai-portal/frontend/src/routes/callback.tsx:95-118`): `/no-account` when
    the user has no workspace, `/provisioning` while the tenant is still being
    provisioned, the tenant subdomain when the workspace lives on another host
    (the path is kept), `/setup/mfa` when no second factor is registered,
    `/admin/billing` for an admin whose billing status is `pending`, and
    otherwise `return_to`.
12. From then on every request carries the cookie. `SessionMiddleware`
    (`klai-portal/backend/app/middleware/session.py:144-174`) loads the record,
    refreshes the access token when needed (section 2), puts a
    `SessionContext` on `request.state.session` and runs the CSRF check
    (section 2). `KlaiTenantHostMiddleware`
    (`klai-portal/backend/app/middleware/tenant_host.py:1-22`) compares the
    first DNS label of a tenant host with the session's org slug and answers a
    mismatch with a 302 for page loads or a 409 `tenant_host_mismatch` with
    `redirect_to` for API calls, which `apiFetch` follows
    (`klai-portal/frontend/src/lib/apiFetch.ts:148-160`).
13. A route that needs the caller depends on `get_caller`
    (`klai-portal/backend/app/core/permissions.py:323-345`). Its credentials come
    from `bearer()`, which builds `Authorization: Bearer <access token>` from
    the session; a Bearer header sent by the client is not read
    (`klai-portal/backend/app/api/bearer.py:21-32`). `get_caller` validates the
    token through Zitadel userinfo (60-second cache), loads org and user in one
    query bound to the session's org, answers 403 `tenant_deleting` while the
    tenant is being deprovisioned and 403 `user_suspended` or `user_offboarded`
    for a membership that is not active (`refuse_inactive_membership`,
    `permissions.py:301-310`), then sets the tenant for row-level security and
    the acting user (`permissions.py:235-290`). The handler receives a `UserPermissions`
    snapshot (`permissions.py:62-108`).

## 2. What the browser holds, what the server holds

| Where | What | Lifetime |
|---|---|---|
| Browser cookie `__Secure-klai_session` | Opaque session id, 256 random bits (`bff_session.py:187-190`). HttpOnly. | 30 days from login |
| Browser cookie `__Secure-klai_csrf` | CSRF token, separate 256 random bits (`bff_session.py:192-195`). Readable by JavaScript. | 30 days from login |
| Browser cookie `klai_sso` | Fernet-encrypted Zitadel session id and token, for silent completion (section 3). HttpOnly. | `sso_cookie_max_age`, 90 days (`config.py:76`) |
| Redis `klai:session:<sid>` | Encrypted record: Zitadel user id, workspace id, access, refresh and ID token, access-token expiry, CSRF token, user-agent and IP hashes (`bff_session.py:56-71`). | 30 days, renewed on every token refresh (`bff_session.py:264-273`) |
| Redis pending OIDC state | `state`, PKCE verifier, `return_to` | 600 seconds |
| Redis `totp_pending:<token>` | Pending Zitadel session between password and TOTP code | 300 seconds |

`GET /api/auth/session` (`auth_bff.py:83-92`) returns the Zitadel user id, the
CSRF token and the access-token expiry, which is all the SPA keeps in memory.

**Refresh.** `SessionMiddleware` calls `refresh_if_needed`
(`bff_session.py:287-326`) on every request. When the access token has less
than `bff_access_token_skew_seconds` (60, `config.py:91`) left, it refreshes
with the refresh token; concurrent requests for the same session wait on one
refresh. When Zitadel rejects the refresh token the session is deleted and the
request continues without a session; a network error keeps the session for
the next request. Zitadel rotates the refresh token on use and the new one is
stored (`bff_session.py:363-370`).

**CSRF.** The check is a double submit. For every method except GET, HEAD and
OPTIONS, the `X-CSRF-Token` header must equal the session's CSRF token,
compared in constant time, or the middleware answers 403 `csrf_invalid`
(`session.py:224-253`). Paths that operate without a BFF session are exempt:
the OIDC start and callback, the login finishers, password reset and set,
e-mail verification, signup, the health probe, the performance beacon,
`/internal/`, `/partner/`, `/api/shield/`, the Shield code exchange and
`/oauth/` (`session.py:54-141`). `apiFetch` copies the CSRF cookie into the
header for POST, PUT, PATCH and DELETE (`apiFetch.ts:83-86`).

**CORS and caching.** `KlaiCORSMiddleware` allows credentialed requests from
the configured origin list (`CORS_ORIGINS`) and from a first-party origin
pattern that is fixed in code rather than configurable
(`klai-portal/backend/app/middleware/klai_cors.py:176-190`; registered in
`klai-portal/backend/app/main.py:394-400`). Every `/api/*` response carries
`Cache-Control: no-store` (`main.py:384-390`).

## 3. Silent completion with `klai_sso`

Every successful login also stores the Zitadel session in the `klai_sso`
cookie (Fernet, key `SSO_COOKIE_KEY`, Domain=`.example.com`, HttpOnly, Secure,
Lax; `auth.py:172-203`, `auth.py:692-701`). When another OIDC client sends the
browser to `/login?authRequest=<id>`, the page calls
`POST /api/auth/sso-complete` (`auth.py:1717-1765`), which decrypts the cookie
and finalises the new auth request with that Zitadel session. Zitadel decides
whether the session is still valid; when it is not, the endpoint answers 401
and the page shows the form. The same mechanism completes a fresh portal login
after the BFF session has ended while the Zitadel session still lives, and it
signs a user in to the tenant's LibreChat, which is a separate OIDC client per
tenant (`OPENID_*` settings written by
`klai-portal/backend/app/services/provisioning/generators.py:264-280`). When
the login page runs inside an iframe and silent completion fails, it posts
`klai-sso-failed` to the parent frame instead of showing a form
(`login.tsx:74-81`).

## 4. Second factor

Each organisation has `mfa_policy`: `optional` (default), `recommended` or
`required` (`klai-portal/backend/app/models/portal.py:108`).

- `ZitadelClient.login_second_factors` is the one answer to which registered
  second factors the login can verify: an authenticator code (`totp`) first,
  then an e-mail code (`otpEmail`). A passkey, a security key or an SMS code is
  not in it, because the login has no step that verifies them
  (`klai-portal/backend/app/services/zitadel.py:19-25`, `:843-857`). An e-mail
  code whose enrolment is not yet confirmed does not count either
  (`counted_second_factors`,
  `klai-portal/backend/app/services/email_otp_enrolment.py:45-59`).
- `_resolve_and_enforce_mfa` (`auth.py:804-933`) reads that list under every
  policy, and the login asks for the first factor in it (steps 5 and 6). When
  the list cannot be read, the login answers 503 under every policy
  (`auth.py:898-916`). Under `required` a user with no counted factor is let
  through so they can enrol; under `optional` and `recommended` such a user
  signs in with the password alone (`auth.py:918-931`).
- `/api/me` returns `mfa_enrolled`, `mfa_policy` and `requires_2fa_setup`
  (true when the policy is `required` and no counted factor is registered),
  from the same two functions; an unreadable answer is a 503, not "not
  enrolled" (`klai-portal/backend/app/api/me.py:192-204`, `:216-218`). The
  protected-route hook sends such a user to `/setup/mfa` until they enrol
  (`useProtectedRoute.ts:126-130`); `/setup/2fa` redirects there as well.
- Enrolment endpoints, all for the signed-in user (`get_current_user_id`,
  `auth.py:634`): TOTP `POST /api/auth/totp/setup` and `/totp/confirm`
  (`auth.py:1768`, `:1927`), passkey `POST /api/auth/passkey/setup` and
  `/passkey/confirm` (`auth.py:1977`, `:2017`), and e-mail code
  `POST /api/auth/email-otp/setup`, `/resend` and `/confirm`
  (`auth.py:2097`, `:2133`, `:2169`).
- The e-mail code enrolment has a confirm step. `setup` writes an
  "unconfirmed" mark in Redis, registers the factor and has Zitadel mail an
  8-digit code through a short-lived session kept in Redis for 300 seconds
  (`_send_email_otp_enrolment_code`, `auth.py:2069-2094`); `resend` mails a new
  one. The factor counts only after `confirm` has checked the code on that
  session and cleared the mark. When the code has expired, `confirm` removes
  the unconfirmed factor and answers 410; a factor that is still unconfirmed at
  the next login is removed there (`auth.py:2169-2245`,
  `email_otp_enrolment.py:38-59`). The mailer writes the mail for that code itself, in the
  reader's language (Dutch or English, English when it is unknown): the code,
  how long it is valid, and no button (`klai-mailer/app/renderer.py:46-70`,
  `:154-175`).

## 5. Single sign-on through an external identity provider

The login page offers Google and Microsoft. `POST /api/auth/idp-intent`
(`auth.py:2247`) resolves the requested provider to one of the two
instance-level Zitadel IdP ids (`ZITADEL_IDP_GOOGLE_ID`,
`ZITADEL_IDP_MICROSOFT_ID`; `_resolve_idp_id`, `auth.py:1094-1102`), creates a
Zitadel IdP intent whose success URL is `/api/auth/idp-callback` and returns
the provider's URL. `GET /api/auth/idp-callback` (`auth.py:2390`) retrieves the
intent, creates a Zitadel user on first use, creates a Zitadel session, runs
the same membership and domain-match routing as the password path, finalises
the auth request, sets `klai_sso` and redirects to the validated callback URL
(`auth.py:2612-2640`). This path asks for no second factor of its own: the
provider has already done its sign-in, second step included, and Klai does not
ask again (decided by the owner on 12 November 2026; `login_second_factors` is
called only on the password path, `auth.py:901`). Signing up with a provider uses
`POST /api/auth/idp-intent-signup` and `GET /api/auth/idp-signup-callback`
(`auth.py:2659`, `:2726`), with a 10-minute encrypted `klai_idp_pending`
cookie carrying the signup state between steps (`auth.py:1118-1119`).

## 6. Signup, password reset and e-mail verification

- Signup: `POST /api/signup` (`klai-portal/backend/app/api/signup.py:301`)
  creates a Zitadel organisation and the founder as a Zitadel user, grants
  `org:owner`, writes the `portal_orgs` and `portal_users` rows (the founder
  gets role `admin`, `signup.py:455`) and queues tenant provisioning as a
  background task (`signup.py:476`). Social signup is
  `POST /api/signup/social` and `/api/signup/social/join` (`signup.py:651`,
  `:859`).
- Password reset: `POST /api/auth/password/reset` always answers 204
  (`auth.py:1187`). The mail links to `/password/set` on the login host, and
  `POST /api/auth/password/set` (`auth.py:1248`) sets the password with the
  code; the same endpoint finishes an invitation.
- E-mail verification: `POST /api/auth/verify-email` (`auth.py:1863`).

All three are pre-session endpoints and are exempt from the CSRF check.

## 7. Logout

1. The SPA posts `POST /api/auth/bff/logout` with the CSRF header
   (`removeUser`, `klai-portal/frontend/src/lib/auth.tsx:127-157`).
2. `logout` (`auth_bff.py:105-143`) deletes the Redis record, revokes the
   refresh token at Zitadel, writes an `auth.logout` audit row and clears
   `__Secure-klai_session`, `__Secure-klai_csrf` and `klai_sso`
   (`_clear_cookies`, `auth_bff.py:340-353`). It always answers 204.
3. When an ID token was known, the response carries `X-Post-Logout-Redirect`
   with the Zitadel end-session URL and `post_logout_redirect_uri=<FRONTEND_URL>/logged-out`.
   The SPA follows it; Zitadel ends its own session and returns the browser to
   `/logged-out`. Without that header the SPA goes to `/logged-out` directly.

## 8. Frontend route guards

`/app/*` and `/admin/*` use the `useProtectedRoute` hook
(`klai-portal/frontend/src/hooks/useProtectedRoute.ts:98-158`). In order: no
session sends the user to `/`; a workspace on another subdomain triggers the
handoff; `requires_2fa_setup` sends the user to `/setup/mfa`; a role-gated
route sends a user below the required role to its fallback. `/admin` requires
at least `kb_manager` and falls back to `/app`
(`klai-portal/frontend/src/routes/admin/route.tsx:18-21`). These guards shape
the interface; every API route checks the caller itself.

## 9. Local development

With both `DEBUG` and `AUTH_DEV_MODE` set (`is_auth_dev_mode`, `config.py:443-445`),
portal-api seeds a development user at start-up and skips Zitadel, and
`SessionMiddleware` attaches a development session to every request without a
cookie (`session.py:164-172`, `session.py:212-221`). The frontend uses
`DevAuthProvider` when `VITE_AUTH_DEV_MODE` is set (`auth.tsx:187-191`).

## History

- Until 19 April 2026 the SPA held Zitadel tokens through
  `react-oidc-context`, sent them as Bearer headers and renewed them in a
  hidden iframe, and logout was `POST /api/auth/logout`. SPEC-AUTH-008
  replaced this with the BFF session described above: session middleware in
  commit 21b870851, the cookie-based frontend in 195d56565 and the end of
  Bearer-header acceptance in e2199a04e (all 19 April 2026). The pending TOTP
  state, then an in-memory cache, moved to Redis under SPEC-SEC-SESSION-001
  (`auth.py:205-222`).
