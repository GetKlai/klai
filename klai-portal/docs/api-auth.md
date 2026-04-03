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
- The SSO cookie is fully stateless — no server-side cache. Zitadel is the authority on session validity.
- This endpoint is called on every LibreChat page load to ensure the iframe stays authenticated

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

## Subject Access Request (GDPR Art. 15)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/me/sar-export` | Download all personal data as JSON |

Returns structured JSON with the following top-level sections:

**`klai_portal`**

| Section | Contents |
|---------|---------|
| `identity` | Name, email, MFA status — sourced from Zitadel; `null` on failure |
| `account` | Role, status, language, GitHub username, KB settings |
| `group_memberships` | Groups with join dates and admin status |
| `knowledge_base_access` | Role, assignment dates |
| `audit_events` | Actor's own actions only |
| `usage_events` | Product events |
| `meetings` | Title, platform, URL, status, language, duration, transcript, summary |

**`external_systems`**

Documents (description only, no data export) which external systems hold data about the user: Moneybird, LibreChat, Twenty CRM.

Self-service only: users can export only their own data. No admin-initiated SAR export exists.

---

## User Lifecycle Endpoints

| Method | Endpoint | Description | Role |
|--------|----------|-------------|------|
| POST | `/api/admin/users/{id}/suspend` | Suspend user (revoke access) | Admin |
| POST | `/api/admin/users/{id}/reactivate` | Reactivate suspended user | Admin |
| POST | `/api/admin/users/{id}/offboard` | Permanently offboard user | Admin |

---

## Product Management (`/api/admin/*`)

Manage product entitlements for users and groups. All endpoints require admin role.

### GET /api/admin/products

List available products for the org's current plan.

**Response:**
```json
["chat", "scribe"]
```

Returns the products allowed by the org's plan (`free` = none, `core` = chat, `professional` = chat + scribe, `complete` = chat + scribe + knowledge).

---

### GET /api/admin/users/{zitadel_user_id}/products

List products directly assigned to a user.

**Response:**
```json
[
  {"product": "chat", "enabled_at": "2026-03-24T10:00:00Z", "enabled_by": "admin-user-id"},
  {"product": "scribe", "enabled_at": "2026-03-24T10:00:00Z", "enabled_by": "admin-user-id"}
]
```

---

### GET /api/admin/users/{zitadel_user_id}/effective-products

List all effective products (direct + group-inherited) with source attribution.

**Response:**
```json
[
  {"product": "chat", "source": "direct"},
  {"product": "scribe", "source": "group", "group_name": "Engineering"}
]
```

---

### POST /api/admin/users/{zitadel_user_id}/products

Assign a product to a user.

**Request:**
```json
{"product": "scribe"}
```

| Status | Meaning |
|--------|---------|
| 201 | Product assigned |
| 403 | Product exceeds org's plan ceiling |
| 409 | Product already assigned to this user |

---

### DELETE /api/admin/users/{zitadel_user_id}/products/{product}

Revoke a product from a user.

| Status | Meaning |
|--------|---------|
| 204 | Product revoked |
| 404 | Product not assigned to this user |

---

### GET /api/admin/products/summary

Per-product user counts for the org.

**Response:**
```json
{"chat": 10, "scribe": 7, "knowledge": 3}
```

---

### Internal: GET /api/internal/users/{zitadel_user_id}/products

Called by the Zitadel Pre-access-token-creation Action to enrich JWTs with `klai:products` claim. Authenticated via `PORTAL_INTERNAL_SECRET`.

**Response:**
```json
{"products": ["chat", "scribe"]}
```

Returns empty list if user not found (fail-closed).

---

## Cookie: klai_sso

The `klai_sso` cookie is set after successful login and removed on logout.

| Property | Value |
|----------|-------|
| Domain | `.getklai.com` (shared across subdomains) |
| HttpOnly | Yes |
| Secure | Yes (HTTPS only) |
| SameSite | Lax |
| Max-Age | 86400 seconds (24 hours) |

The cookie value is a Fernet-encrypted token containing `{session_id, session_token}`. There is no server-side session store — the design is fully stateless. Zitadel is the sole authority on whether the session is still valid.

## CORS

The API allows credentials (`allow_credentials=True`) from:
- `http://localhost:5174` (local dev)
- `https://my.getklai.com`
- Any `https://*.getklai.com` subdomain (tenant portals)

## Cache-Control

All `/api/*` responses include `Cache-Control: no-store` to prevent browser and proxy caching of user-specific responses.
