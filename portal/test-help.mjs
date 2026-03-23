import { chromium } from 'playwright'

const CHROMIUM = 'C:/Users/markv/AppData/Local/ms-playwright/chromium-1208/chrome-win64/chrome.exe'
const USERDATA = 'C:/Users/markv/.pw-klai-profile'

const browser = await chromium.launchPersistentContext(USERDATA, {
  headless: false,
  slowMo: 400,
  viewport: { width: 1400, height: 900 },
  executablePath: CHROMIUM,
})

const page = browser.pages()[0] || await browser.newPage()
page.on('console', msg => { if (msg.type() !== 'log') console.log(`[${msg.type()}] ${msg.text()}`) })

await page.goto('https://getklai.getklai.com/app/transcribe')

// Wait for login if needed
console.log('⏳ Waiting for login... (browser is open, log in if needed)')
try {
  await page.waitForURL('**/app/**', { timeout: 300000 })
  await page.goto('https://getklai.getklai.com/app/transcribe')
  await page.waitForURL('**/app/transcribe', { timeout: 10000 })
  console.log('✅ Logged in, on transcribe page')
} catch {
  console.log('❌ Login timeout')
  process.exit(1)
}

await page.waitForTimeout(1000)

// ── Test 1: Toggle button visual state ──────────────────────────────────────
console.log('\n── Test 1: Toggle visual state ──')

const helpBtn = page.locator('[data-help-id="help-button"]')

// Make sure help is OFF first
const initialPressed = await helpBtn.getAttribute('aria-pressed')
if (initialPressed === 'true') {
  await helpBtn.click()
  await page.waitForTimeout(500)
  console.log('  (disabled help first to start clean)')
}

const offStyles = await helpBtn.evaluate(el => {
  const s = window.getComputedStyle(el)
  return { bg: s.backgroundColor, outline: s.outline, border: s.border }
})
console.log('  OFF state:', offStyles)

await helpBtn.click()
await page.waitForTimeout(600)

const onStyles = await helpBtn.evaluate(el => {
  const s = window.getComputedStyle(el)
  return { bg: s.backgroundColor, outline: s.outline, border: s.border }
})
console.log('  ON  state:', onStyles)

const toggleClear = onStyles.outline !== offStyles.outline || onStyles.border !== offStyles.border
console.log(toggleClear ? '✅ Toggle is visually distinct' : '❌ Toggle looks the same on/off')

await page.screenshot({ path: 'test-01-toggle-on.png' })
console.log('  📷 test-01-toggle-on.png')

// ── Test 2: Intro card visible and z-index ───────────────────────────────────
console.log('\n── Test 2: Intro card z-index ──')

const introCard = page.locator('.fixed.rounded-xl').filter({ hasText: /Scribe|Transcrib/ }).first()
const introVisible = await introCard.isVisible()
console.log(introVisible ? '✅ Intro card is visible' : '❌ Intro card NOT visible')

if (introVisible) {
  const zIdx = await introCard.evaluate(el => window.getComputedStyle(el).zIndex)
  console.log(`  z-index: ${zIdx} (should be > 10000)`)
  console.log(parseInt(zIdx) > 10000 ? '✅ z-index above driver overlay' : '❌ z-index too low')
}

await page.screenshot({ path: 'test-02-intro-card.png' })
console.log('  📷 test-02-intro-card.png')

// ── Test 3: Driver popover z-index when hovering ─────────────────────────────
console.log('\n── Test 3: Driver popover z-index ──')

// Dismiss intro first to register hover listeners
const gotItBtn = page.locator('button', { hasText: /Begrepen|Got it/i })
if (await gotItBtn.isVisible()) {
  await gotItBtn.click()
  await page.waitForTimeout(400)
  console.log('  Dismissed intro')
}

// Hover over transcription list to trigger driver
const transcribeList = page.locator('[data-help-id="transcribe-list"]')
if (await transcribeList.isVisible()) {
  await transcribeList.hover()
  await page.waitForTimeout(800)

  const popover = page.locator('.driver-popover')
  const popoverVisible = await popover.isVisible()
  console.log(popoverVisible ? '✅ Driver popover appeared' : '❌ Driver popover NOT visible')

  if (popoverVisible) {
    const pzIdx = await popover.evaluate(el => window.getComputedStyle(el).zIndex)
    console.log(`  popover z-index: ${pzIdx} (should be > 10000)`)
    console.log(parseInt(pzIdx) > 10000 ? '✅ Popover above overlay' : '❌ Popover behind overlay')

    const overlay = page.locator('.driver-overlay')
    const overlayExists = await overlay.count() > 0
    if (overlayExists) {
      const ozIdx = await overlay.first().evaluate(el => window.getComputedStyle(el).zIndex)
      console.log(`  overlay z-index: ${ozIdx}`)
    }
  }

  await page.screenshot({ path: 'test-03-popover.png' })
  console.log('  📷 test-03-popover.png')
} else {
  console.log('  ⚠️  transcribe-list element not found')
}

console.log('\n✅ Tests done. Browser stays open — close manually.')
