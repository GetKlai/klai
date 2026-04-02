# SPEC-UI-001: Acceptance Criteria

## R1: Remove Token from QueryKeys

**Given** a user is logged in and cached data exists
**When** the OIDC token silently refreshes
**Then** all cached queries remain valid (not refetched)
**And** the new token is used transparently in subsequent API calls

## R2: Global staleTime

**Given** a user visits a page that fetches data
**When** the user navigates away and back within 30 seconds
**Then** no new API request is made (cached data is served)

## R3: apiFetch Helper

**Given** a component needs to call `GET /api/groups`
**When** the developer uses `apiFetch<Group[]>('/api/groups', token)`
**Then** the response is typed as `Group[]`
**And** a non-ok response throws an `ApiError` with status code and detail

## R4: Lazy Load Heavy Routes

**Given** the application's initial bundle
**When** measured with `rollup-plugin-visualizer`
**Then** BlockNote, react-qr-code, and emoji-mart are NOT in the initial chunk
**And** they load on-demand when their routes are visited

## R6: Error States

**Given** the `/api/transcriptions` endpoint returns a 500 error
**When** the user visits the transcribe page
**Then** a `QueryErrorState` component is shown with a retry button
**And** the page does not show an infinite spinner or blank content

## R8: useCurrentUser Hook

**Given** a user navigates directly to `/app` (skipping `/callback`)
**When** the app checks authorization
**Then** `/api/me` is called via TanStack Query
**And** isAdmin, products are derived from the query result (not sessionStorage)

## Quality Gates

- ESLint: 0 errors
- TypeScript (tsc): 0 errors
- All existing functionality works in browser (manual verification)
- Bundle size of initial chunk decreases (measured via build output)
