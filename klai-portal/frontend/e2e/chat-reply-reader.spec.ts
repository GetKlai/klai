/**
 * The reply reader J02 judges Strict with.
 *
 * On 2026-10-22 J02 failed five runs in a row on "Strict must not answer
 * TCP/IP from general knowledge" while the Strict reply was the fixed refusal
 * (portal-api ran no writer for any Strict turn in that window). The oracle
 * read the whole LibreChat frame, and the sidebar lists the conversation
 * titles the general model writes from the question. The markup below is the
 * class LibreChat itself gives an assistant message (`agent-turn`, a user
 * message `user-turn`), read from the client bundle of v0.8.7 and of the
 * pinned v0.8.8 image.
 */
import { test, expect } from '@playwright/test'
import { hasTcpExplanation, latestReplyText } from './prod-tenant/_lib/chat'

const REFUSAL =
  'Ik kan dit niet betrouwbaar beantwoorden op basis van de beschikbare kennisbronnen. ' +
  'Probeer het in Open-modus voor een antwoord op basis van algemene kennis.'

function chatPage(reply: string): string {
  return `
    <nav><a>Uitleg over netwerken</a><a>TCP/IP: Internet Protocol Suite</a></nav>
    <main>
      <div class="message-render"><div class="user-turn"><h2>Prompt 1:</h2><p>Wat is TCP/IP? Antwoord kort.</p></div></div>
      <div class="message-render"><div class="agent-turn"><h2>Response 2:</h2><div><p>${reply}</p></div></div></div>
    </main>`
}

test('a Strict refusal is not read as a general answer because of a conversation title', async ({ page }) => {
  await page.setContent(chatPage(REFUSAL))

  const reply = await latestReplyText(page.mainFrame())

  expect(reply).toContain('niet betrouwbaar beantwoorden')
  expect(hasTcpExplanation(reply)).toBe(false)
})

test('a general answer in the reply is still caught', async ({ page }) => {
  await page.setContent(chatPage('TCP/IP is de verzameling protocollen waarmee computers op internet communiceren.'))

  expect(hasTcpExplanation(await latestReplyText(page.mainFrame()))).toBe(true)
})

// From 2026-10-23 13:51 UTC J02 timed out on every Open turn although the
// footer was in the reply (run 39992711774): Klai's LibreChat entrypoint folds
// "Agent activiteit" into a closed <details> (klai-kb-disclosure), and
// innerText leaves out the body of a closed <details>.
test('the mode line in a folded Agent activiteit footer is read', async ({ page }) => {
  await page.setContent(
    chatPage(
      'TCP/IP is de verzameling protocollen waarmee computers op internet communiceren.</p>' +
        '<details class="klai-kb-disclosure klai-kb-disclosure--activity"><summary>' +
        '<span class="klai-kb-disclosure-title">Agent activiteit</span>' +
        '<span class="klai-kb-disclosure-count">3 stappen</span></summary>' +
        '<div class="klai-kb-disclosure-body"><ul><li>Modus: Open, kennisbank met fallback.</li>' +
        '<li>Antwoord: algemene kennis, niet uit je kennisbank.</li></ul></div></details><p>',
    ),
  )

  expect(await latestReplyText(page.mainFrame())).toContain('Modus: Open, kennisbank met fallback.')
})
