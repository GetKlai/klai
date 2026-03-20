# Authentication Flows

This document describes all auth flows in klai-portal: how they work end-to-end, what components are involved, and what state is held where.

## Components

| Component | Role |
|-----------|------|
| Zitadel | Identity provider -- sessions, OIDC, TOTP, password management |
| portal-api | FastAPI backend -- auth proxy, encrypted SSO cookie, TOTP relay |
| portal-frontend | React SPA -- login UI, OIDC client (react-oidc-context) |
| LibreChat | AI chat, accessed via iframe inside portal -- separate OIDC client |

---

## 1. Login flow

```
Browser -> Zitadel (OIDC auth request) -> /login?authRequest=<id>
  |
/login page checks for existing portal SSO session (sso-complete)
  | no session
Email + password form -> POST /api/auth/login
  |
portal-api creates Zitadel session
  | (no TOTP)
_finalize_and_set_cookie()
  -> zitadel.finalize_auth_request(auth_request_id, session)
  -> Set klai_sso cookie (Fernet-encrypted, 24h max-age)
  <- callback_url
  |
Browser redirected to callback_url
  |
react-oidc-context processes OIDC response -> user authenticated
```

**Key decision point:** when the user arrives at `/login?authRequest=<id>`, the portal immediately tries `POST /api/auth/sso-complete`. If a valid `klai_sso` cookie exists and the Zitadel session it contains is still active, login completes silently (no form shown). This is how LibreChat inside the iframe authenticates without a second login prompt.

---

## 2. TOTP login flow

```
POST /api/auth/login -> { status: "totp_required", temp_token }
  |
TOTP code form -> POST /api/auth/totp-login { temp_token, code }
  |
portal-api: retrieve pending session from _pending_totp cache
  -> verify TOTP code with Zitadel
  -> on success: _finalize_and_set_cookie()
  <- callback_url
```

**State:** Pending session stored in `_pending_totp` (TTL: 5 min, max 5 failures).

---

## 3. TOTP setup flow

```
/setup/2fa page (requires authenticated user)
  |
POST /api/auth/totp/setup (Bearer token)
  <- { uri, secret }
  |
Show QR code + manual secret to user
  |
User scans QR -> enters 6-digit code
  |
POST /api/auth/totp/confirm { code } (Bearer token)
  | success
Clear uri + secret from state (no longer needed)
  |
Redirect to /app or /admin
```

**Note:** The setup flow is only reachable if the user is already authenticated (has a valid Bearer token). The route guard at `/app` and `/admin` redirects users who have not yet set up 2FA to `/setup/2fa`.

---

## 4. SSO completion flow (LibreChat iframe + silent renew)

This flow is triggered in two scenarios:
- **LibreChat page load:** the iframe starts its own OIDC flow with Zitadel
- **Silent token renewal:** react-oidc-context renews the portal access token via a hidden iframe

```
OIDC authorize request -> Zitadel auth request -> /login?authRequest=<id>
  |
POST /api/auth/sso-complete { auth_request_id }
  (klai_sso cookie sent automatically by browser)
  |
portal-api: decrypt cookie (Fernet)
  -> extract session_id + session_token
  -> finalize auth request with Zitadel
  <- callback_url
  |
Browser (iframe) redirected to callback_url
  |
OIDC session established (LibreChat or portal token renewed)
```

**Session validity:** The cookie max-age is 24 hours, but Zitadel is the actual authority. If the Zitadel session has expired, `finalize_auth_request` returns an error and the user sees the login form. No server-side state is involved -- the cookie is self-contained.

---

## 5. Signup flow

```
/signup page
  |
POST /api/user/signup { first_name, last_name, company, email, password }
  |
portal-api: creates Zitadel user + organisation + sends verification email
  |
/signup/confirm page -- user clicks link in email
  |
GET /verify?code=...&userId=...&organization=...
  |
POST /api/auth/verify-email { code, user_id, org_id }
  | success
User redirected to login
```

---

## 6. Password reset flow

```
/password/forgot -> POST /api/auth/password/reset { email }
  (always 204 -- no email enumeration)
  |
User receives email with link:
  /password/set?userID=...&code=...
  |
New password form -> POST /api/auth/password/set { user_id, code, new_password }
  | success
Redirected to / (login)
```

---

## 7. Logout flow

```
User clicks logout in sidebar
  |
POST /api/auth/logout
  -> delete klai_sso cookie
  |
auth.signoutRedirect() (react-oidc-context)
  -> Zitadel end_session endpoint
  -> post_logout_redirect_uri: /logged-out
  |
/logged-out page shown
```

**Implementation note:** The `fetch('/api/auth/logout')` call is awaited before `signoutRedirect()` to ensure the SSO cookie is cleared before the browser navigates away. This prevents a race condition where an in-flight sso-complete request could succeed with a stale session.

---

## Session state

| Store | What | TTL | Notes |
|-------|------|-----|-------|
| `klai_sso` cookie | Fernet-encrypted `{session_id, session_token}` | 24 hours (max-age) | Stateless; Zitadel is the authority on validity |
| `_pending_totp` | `{session_id, session_token, failures}` | 5 minutes | In-memory; locked after 5 failures |
| react-oidc-context | OIDC tokens + user info | access_token TTL | In browser memory; auto-renewed via silent renew |

**Design rationale:** The SSO cookie is fully stateless (no server-side cache). This means:
- Survives portal-api restarts
- Scales horizontally (multiple portal-api instances)
- Zitadel is the single source of truth for session validity
- Cookie max-age is generous (24h); actual session expiry is controlled by Zitadel

---

## Route guards

### `/app/*` and `/admin/*`
Implemented in the TanStack Router `beforeLoad`:

1. If not authenticated: redirect to `/?authRequest=...` (start OIDC flow)
2. If authenticated but no TOTP set up: redirect to `/setup/2fa`
3. For `/admin/*`: if user role is not `admin`: redirect to `/app`

### `/setup/2fa`
Requires authentication (Bearer token). If Zitadel session is invalid, the setup endpoint returns 401 and the user sees an error with a retry button.
