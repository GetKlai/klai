---
id: SPEC-AUTH-005
version: "1.0.0"
status: draft
created: "2026-04-03"
updated: "2026-04-03"
author: MoAI
priority: P0
---

## HISTORY

| Date | Version | Change |
|------|---------|--------|
| 2026-04-03 | 1.0.0 | Initial SPEC creation |

# SPEC-AUTH-005: Industry-Standard OIDC Session Hardening

## Context

Klai's OIDC session management (`react-oidc-context` v3.3.1 + `oidc-client-ts` v3.1.0 + Zitadel) has a solid foundation: localStorage persistence, automatic silent renewal via refresh tokens, and error-based signout detection. However, five industry-standard mechanisms are missing: cross-tab logout synchronization, stale state cleanup, token expiry awareness, activity-based idle timeout, and cross-tab idle sync. Without these, users experience inconsistent session state across tabs, accumulating stale tokens, and no protection against unattended sessions.

## Scope

All changes are in `klai-portal/frontend/`. No backend changes required. The SSO cookie (90 days, Fernet-encrypted) and Zitadel server-side session management are already adequate.

## Out of Scope

- **DPoP / token binding** -- Zitadel OSS does not fully support sender-constrained tokens
- **RP Logout iframe (`check_session_iframe`)** -- Blocked by third-party cookie restrictions in modern browsers; deprecated in OAuth 2.1. The existing `automaticSilentRenew` + `AuthSessionMonitor` catches server-side session revocation via `invalid_grant`
- **Backend session changes** -- SSO cookie max_age is already 90 days; backend auth flow is complete
- **Custom login UI changes** -- Login/signup routes are not affected

---

## Requirements

### R1: Cross-Tab Logout Synchronization (Event-Driven)

**WHEN** a user signs out in one browser tab, **THEN** all other open tabs of the same portal session SHALL detect the signout within 2 seconds and redirect to the logged-out page.

**Implementation approach:** Register `UserManager.events.addUserSignedOut` callback in `AuthSessionMonitor`. When `oidc-client-ts` detects the user store was cleared (localStorage `storage` event), the callback fires in all other tabs. The callback calls `auth.removeUser()` which triggers the existing signout flow.

**Constraints:**
- C1.1: Must not cause infinite signout loops (guard against re-entrant `removeUser()` calls)
- C1.2: Must work across tabs in the same browser, not across browsers/devices
- C1.3: The `storage` event only fires in tabs OTHER than the one that made the change (browser spec)

### R2: Stale State Cleanup (Event-Driven)

**WHEN** the application starts, **THEN** the system SHALL call `UserManager.clearStaleState()` to remove expired OIDC tokens, abandoned authorization request state, and incomplete code exchange artifacts from localStorage.

**Implementation approach:** Access the `UserManager` instance from `react-oidc-context` and call `clearStaleState()` once during `KlaiAuthProvider` mount.

**Constraints:**
- C2.1: Must run after `AuthProvider` has initialized the `UserManager`
- C2.2: Must not clear the current valid session
- C2.3: `clearStaleState()` removes entries older than `staleStateAgeInSeconds` (default 900s / 15 min)

### R3: PKCE Verification and Documentation (State-Driven)

**WHILE** the portal uses `oidc-client-ts` with the default `response_type: 'code'` flow, **THEN** PKCE (S256 code challenge) SHALL be active by default.

**Implementation approach:** Add explicit documentation comment in `oidcConfig`. No code change needed -- `oidc-client-ts` v3 enables PKCE by default for authorization code flow.

**Constraints:**
- C3.1: If a future upgrade changes the default, this must be caught by review

### R4: Token Expiry Awareness (Event-Driven)

**WHEN** the access token is within 5 minutes of expiration AND automatic silent renewal has not yet refreshed it, **THEN** the system SHALL display a non-blocking notification banner informing the user.

**WHEN** the access token is successfully renewed, **THEN** the banner SHALL be dismissed automatically.

**Implementation approach:** Register `UserManager.events.addAccessTokenExpiring` and `addAccessTokenExpired` callbacks. Use a React context or state to show/hide a banner component. The `accessTokenExpiringNotificationTimeInSeconds` config option controls when the event fires (set to 300 = 5 minutes).

**Constraints:**
- C4.1: Banner must be non-blocking (no modal, no overlay)
- C4.2: Banner must auto-dismiss when renewal succeeds (listen to `addUserLoaded`)
- C4.3: If renewal fails, `AuthSessionMonitor` already handles signout -- no duplicate handling

### ~~R5: Activity-Based Idle Timeout~~ (DEFERRED)

Deferred by product decision: Klai targets long-lived sessions (like Claude, ChatGPT, Notion). Idle timeout is not appropriate for the current user base. May be revisited as an opt-in per-tenant feature for enterprise compliance (ISO 27001).

### ~~R6: Cross-Tab Idle Synchronization~~ (DEFERRED)

Deferred: depends on R5. Same rationale.

---

## Dependencies

| Dependency | Type | Notes |
|---|---|---|
| `oidc-client-ts` v3.1.0 | Existing | `clearStaleState()`, `UserManager.events` API |
| `react-oidc-context` v3.3.1 | Existing | Access to underlying `UserManager` via `useAuth()` |
| `AuthSessionMonitor` | Existing | Already handles `invalid_grant`/`login_required` errors |
| Zitadel | External | Server-side session management unchanged |

## Assumptions

- A-001: `react-oidc-context` v3 exposes `UserManager` events via `useAuth().events` or equivalent
- A-002: `oidc-client-ts` v3 fires `addUserSignedOut` when localStorage user key is deleted by another tab
- A-003: 30-minute idle timeout is acceptable for Klai's enterprise user base (not too aggressive, not too lenient)

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| `react-oidc-context` v3 API doesn't expose `UserManager` events directly | R1, R2, R4 blocked | Access via `useAuth()` internal ref or wrap `AuthProvider` with `onSigninCallback` |
| Cross-tab `removeUser()` race condition (multiple tabs fire simultaneously) | Duplicate signout calls | Guard `removeUser()` with `isAuthenticated` check before calling |
| Idle timeout too aggressive for document readers | User frustration, support tickets | 30 min with visible 5-min warning; "Stay logged in" button resets fully |
| `clearStaleState()` accidentally clears valid session | Users logged out unexpectedly | Only clears state older than 15 minutes (default `staleStateAgeInSeconds`) |
