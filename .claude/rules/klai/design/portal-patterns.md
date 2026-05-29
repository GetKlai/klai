---
paths:
  - "klai-portal/frontend/**"
  - "klai-portal/backend/app/templates/**"
  - "klai-portal/backend/app/static/**"
---

# Portal Patterns

Canonical portal UI/UX standards now live at:

`klai-portal/frontend/docs/ui-standards.md`

This file is intentionally a compatibility entrypoint for agents/rule loaders
that still read `.claude/rules/klai/design/portal-patterns.md`.

If you change portal UI, read the canonical file first. If this file and the
canonical file ever disagree, the canonical file wins and this file should be
updated or kept as this pointer.

Minimum non-negotiables:

- Use existing admin/app screens as the reference pattern before creating UI.
- Admin detail pages use separate routes and a header-right `Button variant="ghost" size="sm"` back action.
- Do not add drawers, sheets, or inline detail panels for admin entity flows.
- Use underline tabs with URL search state where navigation needs to preserve the active tab.
- Use `klai-hover` for interactive rows/lists/sidebar items.
- Use semantic CSS tokens for success/warning/destructive states.
- Use Paraglide for all user-visible strings.
- Do not use `window.confirm`.
