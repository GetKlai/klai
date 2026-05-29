---
paths: ["klai-portal/frontend/src/**/*.tsx", "klai-portal/frontend/src/**/*.ts"]
---

# Portal Frontend Engineering Rules

These rules are for the React/TanStack portal frontend only. They do not apply
to the Klai website or marketing pages.

Portal UI/UX patterns live in one canonical file:

`klai-portal/frontend/docs/ui-standards.md`

When changing portal UI, read that file first and follow an existing portal
screen with the same pattern. Keep this file focused on technical frontend
engineering rules: routing, code splitting, file organization, tests, and
known implementation pitfalls.

Shared brand DNA and tokens:

- `.claude/rules/klai/design/styleguide.md` is shared brand guidance.
- `.claude/rules/klai/design/tokens.md` is shared token/logo guidance.
- Website-specific patterns stay in `.claude/rules/klai/design/website-patterns.md`.
- Portal-specific UI patterns stay in `klai-portal/frontend/docs/ui-standards.md`.

Do not copy portal admin/app patterns into the website, and do not copy
website/landing-page patterns into the portal.

---

## Route code splitting

The portal uses TanStack Router's automatic route code splitting in
`vite.config.ts`. Treat this as the default performance pattern for all
new route work.

### Route file rules

- Keep route components unexported. Exporting a route component prevents
  TanStack's splitter from treating it as route-local implementation.
- Do not import directly from another route file. Move shared code into
  a `-`-prefixed helper at the smallest shared scope.
- Keep heavy UI dependencies inside the route/component that needs them;
  avoid importing editor, markdown, table, drag-and-drop, or picker
  libraries from app-wide layout files.
- Manual `.lazy.tsx` split files are still allowed when a route needs
  explicit control over the critical path.

### Manual split shape

Use this when a route has critical matching/loading logic plus heavy UI:

- `<route>.tsx`: `createFileRoute`, `validateSearch`, `beforeLoad`,
  `loader`, redirects, and tiny route constants.
- `<route>.lazy.tsx`: `createLazyFileRoute`, component, hooks, UI
  imports, icons, markdown/editor/table libraries, and page-local state.

In lazy files, do not import the critical route file's `Route`. Use the
lazy file's own `Route` object, or `getRouteApi('/path')` when typed
route hooks are needed without pulling critical config back into the
lazy chunk.

---

## File organization for shared types and helpers

When two or more route files share types, constants, or non-route helpers,
the location is decided by **smallest-shared scope**, not by convenience.

### Decision tree

1. **Used by one route file only?** → declare inline, or in a colocated
   `_components/` directory if it's a sub-component split.
2. **Used by 2+ route files within one directory?** → extract to a
   `-`-prefixed sibling file in that directory:
   `-<feature>-{types,helpers,hooks,query-keys,feedback}.{ts,tsx}`.
3. **Used by 2+ route files across sibling directories under one common
   parent?** → extract to a `-`-prefixed file in **that common parent**,
   not in either subdirectory.
4. **Used by 3+ unrelated areas (admin, app, setup, login)?** → it's
   app-wide infrastructure; goes in `@/lib/`. Examples already there:
   `apiFetch`, `auth`, `logger`, `locale`. Feature-specific types do
   NOT belong in `@/lib/`.

### Naming

- File: `-<feature>-{types,helpers,hooks,query-keys,feedback}.{ts,tsx}`.
  Pure type files use `.ts`. Files with JSX (components, render helpers)
  use `.tsx`.
- Directory: `_components/` for a directory of sub-components owned by
  one route. TanStack Router ignores both `-` prefix files and `_`
  prefix directories.
- Feature segment is kebab-case and matches the feature folder it sits
  in (e.g. `-bronnen-types.ts` next to `bronnen.tsx`).

### Anti-patterns

- **Cross-route imports.** `import { X } from './sibling-route'` where
  `sibling-route.tsx` is a route file is always a smell. Extract `X` to
  a `-`-prefixed sibling file at the smallest-shared scope. Tests
  importing from a route file have the same smell.

- **Cross-directory `-` imports.** `import from './sub/-foo'` from a
  file that lives one level above `sub/` means the helper is in the
  wrong directory. The helper should live at the parent level (one
  directory up). Existing instances are tactical legacy; new code
  must follow rule 3 above.

