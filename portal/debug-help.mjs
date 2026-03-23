import { chromium } from 'playwright'

const browser = await chromium.launch({ headless: false, slowMo: 500 })
const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } })
const page = await ctx.newPage()

page.on('console', msg => console.log(`[${msg.type()}] ${msg.text()}`))
page.on('pageerror', err => console.error('[pageerror]', err.message))

await page.goto('https://getklai.getklai.com/app/transcribe')
console.log('>>> Log in, then press Enter in this terminal...')
await new Promise(r => process.stdin.once('data', r))

// Check current z-index state
const zCheck = await page.evaluate(() => {
  const els = document.querySelectorAll('[data-help-id]')
  return Array.from(els).map(el => ({
    id: el.getAttribute('data-help-id'),
    zIndex: window.getComputedStyle(el).zIndex,
    position: window.getComputedStyle(el).position,
  }))
})
console.log('data-help-id elements:', JSON.stringify(zCheck, null, 2))

// Enable help by clicking the ? button
const helpBtn = page.locator('[data-help-id="help-button"]')
await helpBtn.click()
console.log('Help toggled ON')
await page.waitForTimeout(500)

await page.screenshot({ path: 'debug-01-after-enable.png', fullPage: false })
console.log('Screenshot: debug-01-after-enable.png')

// Check if intro card is visible
const introVisible = await page.locator('text=Scribe').first().isVisible()
console.log('Intro card visible:', introVisible)

// Dismiss intro
const gotItBtn = page.locator('text=Begrepen')
if (await gotItBtn.isVisible()) {
  await gotItBtn.click()
  console.log('Dismissed intro')
}
await page.waitForTimeout(300)

// Hover over first data-help-id element (not help-button)
const target = page.locator('[data-help-id="transcribe-list"]').first()
if (await target.isVisible()) {
  await target.hover()
  console.log('Hovering transcribe-list')
  await page.waitForTimeout(800)
  await page.screenshot({ path: 'debug-02-hover.png', fullPage: false })
  console.log('Screenshot: debug-02-hover.png')

  // Check overlay and z-indexes during hover
  const hoverCheck = await page.evaluate(() => {
    const overlay = document.querySelector('.driver-overlay')
    const helpEls = document.querySelectorAll('[data-help-id]')
    const driverActive = document.body.classList.contains('driver-active')
    return {
      driverActive,
      overlayZIndex: overlay ? window.getComputedStyle(overlay).zIndex : 'none',
      elements: Array.from(helpEls).map(el => ({
        id: el.getAttribute('data-help-id'),
        zIndex: window.getComputedStyle(el).zIndex,
        position: window.getComputedStyle(el).position,
      }))
    }
  })
  console.log('Hover state:', JSON.stringify(hoverCheck, null, 2))
} else {
  console.log('transcribe-list element NOT found in DOM')
  const allIds = await page.evaluate(() =>
    Array.from(document.querySelectorAll('[data-help-id]')).map(e => e.getAttribute('data-help-id'))
  )
  console.log('All data-help-id elements found:', allIds)
}

console.log('\n>>> Done. Browser stays open. Close it manually when done.')
