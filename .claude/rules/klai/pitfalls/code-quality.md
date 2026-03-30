---
paths:
  - "**/*.py"
  - "**/*.ts"
  - "**/*.tsx"
  - "**/pyproject.toml"
  - "**/eslint.config*"
---
# Code Quality Pitfalls

> Linting, type checking, formatting — CI quality gate failures.

## Index
> Keep this index in sync — add a row when adding an entry below.

| Entry | Sev | Rule |
|---|---|---|
| [cq-pyright-noqa-mismatch](#cq-pyright-noqa-mismatch) | HIGH | Use `__all__` not `# noqa` when both ruff and pyright are in CI |
| [cq-eslint-react-refresh-tanstack-routes](#cq-eslint-react-refresh-tanstack-routes) | MED | Disable `react-refresh/only-export-components` for `src/routes/**` in TanStack Router projects |

---

## cq-pyright-noqa-mismatch

**Severity:** HIGH

**Trigger:** Using `# noqa: F401` to suppress unused-import warnings in a file that is also checked by pyright

`# noqa: F401` is a ruff/flake8 directive. Pyright does not read `noqa` comments. If pyright is enabled (as it is in the klai-portal CI pipeline), it will still report `reportUnusedImport` for the same import, even though ruff is happy.

**What happened:** During SPEC-KB-012, `models/__init__.py` re-exported taxonomy models for Alembic auto-detection. Adding `# noqa: F401` suppressed the ruff I001/F401 warnings but pyright still flagged the imports as unused. It took an additional CI iteration to discover the fix.

**Why it happens:**
ruff and pyright are independent tools with separate configuration. ruff uses inline `# noqa` directives; pyright uses its own `# pyright: ignore[...]` or `# type: ignore[...]` comments. They do not share suppression mechanisms.

**Prevention:**
1. For `__init__.py` re-exports, use `__all__` to explicitly declare public API. This satisfies both ruff and pyright:
   ```python
   from app.models.taxonomy import PortalTaxonomyNode, PortalTaxonomyProposal

   __all__ = ["PortalTaxonomyNode", "PortalTaxonomyProposal"]
   ```
2. Never rely on `# noqa` alone when both ruff and pyright are in the CI pipeline
3. If `__all__` is not appropriate, use both suppressions:
   ```python
   from app.models.taxonomy import PortalTaxonomyNode  # noqa: F401  # pyright: ignore[reportUnusedImport]
   ```

**Rule:** When suppressing an import warning, check which tool is flagging it. `# noqa` is for ruff; `__all__` or `# pyright: ignore` is for pyright. Prefer `__all__` because it solves both at once.

**See also:** `patterns/code-quality.md` -- ruff + pyright configuration per project

---

## cq-eslint-react-refresh-tanstack-routes

**Severity:** MED

**Trigger:** Adding a new TanStack Router route file (`src/routes/**/*.tsx`) and seeing ESLint errors about `only-export-components`

`eslint-plugin-react-refresh` reports a false positive on TanStack Router route files. A route file exports `Route` (a non-component constant created by `createFileRoute`) alongside local component functions. The plugin's `only-export-components` rule does not understand this pattern and flags the export, even when `allowConstantExport: true` is set.

**What went wrong:**
The `react-refresh/only-export-components` rule was failing in CI for `vitals.ts` (a lib file that also exports non-components). The fix was to extend the existing `src/routes/**` exception to also cover `src/lib/locale.tsx`.

**Why it happens:**
The plugin's purpose is to warn when a non-component export in a file might break Fast Refresh. For route files this is a false positive — Fast Refresh works fine because the component is registered via `Route.component`, not via a direct export. The plugin has no knowledge of TanStack Router's registration model.

**Prevention:**
The exception is already in `frontend/eslint.config.js` — do not remove it:
```js
// src/components/ui/ and src/routes/ are intentional exceptions
{
  files: ['src/components/ui/**/*.{ts,tsx}', 'src/lib/locale.tsx', 'src/routes/**/*.{ts,tsx}'],
  rules: {
    'react-refresh/only-export-components': 'off',
  },
},
```

If a new file in `src/lib/` exports non-component values alongside React components and the lint rule fires, add it to this glob pattern. Do not try to restructure the file to satisfy the rule — the exception is the correct fix.

**Rule:** `react-refresh/only-export-components` must be disabled for `src/routes/**` in any TanStack Router project. Do not attempt workarounds (re-exporting, barrel files) — they add complexity for no gain.

**See also:** `patterns/code-quality.md#klai-portalfrontend`

---

## See Also

- [patterns/code-quality.md](../patterns/code-quality.md) - Per-project quality tooling reference
- [pitfalls/devops.md](devops.md) - Deployment and Docker mistakes
- [pitfalls/process.md](../../../../docs/pitfalls/process.md) - AI dev workflow rules