- **Feature types in `@/lib/`.** `@/lib/` is for app-wide infrastructure
  (API client, auth, logger). Feature-specific types pollute the
  namespace and lose the smallest-shared-scope signal. The exception is
  a small (< 30 lines) tactical helper used by exactly one feature
  (e.g. `lib/ms-docs.ts`); even then, prefer a `-`-prefixed sibling.

- **Duplicate definitions across files.** If `interface FooConfig`
  appears in more than one file with the same body, that's a bug
  waiting to happen. Extract per the decision tree, even if the
  current usage is "just two files".

### Why this matters

Feature-local ad-hoc choices have produced five different shared-helper
locations in this codebase (`-kb-helpers.tsx`, `-kb-types.ts`,
`-bronnen-helpers.tsx`, `admin/api-keys/-types.ts`, `admin/widgets/-types.ts`).
Each was a reasonable choice in isolation; together they make "where
does this type belong" an open question every time. The decision tree
above is the answer — apply it before adding a new shared file.

### Mechanical enforcement

The "cross-route imports" anti-pattern is enforced by the
`klai/no-cross-route-import` ESLint rule, defined in
`klai-portal/frontend/eslint-rules/no-cross-route-import.js` and wired
into `klai-portal/frontend/eslint.config.js`. It fires on any route
file (under `src/routes/`, basename without `-`/`_`/`._` markers) that
relatively imports from another route file. Allowed targets:
`-`-prefixed siblings, `_components/` directories,
`<route>._<feature>` colocation, `routeTree.gen`, and `__tests__/`.

The rule has a `vitest run eslint-rules/__tests__/no-cross-route-import.test.js`
suite covering 4 valid + 4 invalid + 5 edge cases. Re-run when extending
the heuristic.

The other anti-patterns above (cross-directory `-` imports, feature
types in `@/lib/`, duplicate definitions) are NOT yet mechanically
enforced — reviewers are the only gate. Future work could codify them
in additional ESLint rules or via the `klai-tenant-review` agent.

---

## Multi-step wizard password fields (MED)

Any wizard step containing a `type="password"` or secret input must be wrapped in a
`<form>` element, even if the step has no traditional submit button. Without it,
browsers emit a warning and autofill/password managers behave incorrectly.

```tsx
<form onSubmit={(e) => { e.preventDefault(); setStep('next') }}>
  <Input type="password" ... />
  <Button type="submit">Continue</Button>
</form>
```

**Rule:** Every wizard step with a secret field needs a `<form>` wrapper with `onSubmit` advancing to the next step.

---

## ID format change breaks length-guard redirect (HIGH)

When a URL scheme changes ID length or format (e.g. 8-char prefix → full 36-char UUID),
any existing `pid.length === 8` guard in redirect logic will silently stop firing.
The auto-redirect that upgrades old-format URLs to new-format URLs never triggers,
leaving users on broken or stale URLs with no visible error.

**Why:** The guard was written for the old format and was never updated when the ID scheme changed.

**Prevention:** When changing an ID length or format, search the entire codebase for all
`length === N`, `length < N`, or `startsWith(id)` checks that reference the old length or
prefix logic. Update or remove every guard before shipping.

---

## Verify exported handle interface before calling methods (MED)

When calling methods on a React `forwardRef` handle (e.g. `BlockPageEditorHandle`),
verify the exported interface in the source file before use. TypeScript will catch
mismatches in CI, but only if `tsc` is run — the dev server does not run type-checking.

**Why:** A handle ref exposes `getContent()`, but a caller written `getMarkdown()` — a method
that does not exist. The dev server hot-reloaded fine; CI's `tsc` caught it.

**Prevention:** Before pushing code that calls methods on a custom ref handle type, open the
source file and confirm the exact exported method names.

---

## TanStack Router routeTree.gen.ts must be committed (HIGH)

Adding a new file-based route (e.g. `src/routes/$locale/signup/social.tsx`) without regenerating and committing `routeTree.gen.ts` causes TypeScript errors in CI. CI uses the committed version — the local dev server auto-regenerates on save, so the problem is invisible locally.

**Why:** TanStack Router's file-based routing generates `routeTree.gen.ts` from the file tree. Devs see a working app; CI sees the old generated file and fails type-checking.

**Prevention:** After adding any new route file, run `npx @tanstack/router-cli generate` and commit `routeTree.gen.ts` before pushing.

---

## See Also

- Portal UI standards: `klai-portal/frontend/docs/ui-standards.md`
- Shared brand DNA: `.claude/rules/klai/design/styleguide.md`
- Shared tokens/logo: `.claude/rules/klai/design/tokens.md`
