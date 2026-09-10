import { readFileSync } from 'node:fs'
import { test, expect } from '@playwright/test'

const css = readFileSync(new URL('../../../klai-widget/src/styles/widget.css', import.meta.url), 'utf8')

test('floating widget overrides fit a small screen while inline widgets retain their container height', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 600 })
  await page.setContent('<div id="floating"></div><div id="inline" style="width:200px;height:800px"></div>')
  await page.evaluate((styles) => {
    for (const id of ['floating', 'inline']) {
      const host = document.getElementById(id)!
      host.style.setProperty('--klai-window-width', '420px')
      host.style.setProperty('--klai-window-height', '640px')
      const root = host.attachShadow({ mode: 'open' })
      const style = document.createElement('style')
      style.textContent = styles
      const window = document.createElement('div')
      window.className = `klai-window${id === 'inline' ? ' klai-window--inline' : ''}`
      root.append(style, window)
    }
  }, css)
  const floating = await page.locator('#floating .klai-window').boundingBox()
  expect(floating!.x).toBeGreaterThanOrEqual(0)
  expect(floating!.y).toBeGreaterThanOrEqual(0)
  expect(floating!.x + floating!.width).toBeLessThanOrEqual(390)
  expect(floating!.y + floating!.height).toBeLessThanOrEqual(600)
  expect((await page.locator('#inline .klai-window').boundingBox())!.height).toBe(800)
})
