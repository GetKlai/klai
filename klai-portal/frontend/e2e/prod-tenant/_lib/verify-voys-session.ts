/**
 * Verify the gitignored Voys attached-session storage state.
 *
 * This is the guard agents should run before claiming that Voys/real-user
 * testing is available. It does not print cookies or user details; it only
 * proves that the saved browser state reaches /app and that /api/me returns 200.
 */
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const storageState = path.resolve(__dirname, '..', '_config', 'storageState.voys.json')
const baseUrl = process.env.E2E_BASE_URL ?? 'https://voys.getklai.com'
const headed = process.env.PLAYWRIGHT_HEADED === '1'
const channel = process.env.PLAYWRIGHT_BROWSER_CHANNEL || 'chrome'

if (!fs.existsSync(storageState)) {
  throw new Error(
    `[voys-session] missing ${storageState}. ` +
      `Run "npm run e2e:capture-session" and log in via Google SSO once.`,
  )
}

const browser = await chromium.launch({
  headless: !headed,
  channel,
})
const context = await browser.newContext({ storageState })
const page = await context.newPage()

try {
  await page.goto(new URL('/app', baseUrl).toString(), { waitUntil: 'domcontentloaded' })
  await page.waitForURL(/\/app(\/|$)/, { timeout: 15_000 })

  const me = await page.evaluate(async () => {
    const r = await fetch('/api/me', { credentials: 'include' })
    return { status: r.status, ok: r.ok }
  })

  if (!me.ok) {
    throw new Error(`[voys-session] /api/me returned ${me.status}`)
  }

  console.log(
    JSON.stringify({
      ok: true,
      mode: 'voys-attached',
      baseUrl,
      url: page.url(),
      apiMeStatus: me.status,
      storageState,
    }),
  )
} finally {
  await browser.close()
}
