# SPEC-AUTH-005: Progress

## Status: COMPLETED

## Implementation Summary

| Requirement | Status | Commit |
|---|---|---|
| R1: Cross-Tab Logout Sync | Done | `90fe5f5`, refactored in `f326fa3` |
| R2: Stale State Cleanup | Done | `90fe5f5`, refactored in `f326fa3` |
| R3: PKCE Documentation | Done | `90fe5f5` |
| R4: Token Expiry Banner | Done | `90fe5f5`, refactored in `f326fa3` |
| R5: Idle Timeout | DEFERRED | Product decision: long-lived sessions |
| R6: Cross-Tab Idle Sync | DEFERRED | Depends on R5 |

## Files Changed

| File | Action | Description |
|---|---|---|
| `frontend/src/lib/auth.tsx` | Modified | Three lifecycle hooks: `useSentryUserSync`, `useStaleStateCleanup`, `useSessionGuard` |
| `frontend/src/components/SessionBanner.tsx` | New | Token expiry warning banner with `useTokenExpiring` hook |
| `frontend/src/routes/app/route.tsx` | Modified | Mount `<SessionBanner />` in app layout |
| `frontend/messages/en.json` | Modified | +1 key: `session_token_expiring` |
| `frontend/messages/nl.json` | Modified | +1 key: `session_token_expiring` |

## Architecture Decision

R5/R6 (idle timeout) deferred by explicit product decision: Klai targets long-lived sessions like Claude, ChatGPT, and Notion. Idle timeout frustrates power users reading documents. May be revisited as opt-in per-tenant for enterprise compliance (ISO 27001).

## Quality Evidence

- ESLint: clean
- TypeScript build: clean
- CI: portal-frontend + SAST/Semgrep green
- Adversarial self-check: no bugs found in re-entrant guards, cleanup patterns, race conditions
