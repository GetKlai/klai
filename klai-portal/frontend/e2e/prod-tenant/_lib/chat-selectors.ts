/**
 * The LibreChat controls the production journeys depend on, in one place.
 *
 * Imported by the prod-tenant journeys (J02, J04, chat.ts) and by the screen
 * check that runs against a freshly built chat image before merge
 * (e2e/chat-image/screen.check.ts), so the two cannot drift. On 2026-11-24
 * upstream v0.8.9 dropped the element id the journeys used for the send
 * button, and nothing noticed until every tenant already ran it.
 */
export const CHAT = {
  promptTextarea: '#prompt-textarea',
  sendButton: '[data-testid="send-button"]',
  // The class LibreChat gives an assistant message (`user-turn` for the
  // employee's own).
  assistantTurn: '.agent-turn',
  // Rendered only for a finished assistant turn, so it doubles as the
  // completion signal.
  regenerateButton: '[data-testid="regenerate-generation-button"]',
  // By accessible name: from v0.8.8 the pressed state carries aria-label and
  // no title.
  loveButtonName: 'Love this',
  // Picking a reason tag in the popover is what sends the feedback.
  feedbackTagButton: '[role="dialog"] button[aria-label]',
  feedbackRoute: /\/api\/messages\/[^/]+\/[^/]+\/feedback$/,
} as const
