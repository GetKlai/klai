---
name: klai-portal-ui
description: >
  Klai portal UI conventions. Mandatory for any agent editing
  klai-portal/frontend/ UI. Delegates layout, admin/app patterns,
  component usage, colors, and i18n to the canonical portal UI standards.
license: Apache-2.0
user-invocable: false
metadata:
  version: "1.1.0"
  category: "domain"
  status: "active"
  updated: "2026-05-29"
  tags: "klai, portal, frontend, ui, react, typescript"
---

# Klai Portal UI Conventions

This skill is portal-only. It does not apply to `klai-website/`, marketing
pages, landing pages, or public web storytelling. Portal and web are separate
UI worlds.

## Canonical Source

Before editing portal UI, read:

`klai-portal/frontend/docs/ui-standards.md`

That file wins over older examples, skills, or rule files. If another portal
document disagrees with it, update the other document in the same change.

## Required Workflow

1. Read `klai-portal/frontend/docs/ui-standards.md`.
2. Find an existing portal screen with the same pattern.
3. State the reference screen before editing.
4. Follow the existing portal pattern before introducing anything new.
5. Keep website/marketing patterns out of the portal, and portal admin/app
   patterns out of the website.

## Non-Negotiables

- Admin detail/edit flows use separate routes, not drawers, sheets, or inline
  row-expanded detail panels.
- Back/cancel actions live in the page header on the right as
  `Button variant="ghost" size="sm"`.
- Lists/tables use the existing portal table/list pattern and `klai-hover`.
- All user-visible strings go through Paraglide messages.
- Form fields use `components/ui/` primitives and pair every field with
  `Label`.
- Semantic states use CSS tokens such as `var(--color-success)`,
  `var(--color-warning)`, and `var(--color-destructive)`.
- Do not use `window.confirm`.

## Related References

- `klai-portal/frontend/docs/ui-standards.md` — portal UI/UX source of truth.
- `.claude/rules/klai/projects/portal-frontend.md` — portal frontend
  engineering rules, routing, file organization, and implementation pitfalls.
- `.claude/rules/klai/design/tokens.md` — shared tokens and canonical logo
  guidance.
- `.claude/rules/klai/design/styleguide.md` — shared brand DNA.
