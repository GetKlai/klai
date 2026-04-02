---
id: SPEC-UI-001
version: "1.0.0"
status: draft
created: 2026-04-02
updated: 2026-04-02
author: MoAI
priority: P1
---

## HISTORY

| Date | Version | Change |
|------|---------|--------|
| 2026-04-02 | 1.0.0 | Initial draft from code review findings |

# SPEC-UI-001: Frontend Architecture Improvements

## Overview

Address frontend architecture issues discovered during comprehensive code review of klai-portal/frontend. Focuses on data layer consistency, performance, and developer experience.

## Requirements (EARS Format)

### R1: Remove Token from QueryKeys (HIGH)

**When** a TanStack Query `useQuery` call is defined, **the system shall** exclude the auth token from the `queryKey` array, **so that** token refresh does not invalidate the entire query cache.

**Rationale:** ~90 queries use `queryKey: ['entity', token]`. Token refresh invalidates all cached data.

### R2: Configure Global staleTime (HIGH)

**When** the `QueryClient` is instantiated in `main.tsx`, **the system shall** set `defaultOptions.queries.staleTime` to `30_000` (30 seconds).

**Rationale:** Default `staleTime: 0` causes unnecessary refetches on every navigation.

**File:** `frontend/src/main.tsx`

### R3: Create apiFetch Helper (HIGH)

**When** any component needs to make an authenticated API call, **the system shall** use a shared `apiFetch<T>()` helper from `lib/apiFetch.ts` that handles Bearer token injection, error checking, and typed JSON parsing.

**Rationale:** The `fetch + Bearer token + res.ok check + res.json()` pattern is duplicated ~90 times across 37 files.

### R4: Lazy Load Heavy Routes (MEDIUM-HIGH)

**When** the application initializes, **the system shall** lazy-load the following routes using TanStack Router's `lazy()`:
- `docs/$kbSlug` (BlockNote editor)
- `setup/mfa` (QR code + passkey)
- `admin/billing` (payment forms)
- `focus/$notebookId` (chat + sources)

**Rationale:** No code splitting exists. BlockNote, emoji-mart, react-qr-code are all in the initial bundle.

### R5: Split God Components (MEDIUM)

**When** a route component exceeds 500 LOC, **the system shall** extract logical sub-components.

**Targets:**
- `transcribe/index.tsx` (731 LOC) → extract `TranscriptionRow`
- `setup/mfa.tsx` (719 LOC) → split into `TotpSetup`, `PasskeySetup`, `EmailMfaSetup`
- `admin/billing.tsx` (700 LOC) → split into `BillingStatus`, `MandateForm`, `PlanSelector`

### R6: Add Error States to useQuery Consumers (MEDIUM)

**When** a `useQuery` call returns an error state, **the system shall** display a `QueryErrorState` component instead of a blank page.

**Rationale:** ~55 of ~90 useQuery calls have no error UI. Failed requests show infinite spinners or empty pages.

### R7: Move Hardcoded Strings to Paraglide (MEDIUM)

**When** displaying user-facing text, **the system shall** use Paraglide message functions instead of hardcoded Dutch/English strings.

**Known hardcoded strings:**
- `$kbSlug.tsx`: "Koppelen aan pagina", "Zoek een pagina...", "Geen andere pagina's gevonden."
- `billing.tsx`: "Voorbeeldstraat 1", "Amsterdam" (placeholders)
- `groups/index.tsx`: "Team naam..." (placeholder)
- Error messages in mutations (~30 instances)

### R8: Replace sessionStorage Auth with useQuery (MEDIUM)

**When** checking user authorization (isAdmin, products), **the system shall** use a `useCurrentUser()` hook backed by `useQuery('/api/me')` with `staleTime: 300_000`, replacing direct `sessionStorage` reads.

**Rationale:** sessionStorage auth is fragile — stale when callback is skipped, not revalidated.

### R9: Fix Accessibility Issues (MEDIUM)

**The system shall:**
- Add `role="navigation"` and `aria-label` to the sidebar `<aside>`
- Add focus trap and Escape key handler to the wikilink picker modal
- Use `role="radiogroup"` + `role="radio"` for billing cycle toggle buttons

### R10: Remove Dead Dependencies (LOW)

**The system shall** remove `next-themes` from `package.json` (Next.js-specific, unused in Vite project).

**Also verify:** `@emoji-mart/data`, `@emoji-mart/react` — confirm if used, remove or lazy-load if not.

## Constraints

- No breaking changes to existing routes or URL structure
- All Paraglide keys must have both NL and EN translations
- Lazy loading must not break TanStack Router's type-safe routing
- R1 (token removal from queryKeys) must be tested with token refresh scenario

## Acceptance Criteria

See `acceptance.md` for Given/When/Then scenarios.
