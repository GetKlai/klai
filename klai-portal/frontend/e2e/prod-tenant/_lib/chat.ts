/**
 * Shared LibreChat-iframe helpers for prod-tenant e2e journeys.
 *
 * The chat UI is served from a `chat-*` subdomain and embedded via
 * iframe on `/app/chat`. Locating the live conversation frame (as
 * opposed to a stale/loading frame) requires polling for both the
 * iframe URL shape (`chat-` + `/c/`) and the prompt textarea being
 * mounted inside it.
 *
 * docs/testing/test-suite-plan.md §6 (J02, J04).
 */
import type { Frame, Page } from '@playwright/test'

/**
 * Navigate to /app/chat and return the live LibreChat conversation frame.
 *
 * On exit: the frame's `#prompt-textarea` is present and ready for input.
 */
export async function getChatFrame(page: Page): Promise<Frame> {
  await page.goto('/app/chat')
  await page.waitForSelector('iframe', { timeout: 30_000 })
  await page.waitForFunction(
    () => Array.from(document.querySelectorAll('iframe')).some((iframe) => iframe.src.includes('chat-')),
    null,
    { timeout: 30_000 },
  )

  for (let attempt = 0; attempt < 60; attempt += 1) {
    const frame = page
      .frames()
      .find((candidate) => candidate.url().includes('chat-') && candidate.url().includes('/c/'))
    if (frame && (await frame.locator('#prompt-textarea').count())) {
      return frame
    }
    await page.waitForTimeout(500)
  }

  throw new Error(`chat frame/input not found; frames=${page.frames().map((frame) => frame.url()).join(' | ')}`)
}
