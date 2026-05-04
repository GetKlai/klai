/**
 * One-time capture of a Voys-tenant browser session for `voys-attached`
 * E2E mode.
 *
 * Opens a headed Chromium pointing at $E2E_BASE_URL (default
 * https://voys.getklai.com), waits for the user to log in via Google SSO,
 * and writes the resulting storage-state to
 * `_config/storageState.voys.json` (gitignored).
 *
 * Run:
 *   cd klai-portal/frontend
 *   npm run e2e:capture-session
 *
 * docs/testing/test-suite-plan.md §4 + §7.
 */
import { chromium } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const STORAGE_PATH = path.resolve(__dirname, '..', '_config', 'storageState.voys.json')

async function main() {
  const baseUrl = process.env.E2E_BASE_URL ?? 'https://voys.getklai.com'

  console.log(`[capture] launching Chromium at ${baseUrl}`)
  console.log('[capture] log in via Google SSO; the script auto-saves once')
  console.log('[capture] you reach a /app/* URL (and continues running so')
  console.log('[capture] you can verify before closing).')
  console.log('')

  const browser = await chromium.launch({ headless: false })
  const context = await browser.newContext()
  const page = await context.newPage()

  await page.goto(baseUrl)

  // Wait until the user reaches /app/*. 5 minutes should be plenty
  // for the Google-SSO + tenant-resolve roundtrip.
  await page.waitForURL(/\/app(\/|$)/, { timeout: 5 * 60 * 1000 })

  console.log(`[capture] reached ${page.url()} — saving storage-state`)
  await context.storageState({ path: STORAGE_PATH })
  console.log(`[capture] wrote ${STORAGE_PATH}`)
  console.log('[capture] you can close the browser when done verifying.')

  // Don't auto-close — user might want to poke around to confirm.
  // Wait until they close the page manually.
  await page.waitForEvent('close', { timeout: 0 }).catch(() => {})
  await browser.close()
}

main().catch((err) => {
  console.error('[capture] failed:', err)
  process.exit(1)
})
