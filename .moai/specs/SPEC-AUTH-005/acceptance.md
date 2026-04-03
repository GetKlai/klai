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

## AC-5: Idle Timeout

**Given** a user is logged in and has been inactive for 25 minutes
**When** the 25-minute threshold is reached
**Then** a warning banner appears showing "Je sessie verloopt over 5 minuten wegens inactiviteit"
**And** a countdown timer shows remaining seconds
**And** a "Blijf ingelogd" button is visible

**Given** the idle warning banner is visible
**When** the user clicks "Blijf ingelogd"
**Then** the idle timer resets to 30 minutes
**And** the warning banner disappears

**Given** the idle warning banner is visible
**When** the user performs any activity (mouse, keyboard, touch)
**Then** the idle timer resets to 30 minutes
**And** the warning banner disappears

**Given** a user has been inactive for 30 minutes with no interaction
**When** the 30-minute timeout fires
**Then** the user is signed out via `removeUser()`
**And** the user is redirected to `/logged-out`

**Given** a user is on the `/login` or `/logged-out` page
**Then** the idle timeout timer does NOT run

## AC-6: Cross-Tab Idle Synchronization

**Given** a user has two tabs open and is active in tab 1
**When** tab 1 detects user activity
**Then** tab 2's idle timer also resets (via localStorage `klai_last_activity` key)
**And** tab 2 does NOT show the idle warning

**Given** a user has two tabs open and is idle in both for 25 minutes
**When** the warning threshold is reached
**Then** both tabs show the idle warning simultaneously (within 1 second of each other)

**Given** a user has two tabs open and the idle timeout fires
**When** tab 1 signs out due to inactivity
**Then** tab 2 also signs out within 2 seconds (via cross-tab logout sync from R1)

## Quality Gates

- [ ] `npm run lint` passes with no new errors
- [ ] `npm run build` compiles without TypeScript errors
- [ ] No raw `console.log` usage -- all logging via `authLogger`
- [ ] All user-facing strings in both `messages/en.json` and `messages/nl.json`
- [ ] Browser-verified: cross-tab logout in Chrome/Brave
- [ ] Browser-verified: idle warning appears and "Blijf ingelogd" resets timer
- [ ] Browser-verified: token expiry banner shows and auto-dismisses on renewal
