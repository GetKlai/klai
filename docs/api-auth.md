# API: Authentication (`/api/auth/*`)

All auth endpoints are served by the FastAPI portal-api at `https://*.getklai.com/api/auth/*`.

## Endpoints

### POST /api/auth/login

Email + password login. First step of the login flow.

**Request:**
```json
{
  "email": "user@example.com",
  "password": "secret",
  "auth_request_id": "zitadel-oidc-auth-request-id"
}
```

**Responses:**

| Status | Body | Meaning |
|--------|------|---------|
| 200 | `{"callback_url": "https://auth.getklai.com/..."}` | Login complete — redirect browser to `callback_url` |
| 200 | `{"status": "totp_required", "temp_token": "..."}` | TOTP required — proceed to `/totp-login` |
| 401 | `{"detail": "..."}` | Wrong email or password |
| 400 | `{"detail": "..."}` | Auth request expired (Zitadel session gone) |

**Notes:**
- `auth_request_id` is supplied by Zitadel when it redirects the user to `/login?authRequest=<id>`
- On TOTP-required response: the `temp_token` is valid for 5 minutes and locked after 5 wrong TOTP codes

---

### POST /api/auth/totp-login

Complete login with TOTP code. Called after `/login` returns `totp_required`.

**Request:**
```json
{
  "temp_token": "...",
  "code": "123456",
  "auth_request_id": "..."
}
```

**Responses:**

| Status | Body | Meaning |
|--------|------|---------|
| 200 | `{"callback_url": "..."}` | TOTP verified — redirect to `callback_url` |
| 401 | `{"detail": "..."}` | Wrong TOTP code |
| 403 | `{"detail": "..."}` | Too many failed attempts (5) — token locked |
| 400 | `{"detail": "..."}` | `temp_token` expired or unknown |

---

### POST /api/auth/sso-complete

Silent SSO completion. Used by the portal to re-authenticate requests to LibreChat using the stored portal session.

The browser's `klai_sso` cookie (set after login) is read automatically.

**Request:**
```json
{
  "auth_request_id": "..."
}
```

**Responses:**

| Status | Body | Meaning |
|--------|------|---------|
| 200 | `{"callback_url": "..."}` | Session still valid — redirect browser |
| 401 | — | No cookie, or session expired |

**Notes:**
- The session is cached in-memory for 1 hour
- This endpoint is called on every LibreChat page load to ensure the iframe stays authenticated
- See pitfall: `platform-sso-cache-single-instance` (single-instance only)

---

### POST /api/auth/logout

Clear the portal SSO session.

**Request:** No body. The `klai_sso` cookie is read automatically.

**Response:** `204 No Content`

**What it does:**
1. Removes the session from the in-memory cache
2. Deletes the `klai_sso` cookie from the browser
3. Does NOT call Zitadel end-session — caller is responsible for OIDC `signoutRedirect()`

---

### POST /api/auth/totp/setup

Start TOTP 2FA setup. Returns a QR code URI and plaintext secret.

**Authentication:** Bearer token required (`Authorization: Bearer <access_token>`)

**Response:**
```json
{
  "uri": "otpauth://totp/Klai:user@example.com?secret=...",
  "secret": "JBSWY3DPEHPK3PXP"
}
```

**Notes:**
- The secret and URI should be shown once and then discarded after the user confirms
- Frontend clears `uri` and `secret` from state after `/totp/confirm` succeeds

---

### POST /api/auth/totp/confirm

Activate TOTP after the user has scanned the QR code.

**Authentication:** Bearer token required

**Request:**
```json
{
  "code": "123456"
}
```

**Response:** `204 No Content`

On failure: `401` with `{"detail": "..."}` if the code is wrong.

---

### POST /api/auth/password/reset

Send a password reset email.

**Request:**
```json
{
  "email": "user@example.com"
}
```

**Response:** Always `204 No Content` — does not reveal whether the email exists.

---

### POST /api/auth/password/set

Complete password reset using the code from the reset email.

**Request:**
```json
{
  "user_id": "...",
  "code": "...",
  "new_password": "mysecurepassword"
}
```

**Response:** `204 No Content` on success. `400` with `{"detail": "..."}` if code is expired/invalid.

---

### POST /api/auth/verify-email

Verify email address using the link from the activation or verification email.

**Request:**
```json
{
  "user_id": "...",
  "code": "...",
  "org_id": "..."
}
```

**Response:** `204 No Content` on success.

---

## Cookie: klai_sso

The `klai_sso` cookie is set after successful login and removed on logout.

| Property | Value |
|----------|-------|
| Domain | `.getklai.com` (shared across subdomains) |
| HttpOnly | Yes |
| Secure | Yes (HTTPS only) |
| SameSite | Lax |
| Max-Age | 3600 seconds (1 hour) |

The cookie value is an opaque token. The actual Zitadel session is stored in the in-memory `_sso_cache` on the portal-api instance.

## CORS

The API allows credentials (`allow_credentials=True`) from:
- `http://localhost:5174` (local dev)
- `https://my.getklai.com`
- Any `https://*.getklai.com` subdomain (tenant portals)

## Cache-Control

All `/api/*` responses include `Cache-Control: no-store` to prevent browser and proxy caching of user-specific responses.
