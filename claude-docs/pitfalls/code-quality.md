# Code Quality Pitfalls

> Linting, type checking, formatting — CI quality gate failures.

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

## See Also

- [patterns/code-quality.md](../patterns/code-quality.md) - Per-project quality tooling reference
- [pitfalls/devops.md](devops.md) - Deployment and Docker mistakes
- [pitfalls/process.md](process.md) - AI dev workflow rules
