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

/**
 * How many conversation starters a widget may carry.
 *
 * The backend is the authority — `WidgetConfig.conversation_starters` is
 * `Field(max_length=3)` in `admin_widgets.py`. This constant mirrors it for
 * the places that have to agree with it AND with each other: the number of
 * starter input fields the appearance form (and the create wizard) render,
 * and the live preview's parsing of the same unsaved draft. When those
 * disagree the preview shows a different set of starters than the form is
 * about to save, which is the one thing a preview must never do.
 *
 * Was 6 until short, vague starter chips were found to work against the new
 * clarify flow: a visitor's own concrete question retrieves better than a
 * generic one-liner, so fewer, better starters replaced more, vaguer ones.
 */
export const WIDGET_MAX_CONVERSATION_STARTERS = 3

/**
 * Saved starters -> the fixed-length draft the numbered input fields hold.
 *
 * The form always renders exactly `WIDGET_MAX_CONVERSATION_STARTERS` fields,
 * so a widget with fewer saved starters pads with blanks and one saved with
 * more (a widget from before the cap dropped from 6) is truncated on load,
 * matching what a save from this form would produce.
 */
export function toStarterFields(starters: string[] | undefined): string[] {
  const fields = (starters ?? []).slice(0, WIDGET_MAX_CONVERSATION_STARTERS)
  while (fields.length < WIDGET_MAX_CONVERSATION_STARTERS) fields.push('')
  return fields
}

/** Draft fields -> the list that gets saved: trimmed, blanks dropped, order kept. */
export function starterFieldsToList(fields: string[]): string[] {
  return fields.map((field) => field.trim()).filter(Boolean)
}
