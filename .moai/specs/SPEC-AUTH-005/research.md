# SPEC-AUTH-005: Research — OIDC Session Hardening

## Current State

**Stack:** `react-oidc-context` v3.3.1 + `oidc-client-ts` v3.1.0 + Zitadel (OIDC provider)

**Main file:** `klai-portal/frontend/src/lib/auth.tsx` (75 LOC)

**What's already implemented:**
- localStorage persistence via `WebStorageStateStore` (fixed sessionStorage issue)
- `automaticSilentRenew: true` with `offline_access` scope (refresh token flow)
- `revokeTokensOnSignout: true` (calls Zitadel end_session)
- `AuthSessionMonitor` catches `invalid_grant`/`login_required` → signs out
- SSO cookie: 90 days, Fernet-encrypted, httponly, samesite=lax

**What's missing (5 gaps identified):**

| Gap | Current state | Industry standard |
|---|---|---|
| Cross-tab logout | No `storage` event listener | All tabs sync within 2s |
| Stale state cleanup | No `clearStaleState()` call | Cleanup on startup |
| Token expiry UI | Silent renewal only | 5-min warning banner |
| Idle timeout | None | 15-30 min with warning |
| Cross-tab idle sync | N/A | Activity in any tab resets all |

## Auth-Related Files

| File | LOC | Purpose |
|---|---|---|
| `src/lib/auth.tsx` | 75 | OIDC config, AuthSessionMonitor, provider |
| `src/hooks/useCurrentUser.ts` | ~50 | `/api/me` query hook |
| `src/hooks/useUserLifecycle.ts` | ~42 | User suspend/offboard mutations |
| `src/routes/callback.tsx` | 110 | Post-login routing (provisioning, MFA, billing) |
| `src/routes/logged-out.tsx` | 51 | Logout confirmation page |
| `src/routes/login.tsx` | Custom | Login UI (not OIDC redirect) |
| `src/routes/app/route.tsx` | Auth guard | App layout with auth check |
| `src/routes/admin/route.tsx` | Role guard | Admin layout with role check |
| `backend/app/api/auth.py` | 712 | Login, TOTP, SSO cookie, logout |

## oidc-client-ts API Surface

**UserManager.events (available for R1, R4):**
- `addUserSignedOut` — fires when user store is cleared in another tab
- `addAccessTokenExpiring` — fires N seconds before token expires
- `addAccessTokenExpired` — fires when token has expired
- `addUserLoaded` — fires when user/token is loaded (after renewal)
- `addUserUnloaded` — fires when user is removed

**UserManager methods (available for R2):**
- `clearStaleState()` — removes OIDC keys older than `staleStateAgeInSeconds`

**Config options (available for R4):**
- `accessTokenExpiringNotificationTimeInSeconds` — default 60, set to 300 for 5-min warning

## PKCE Status

`oidc-client-ts` v3 enables PKCE by default for authorization code flow (`response_type: 'code'`). No explicit config needed. Zitadel requires PKCE for public clients (which SPAs are). Currently active but not documented in config.

## Idle Timeout Patterns

No existing idle detection in the codebase (grep for `idle|inactivity|activity` returned only UI component contexts like sidebar collapse).

Industry standard approaches:
1. **DOM event tracking** — `mousemove`, `keydown`, `touchstart`, `scroll`, `click`
2. **Debounced writes** — Write `lastActivity` to localStorage every 30s max
3. **Cross-tab sync** — Other tabs detect via `storage` event
4. **Warning + hard cutoff** — Warning at T-5min, logout at T

Libraries considered:
- `react-idle-timer` — popular but adds dependency for ~80 lines of custom code
- Custom hook — preferred (fewer dependencies, full control)

## Risks

1. **react-oidc-context v3 UserManager access** — Need to verify how to access the underlying UserManager. May need `useAuth()` internal ref or `oidcConfig` callback pattern
2. **Race condition on multi-tab signout** — Multiple tabs calling `removeUser()` simultaneously. Mitigated by checking `isAuthenticated` before calling
3. **Idle timeout UX** — Too aggressive frustrates users reading docs. 30 min with 5-min warning is the sweet spot for enterprise SaaS
