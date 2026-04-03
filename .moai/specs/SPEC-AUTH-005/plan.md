# SPEC-AUTH-005: Implementation Plan

## Overview

Industry-standard OIDC session hardening for klai-portal frontend. All changes in `klai-portal/frontend/src/`.

## Development Mode

DDD (Domain-Driven Development) -- extending existing auth domain with minimal new files.

## Architecture

```
klai-portal/frontend/src/
├── lib/
│   └── auth.tsx              # MODIFY: add clearStaleState, cross-tab sync, token expiry events
├── hooks/
│   └── useIdleTimeout.ts     # NEW: activity tracking, idle timer, cross-tab sync
├── components/
│   └── SessionBanner.tsx     # NEW: token expiry warning + idle timeout warning banner
└── routes/
    └── app/
        └── route.tsx         # MODIFY: mount useIdleTimeout + SessionBanner inside auth guard
```

## Task Decomposition

### Task 1: Cross-Tab Logout Sync (R1) -- auth.tsx

**Files:** `src/lib/auth.tsx`

**Changes:**
- In `AuthSessionMonitor`, register `addUserSignedOut` event on the `UserManager`
- Access UserManager via `useAuth()` -- `react-oidc-context` exposes it as `auth.settings` or through the internal user manager reference
- Guard against re-entrant calls: check `auth.isAuthenticated` before calling `removeUser()`
- Cleanup event listener on unmount

**Pattern:**
```tsx
// Inside AuthSessionMonitor useEffect:
const mgr = /* get UserManager from auth context */
const handleSignedOut = () => {
  if (auth.isAuthenticated) {
    authLogger.info('Signed out in another tab')
    void auth.removeUser()
  }
}
mgr.events.addUserSignedOut(handleSignedOut)
return () => mgr.events.removeUserSignedOut(handleSignedOut)
```

**Verification:** Open 2 tabs, logout in tab 1, tab 2 redirects to /logged-out within 2s.

### Task 2: Stale State Cleanup (R2) -- auth.tsx

**Files:** `src/lib/auth.tsx`

**Changes:**
- In `AuthSessionMonitor` or a new `AuthStartupCleanup` component, call `clearStaleState()` once on mount
- Access UserManager from auth context

**Pattern:**
```tsx
// Once on mount:
useEffect(() => {
  const mgr = /* get UserManager */
  mgr.clearStaleState().catch((err) => {
    authLogger.warn('Failed to clear stale OIDC state', err)
  })
}, [])
```

**Verification:** Inspect localStorage before/after app load -- stale `oidc.` keys from incomplete flows are removed.

### Task 3: PKCE Documentation (R3) -- auth.tsx

**Files:** `src/lib/auth.tsx`

**Changes:**
- Add comment to `oidcConfig` documenting that PKCE is enabled by default

**Verification:** Code review only.

### Task 4: Token Expiry Banner (R4) -- auth.tsx + SessionBanner.tsx

**Files:** `src/lib/auth.tsx`, `src/components/SessionBanner.tsx`

**Changes in auth.tsx:**
- Add `accessTokenExpiringNotificationTimeInSeconds: 300` to `oidcConfig`
- Register `addAccessTokenExpiring` and `addUserLoaded` events
- Expose expiry state via React context or callback

**New file SessionBanner.tsx:**
- Non-blocking banner at top of page (below nav)
- Shows "Sessie verloopt binnenkort" with dismiss option
- Auto-dismisses when token is renewed (UserLoaded event)
- Also used for idle timeout warning (Task 5)

**Verification:** Set token TTL to 6 minutes in Zitadel, observe banner at 1 minute remaining.

### Task 5: Idle Timeout (R5) -- useIdleTimeout.ts

**Files:** `src/hooks/useIdleTimeout.ts` (new)

**Changes:**
- Track `mousemove`, `keydown`, `touchstart`, `scroll`, `click` events on `document`
- Debounce activity detection (30s interval)
- Two timeouts: warning at 25 min, logout at 30 min
- `removeUser()` for logout (consistent with AuthSessionMonitor)
- Return `{ isWarning, remainingSeconds, resetTimer }` for UI consumption

**Constants:**
```ts
const IDLE_TIMEOUT_MS = 30 * 60 * 1000      // 30 minutes
const IDLE_WARNING_MS = 25 * 60 * 1000      // 25 minutes (5 min before)
const ACTIVITY_DEBOUNCE_MS = 30 * 1000      // 30 seconds
```

**Verification:** Set timeout to 2 min for testing, verify warning appears at 1:30, logout at 2:00.

### Task 6: Cross-Tab Idle Sync (R6) -- useIdleTimeout.ts

**Files:** `src/hooks/useIdleTimeout.ts`

**Changes:**
- On activity: write `Date.now()` to `localStorage.setItem('klai_last_activity', ...)`
- Listen for `storage` event on `klai_last_activity` key
- When other tab writes activity, reset local timers
- Debounced write (30s) to minimize localStorage churn

**Verification:** Open 2 tabs, be active in tab 1, verify tab 2 timer resets. Be idle in both, verify both show warning simultaneously.

### Task 7: Mount in App Layout -- route.tsx

**Files:** `src/routes/app/route.tsx`

**Changes:**
- Import and mount `useIdleTimeout` hook
- Render `SessionBanner` component for both token expiry and idle warnings
- Only active when user is authenticated (inside auth guard)

**Verification:** Full flow: login, verify no banner, wait for idle warning, click "Stay logged in", verify reset.

## Implementation Order

1. Task 3 (PKCE docs) -- trivial, immediate
2. Task 2 (stale cleanup) -- low risk, immediate value
3. Task 1 (cross-tab logout) -- P0, enables R6
4. Task 4 (token expiry banner) -- creates SessionBanner component
5. Task 5 (idle timeout) -- core hook
6. Task 6 (cross-tab idle sync) -- extends Task 5
7. Task 7 (mount in layout) -- integration

## Quality Gates

- ESLint passes (`npm run lint`)
- TypeScript compiles (`npm run build`)
- No new `console.log` usage (use `authLogger`)
- All i18n strings through Paraglide (`messages/en.json`, `messages/nl.json`)
- Browser verification: cross-tab logout, idle warning, token expiry banner

## Estimated File Changes

| File | Action | LOC estimate |
|---|---|---|
| `src/lib/auth.tsx` | Modify | +40 lines |
| `src/hooks/useIdleTimeout.ts` | New | ~80 lines |
| `src/components/SessionBanner.tsx` | New | ~60 lines |
| `src/routes/app/route.tsx` | Modify | +10 lines |
| `messages/en.json` | Modify | +6 keys |
| `messages/nl.json` | Modify | +6 keys |
| **Total** | | ~200 lines new/modified |
