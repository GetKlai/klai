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

export function hasTcpExplanation(text: string): boolean {
  return (
    /TCP\/IP\s+(is|staat|vormt|bestaat|verwijst|betekent)/i.test(text) ||
    /Transmission Control Protocol/i.test(text) ||
    /Internet Protocol/i.test(text)
  )
}

/**
 * Text of the latest assistant message only.
 *
 * The whole frame also holds the sidebar, whose conversation titles the
 * general model writes from the question ("TCP/IP: Internet Protocol Suite"),
 * so a Strict refusal read with the page around it failed J02 on 2026-10-22.
 * `agent-turn` is the class LibreChat v0.8.7 and v0.8.8 give an assistant
 * message (`user-turn` for the employee's own).
 */
export function latestReplyText(frame: Frame): Promise<string> {
  return frame.evaluate(() => {
    const replies = document.querySelectorAll<HTMLElement>('.agent-turn')
    return replies.length ? replies[replies.length - 1].innerText : ''
  })
}
