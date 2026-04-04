# SPEC-AUTH-005: Acceptance Criteria

## AC-1: Cross-Tab Logout Synchronization

**Given** a user is logged in with two browser tabs open on the portal
**When** the user clicks "Uitloggen" in tab 1
**Then** tab 2 automatically redirects to `/logged-out` within 2 seconds
**And** no OIDC tokens remain in localStorage for that session

**Given** a user is logged in with three browser tabs open
**When** tab 1 signs out
**Then** both tab 2 and tab 3 redirect to `/logged-out` within 2 seconds
**And** no infinite signout loop occurs (each tab calls `removeUser()` at most once)

## AC-2: Stale State Cleanup

**Given** localStorage contains stale OIDC state from a previous incomplete login flow (e.g., abandoned `code_verifier`)
**When** the user opens the portal in a new tab
**Then** the stale OIDC keys are removed from localStorage during startup
**And** the user's current valid session is NOT affected

**Given** localStorage contains only valid, non-expired OIDC state
**When** the portal starts
**Then** `clearStaleState()` completes without removing any keys
**And** the user remains logged in

## AC-3: PKCE Active

**Given** the portal OIDC configuration uses `oidc-client-ts` v3+ with authorization code flow
**When** a developer reads `auth.tsx`
**Then** an explicit comment documents that PKCE (S256) is enabled by default
**And** no configuration disables PKCE

## AC-4: Token Expiry Warning

**Given** a user is logged in and the access token will expire in less than 5 minutes
**When** `automaticSilentRenew` has not yet refreshed the token
**Then** a non-blocking banner appears at the top of the page with text "Sessie wordt verlengd..."
**And** the banner does NOT block user interaction (no modal, no overlay)

**Given** the token expiry banner is visible
**When** the token is successfully renewed via silent refresh
**Then** the banner disappears automatically

**Given** the token expiry banner is visible
**When** token renewal fails with `invalid_grant`
**Then** `AuthSessionMonitor` handles the signout (no duplicate handling by the banner)

## ~~AC-5: Idle Timeout~~ (DEFERRED)

Deferred by product decision: Klai targets long-lived sessions (like Claude, ChatGPT, Notion). Idle timeout is not appropriate for the current user base. May be revisited as an opt-in per-tenant feature for enterprise compliance (ISO 27001).

## ~~AC-6: Cross-Tab Idle Synchronization~~ (DEFERRED)

Deferred: depends on AC-5. Same rationale.

## Quality Gates

- [x] `npm run lint` passes with no new errors
- [x] `npm run build` compiles without TypeScript errors
- [x] No raw `console.log` usage -- all logging via `authLogger`
- [x] All user-facing strings in both `messages/en.json` and `messages/nl.json`
- [x] CI green: portal-frontend, SAST/Semgrep
- [ ] Browser-verified: cross-tab logout in Chrome/Brave
- [ ] Browser-verified: token expiry banner shows and auto-dismisses on renewal
