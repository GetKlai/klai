---
paths:
  - "**/*.tsx"
  - "**/*.ts"
  - "**/*.css"
---
# Frontend Pitfalls

> React, TanStack, TypeScript, and UI architecture mistakes in klai-portal.

## Index
> Keep this index in sync — add a row when adding an entry below.

| Entry | Sev | Rule |
|---|---|---|
| [frontend-route-guard-async-race](#frontend-route-guard-async-race) | CRIT | Route guards must wait for async user data before redirecting |
| [frontend-erasable-syntax-only](#frontend-erasable-syntax-only) | MED | No TS parameter properties when erasableSyntaxOnly is enabled |
| [frontend-bulk-migration-import-collateral](#frontend-bulk-migration-import-collateral) | HIGH | Verify remaining usages before removing an import during bulk migrations |

---

## frontend-route-guard-async-race

**Severity:** CRIT

**Trigger:** Writing a route guard (admin check, product gate, role check) that reads user data from a `useQuery` hook or any async source

When user data comes from an async source (e.g. `useCurrentUser` backed by TanStack Query), the data is `undefined` while the query is loading. If the guard evaluates `isAdmin` or `hasProduct` before the query resolves, it reads `undefined` as `false` and redirects the user away — even when they have the correct permissions.

**Why it happens:**
Route components render immediately. `useQuery` returns `{ data: undefined, isPending: true }` on first render. A guard like `if (!user?.is_superuser) redirect('/app')` fires on that first render, before the query has a chance to return the actual user object.

**Wrong:**
```tsx
function AdminRoute() {
  const { user } = useCurrentUser()
  // BUG: user is undefined while loading — redirects admin users
  if (!user?.is_superuser) {
    return <Navigate to="/app" />
  }
  return <Outlet />
}
```

**Correct:**
```tsx
function AdminRoute() {
  const { user, isPending } = useCurrentUser()
  // Gate: wait for data before making any redirect decision
  if (isPending) return <LoadingSpinner />
  if (!user?.is_superuser) {
    return <Navigate to="/app" />
  }
  return <Outlet />
}
```

**Rule:** ANY route guard that reads async user data must check `isPending`/`isLoading` and return a loading state before evaluating permissions.

**Prevention:**
1. Search for `Navigate` or `redirect` in route files — every one that depends on user data needs a loading gate
2. After migrating from sync (sessionStorage) to async (useQuery) user data, audit ALL guards
3. Test by throttling the network — if the redirect fires before data loads, the guard is broken

**Seen in:** SPEC-UI-001 R8 — migrating admin/route.tsx from sessionStorage to `useCurrentUser` caused admin users to be redirected to /app. Same issue appeared in app layout nav rendering (products flashed empty).

---

## frontend-erasable-syntax-only

**Severity:** MED

**Trigger:** Writing a class with constructor parameter properties (`constructor(public x: number)`) in a project with `erasableSyntaxOnly` enabled

TypeScript's `erasableSyntaxOnly` mode (enabled in this project's tsconfig) requires all TypeScript-specific syntax to be erasable — meaning it can be removed to produce valid JavaScript. Parameter properties (`public`, `protected`, `private`, `readonly` in constructor parameters) generate JavaScript code, so they are not allowed.

**Wrong:**
```ts
// Error: parameter properties not allowed with erasableSyntaxOnly
class ApiError extends Error {
  constructor(public status: number, public statusText: string, message: string) {
    super(message)
  }
}
```

**Correct:**
```ts
class ApiError extends Error {
  status: number
  statusText: string
  constructor(status: number, statusText: string, message: string) {
    super(message)
    this.status = status
    this.statusText = statusText
  }
}
```

**Rule:** With `erasableSyntaxOnly`, always use explicit class fields + manual constructor assignments instead of parameter properties.

**Seen in:** SPEC-UI-001 R1 — `ApiError` class in `lib/apiFetch.ts`.

---

## frontend-bulk-migration-import-collateral

**Severity:** HIGH

**Trigger:** Doing a bulk find-and-replace or migration across many files (e.g., replacing manual fetch with a centralized helper, removing a deprecated API)

When migrating a pattern across many files, removing the old import (e.g., `STORAGE_KEYS`) because the primary usage is gone can break other usages of that same import in the same file. The agent or developer focuses on the migration target and misses that the import served multiple purposes.

**Why it happens:**
During bulk operations (42 files in SPEC-UI-001), the focus is on the repetitive transformation. When an import like `STORAGE_KEYS` was used for both `accessToken` (being migrated away) and `sidebarCollapsed` (still needed), removing the import entirely breaks the sidebar.

**Prevention:**
1. Before removing any import during a bulk migration, grep the file for all usages of that import — not just the one being migrated
2. After completing a bulk migration, run `tsc --noEmit` to catch broken imports immediately
3. For large migrations (>10 files), do a final `grep -r "IMPORT_NAME"` across all changed files to verify no collateral damage

**Rule:** When removing an import during bulk migration, verify all usages in the file — not just the one being replaced.

**Seen in:** SPEC-UI-001 R1 — `STORAGE_KEYS` import was removed from Sidebar.tsx during the apiFetch migration, but `sidebarCollapsed` still needed it.

---
