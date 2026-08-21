/**
 * Widget appearance defaults.
 *
 * The widget's primary colour is tenant-configurable DATA, not portal
 * styling: it is persisted per widget and handed to an embedded surface that
 * does not share the portal's stylesheet, so a `var(--color-rl-accent)`
 * reference would not resolve there. It therefore has to be a literal.
 *
 * It only has to be a literal ONCE. Before this module the same literal sat
 * in four places (the chat surface default param, the create-widget form, the
 * appearance tab's initial state, and its dirty-check), which is the shape of
 * drift this repo has already paid for once with the `/kb-images/` path
 * literal. Import from here instead of retyping the hex.
 *
 * The value intentionally matches `--color-rl-accent` in `src/index.css`;
 * `widget-appearance.test.ts` asserts they stay in step.
 */
export const WIDGET_DEFAULT_PRIMARY_COLOR = '#fcaa2d'
