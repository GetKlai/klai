---
name: klai-portal-ui
description: Build, change, or review user interfaces in klai-portal/frontend. Use for React route pages, shared UI components, design-token changes, layouts, forms, tables, navigation, accessibility, responsive behavior, and visual consistency in the Klai portal.
---

# Klai Portal UI

Treat the implementation and the path-scoped design rules as the source of truth. Do not revive old purple, serif-heading, or raw model-name conventions.

## Read the applicable references

Before editing, read:

1. `.claude/rules/klai/design/tokens.md` for shared tokens, fonts, logos, and accessibility constraints.
2. `.claude/rules/klai/design/styleguide.md` for shared brand principles.
3. `.claude/rules/klai/design/portal-patterns.md` for the current portal-specific overrides and component patterns.
4. `.claude/rules/klai/projects/portal-frontend.md` for frontend architecture, i18n, routing, and behavioral pitfalls.
5. `klai-portal/docs/ui-components.md` when selecting or extending shared components.

Verify any disputed rule against `klai-portal/frontend/src/index.css`, the relevant component in `src/components/ui/`, and nearby current pages. Update code and its owning rule together when intentional design changes make a rule stale.

## Workflow

1. Inspect the nearest comparable route or component and reuse its layout and interaction pattern.
2. Prefer existing `src/components/ui/` primitives. Use a native element when semantics or an established low-level component require it; do not wrap elements mechanically.
3. Route all user-facing strings through Paraglide. Add both Dutch and English messages.
4. Use CSS variables for themeable or semantic color and typography. Follow `portal-patterns.md` for the deliberate v1 grayscale overrides.
5. Preserve keyboard access, focus visibility, labels, loading states, empty states, and error feedback.
6. Check narrow and wide layouts when changing page structure.
7. Run the smallest relevant frontend tests, then typecheck and lint for the touched package.

## Guardrails

- Never introduce the retired purple palette or `font-serif` heading policy.
- Never copy long token tables or component examples into this skill; fix the owning design rule instead.
- Keep product-runtime LiteLLM policy in `.claude/rules/klai/platform/litellm.md`; it is not a UI convention.
- Do not replace an established interaction solely to make markup uniform.
- Do not hardcode translatable UI copy.
