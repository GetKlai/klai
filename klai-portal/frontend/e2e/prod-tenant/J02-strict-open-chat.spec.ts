/**
 * J02 - Strict/Open chat contract smoke.
 *
 * Validates the production path that previously regressed:
 * changing /api/app/account/kb-preference must be visible to the next
 * LibreChat/LiteLLM turn, and Strict must not answer a no-KB TCP/IP question
 * from general model knowledge.
 */
import { test, expect, type Page } from '@playwright/test'
import { getChatFrame } from './_lib/chat'

test.skip(process.env.E2E_MODE === 'voys-attached', 'Strict/Open smoke mutates the current chat user preference')

type KBPreference = {
  kb_narrow: boolean
  kb_pref_version: number
}

function hasTcpExplanation(text: string): boolean {
  return (
    /TCP\/IP\s+(is|staat|vormt|bestaat|verwijst|betekent)/i.test(text) ||
    /Transmission Control Protocol/i.test(text) ||
    /Internet Protocol/i.test(text)
  )
}

async function patchMode(page: Page, kbNarrow: boolean): Promise<KBPreference> {
  if (page.url() === 'about:blank') {
    await page.goto('/app')
  }

  const result = await page.evaluate(async (strict) => {
    const csrf =
      document.cookie
        .split(';')
        .find((cookie) => cookie.trimStart().startsWith('__Secure-klai_csrf='))
        ?.trimStart()
        .split('=')
        .slice(1)
        .join('=') || ''

    const response = await fetch('/api/app/account/kb-preference', {
      method: 'PATCH',
      credentials: 'include',
      headers: {
        'content-type': 'application/json',
        ...(csrf ? { 'X-CSRF-Token': csrf } : {}),
      },
      body: JSON.stringify({ kb_narrow: strict }),
    })
    return {
      ok: response.ok,
      status: response.status,
      body: await response.json().catch(() => null),
    }
  }, kbNarrow)

  expect(result.ok, `PATCH kb-preference should accept kb_narrow=${kbNarrow}`).toBe(true)
  expect(result.body?.kb_narrow).toBe(kbNarrow)
  return result.body as KBPreference
}

async function askChat(page: Page, prompt: string): Promise<string> {
  const frame = await getChatFrame(page)
  await frame.locator('#prompt-textarea').fill(prompt)
  await frame.locator('#send-button').click()
  await frame.waitForFunction(
    () => {
      const text = document.body.innerText
      return (
        text.includes('Modus: Open, kennisbank met fallback.') ||
        text.includes('Modus: Strict, alleen kennisbank.') ||
        text.includes('niet betrouwbaar beantwoorden') ||
        text.includes('cannot answer this reliably')
      )
    },
    null,
    { timeout: 90_000 },
  )
  await page.waitForTimeout(5_000)
  return frame.evaluate(() => document.body.innerText)
}

test('J02 - Strict/Open mode reaches LiteLLM chat response without stale mode', async ({ page }) => {
  test.setTimeout(180_000)

  const prompt = `Wat is TCP/IP? Antwoord kort. Prod smoke ${Date.now()}`

  try {
    const openPref = await patchMode(page, false)
    const openText = await askChat(page, prompt)

    expect(openPref.kb_narrow).toBe(false)
    expect(openText).toContain('Modus: Open, kennisbank met fallback.')
    expect(openText).not.toContain('Modus: Strict, alleen kennisbank.')
    expect(hasTcpExplanation(openText), 'Open may answer TCP/IP from general knowledge').toBe(true)
    expect(openText).not.toContain('**Bronnen**')

    const strictPref = await patchMode(page, true)
    const strictText = await askChat(page, prompt)

    expect(strictPref.kb_narrow).toBe(true)
    expect(strictPref.kb_pref_version).toBeGreaterThan(openPref.kb_pref_version)
    expect(strictText).toContain('Modus: Strict, alleen kennisbank.')
    expect(strictText).not.toContain('Modus: Open, kennisbank met fallback.')
    expect(strictText).toMatch(/niet betrouwbaar beantwoorden|cannot answer this reliably/i)
    expect(hasTcpExplanation(strictText), 'Strict must not answer TCP/IP from general knowledge').toBe(false)
    expect(strictText).not.toContain('**Bronnen**')
  } finally {
    await patchMode(page, false)
  }
})
