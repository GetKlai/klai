# Klai Portal Frontend Instructions

Before changing UI in this directory, read:

`klai-portal/frontend/docs/ui-standards.md`

Hard rules:

- Follow an existing screen with the same pattern before introducing new UI.
- Admin detail pages use separate routes and header-right back actions.
- Do not introduce drawers/sheets/inline detail panels for admin entity work.
- Use Paraglide for all user-visible strings.
- Use `components/ui/` form controls and pair every field with `Label`.
- Use semantic CSS tokens for success/warning/destructive states.
- Use `klai-hover` for interactive row/sidebar/list hover states.
- Do not use `window.confirm`.
