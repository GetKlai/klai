---
paths:
  - "**/*.ts"
  - "**/*.tsx"
  - "**/*.css"
---
# TypeScript / React Rules

## Tooling (ESLint + tsc)
- ESLint 9 flat config with `typescript-eslint recommendedTypeChecked`.
- `no-console` is an error (except `warn`/`error`). Use the project's tagged logger, never `console.log`.
- Run `tsc --noEmit` after refactors to catch broken types immediately.
- `react-refresh/only-export-components` is disabled for `src/routes/**` in TanStack Router projects (false positive on `Route` export).

## erasableSyntaxOnly
Projects with `erasableSyntaxOnly` in tsconfig forbid TS parameter properties (`constructor(public x: number)`). Use explicit class fields + manual assignment instead.

## Styling rules
- Use Tailwind `className` for all fixed styling. `style={{}}` only for truly runtime-dynamic values.
- Semantic states (error, success, warning) MUST use CSS token vars (`var(--color-destructive)`), never raw Tailwind (`text-red-600`).
- Purely decorative colors (avatar backgrounds) may use raw Tailwind.

## Bulk migrations
- Before removing any import during a bulk migration, grep the file for ALL usages — not just the one being replaced.
- After bulk migrations (>10 files), run `tsc --noEmit` + `npm run lint` to catch collateral damage.

## Route guards (TanStack Query + Router)
- ANY route guard reading async user data must check `isPending` and return a loading state before evaluating permissions.
- Without this gate, `undefined` on first render causes false redirects.

## UI verification
- After fixing any frontend bug, click through the actual browser flow before reporting done.
- Use Playwright MCP (`browser_navigate`, `browser_click`, `browser_snapshot`) — available in every session.
- Code reading and "looks correct" score zero for UI bugfixes.

## Component structure
- Route component owns data fetching + page layout. Extract sub-components at ~50 lines JSX.
- Business logic unrelated to rendering belongs in custom hooks (`useXxx.ts`).
- Data fetching: inline `queryFn` in `useQuery`/`useMutation`. No service layer abstraction.
