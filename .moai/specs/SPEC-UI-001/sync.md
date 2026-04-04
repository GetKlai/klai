# SPEC-UI-001: Sync Report

## Status: COMPLETED

**Completed:** 2026-04-02
**Implementation commits:**
- `205a86c` — refactor(frontend): introduce apiFetch utility and clean up API calls (R3 groundwork)
- `2bf4069` — refactor(frontend): implement SPEC-UI-001 frontend architecture improvements (R1-R10)
- `a3d1ab7` — fix(frontend): wait for useCurrentUser before route guard redirects (R8 bugfix)
- `d48c918` — fix(frontend): remove dead sessionStorage writes and fix nav flicker (R8 cleanup)

## Requirements Completion

| Req | Description | Status | Notes |
|-----|-------------|--------|-------|
| R1 | Remove token from queryKeys | Done | Token removed from all ~90 useQuery calls |
| R2 | Global staleTime 30s | Done | Set in QueryClient defaults |
| R3 | apiFetch helper | Done | `lib/apiFetch.ts` — centralizes Bearer token, error handling, typed JSON |
| R4 | Lazy load heavy routes | Done | 4 routes: `$kbSlug`, `mfa`, `billing`, `$notebookId` via `.lazy.tsx` |
| R5 | Split god components | Done | `transcribe/index.tsx` -> TranscriptionTable; `mfa.tsx` -> TOTPSetup/PasskeySetup/EmailOTPSetup/MethodCard; `billing.tsx` -> BillingActiveView/BillingSetupView/BillingStatusViews |
| R6 | QueryErrorState component | Done | `components/ui/query-error-state.tsx` |
| R7 | Paraglide i18n strings | Done | Hardcoded NL/EN strings moved to `messages/en.json` and `messages/nl.json` |
| R8 | useCurrentUser hook | Done | `hooks/useCurrentUser.ts` replaces sessionStorage auth + follow-up fixes for pending state |
| R9 | Accessibility fixes | Done | Sidebar aria-label, focus improvements |
| R10 | Remove dead deps | Done | next-themes removed |

## Files Changed (35 files, +4431/-3129 lines)

### New files
- `src/lib/apiFetch.ts` (pre-existing from 205a86c)
- `src/components/ui/query-error-state.tsx`
- `src/hooks/useCurrentUser.ts`
- `src/routes/admin/_billing-types.ts`
- `src/routes/admin/_components/BillingActiveView.tsx`
- `src/routes/admin/_components/BillingSetupView.tsx`
- `src/routes/admin/_components/BillingStatusViews.tsx`
- `src/routes/admin/billing.lazy.tsx`
- `src/routes/app/docs/$kbSlug.lazy.tsx`
- `src/routes/app/focus/$notebookId.lazy.tsx`
- `src/routes/app/transcribe/_components/TranscriptionTable.tsx`
- `src/routes/app/transcribe/_types.ts`
- `src/routes/setup/_components/EmailOTPSetup.tsx`
- `src/routes/setup/_components/MethodCard.tsx`
- `src/routes/setup/_components/PasskeySetup.tsx`
- `src/routes/setup/_components/TOTPSetup.tsx`
- `src/routes/setup/mfa.lazy.tsx`

### Key patterns introduced
- **Lazy routes via `.lazy.tsx`**: TanStack Router code splitting pattern — keep route definition in `.tsx`, move component to `.lazy.tsx`
- **useCurrentUser hook**: Single source of truth for auth state, replaces fragile sessionStorage reads
- **apiFetch helper**: Centralized API call pattern with typed responses and error handling
- **QueryErrorState**: Reusable error display component for useQuery consumers

## Follow-up Issues Found

1. **Route guard race condition** (fixed in a3d1ab7): `useCurrentUser` returns `undefined` while loading, causing admin route guard to redirect prematurely. Fix: check `isPending` before evaluating `isAdmin`.

2. **Dead sessionStorage writes** (fixed in d48c918): After migrating to `useCurrentUser`, callback.tsx still wrote auth state to sessionStorage. Cleaned up dead writes and added `userLoading` guard to prevent nav flicker.

## Bundle Impact

Initial bundle: 998kB -> 976kB (reported in commit message). BlockNote, react-qr-code, and emoji-mart moved to lazy-loaded chunks.

## Learnings

1. **useCurrentUser pending state**: When replacing synchronous state (sessionStorage) with async state (useQuery), every consumer must handle the loading state. Route guards that assumed synchronous isAdmin checks broke silently.

2. **Lazy route pattern for TanStack Router**: The `.lazy.tsx` convention works cleanly but requires keeping the route definition (path, loader, etc.) in the original `.tsx` file. Only the component moves to `.lazy.tsx`.

3. **God component extraction**: Shared types between parent and extracted children should go in a `_types.ts` file at the route level (e.g., `transcribe/_types.ts`, `admin/_billing-types.ts`).
