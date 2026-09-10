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

// Regression: the deployed 440x650 admin preview fills the frame with the
// chat window but keeps ChatBubble's collapse button painted over the send
// button (public floating layout offsets the window 88px above the bubble;
// previewGeometry does not). Loads the real dist bundle in a real iframe.
const previewConfig = {
  title: 'E2E preview', welcome_message: 'Hi', footer_text: 'Plan met [onze nerds](https://example.com).',
  css_variables: { '--klai-message-font-size': '15px', '--klai-message-line-height': '1.65' },
  chat_endpoint: 'https://widget.e2e/chat', session_token: 'e2e-preview-token',
  session_expires_at: '2030-01-01T00:00:00Z',
}

test('preview send button stays clickable while open and the header close leaves a launcher that reopens', async ({ page }) => {
  const bundle = readFileSync(new URL('../../../klai-widget/dist/klai-chat.js', import.meta.url), 'utf8')
  const frameHtml = '<!doctype html><html><body style="margin:0"><div id="klai-preview-host" style="height:100vh"></div><script src="/klai-chat.js" data-widget-id="e2e-preview" data-mode="preview"></script></body></html>'
  await page.route('https://widget.e2e/**', (route) => {
    const url = route.request().url()
    if (url.endsWith('.js')) return route.fulfill({ contentType: 'text/javascript', body: bundle })
    if (url.endsWith('/preview-frame')) return route.fulfill({ contentType: 'text/html', body: frameHtml })
    return route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><body style="margin:0"><iframe src="/preview-frame" width="440" height="650" style="border:0"></iframe></body></html>' })
  })
  await page.goto('https://widget.e2e/')
  const frame = page.frames().find((f) => f.url().endsWith('/preview-frame'))!
  await frame.waitForFunction('window.KlaiWidget !== undefined')
  await frame.evaluate((config) => (window as any).KlaiWidget.mountPreview(document.getElementById('klai-preview-host')!, {
    widgetId: 'e2e-preview', config, fetchConfig: async () => config,
  }), previewConfig)
  const chat = page.frameLocator('iframe')
  await expect(chat.locator('.klai-window')).toBeVisible()
  await expect(chat.locator('.klai-bubble')).toBeHidden()
  await chat.locator('.klai-textarea').fill('hello')
  // Before the fix this fails: the expanded bubble intercepts the hit target.
  await chat.locator('.klai-send-btn').click({ trial: true })
  await chat.locator('.klai-close-btn').click()
  await expect(chat.locator('.klai-bubble')).toBeVisible()
  await chat.locator('.klai-bubble').click()
  await expect(chat.locator('.klai-window')).toBeVisible()
})

// Regression: Voys/demo rendered .SF NS although live CSS says Geist, and
// tenant typography could not be configured. Opting in via
// --klai-font-family must load the locally bundled 'Klai Widget Geist' face
// through document.fonts (not merely the computed family) with zero external
// font requests, and the input/starter/message size variables must apply and
// clear. Runs the real dist bundle through mountPreview/updateConfig.
test('bundled Geist opt-in loads without external font requests and typography overrides apply and clear', async ({ page }) => {
  const bundle = readFileSync(new URL('../../../klai-widget/dist/klai-chat.js', import.meta.url), 'utf8')
  const fontRequests: string[] = []
  page.on('request', (r) => { if (/font/i.test(r.url())) fontRequests.push(r.url()) })
  const base = { ...previewConfig, css_variables: {}, conversation_starters: ['E2E starter'] }
  const opted = { ...base, css_variables: {
    '--klai-font-family': '"Klai Widget Geist", system-ui, sans-serif',
    '--klai-input-font-size': '16.5px', '--klai-starter-font-size': '14.3px', '--klai-message-font-size': '15.4px',
  } }
  const frameHtml = '<!doctype html><html><body style="margin:0"><div id="klai-preview-host" style="height:100vh"></div><script src="/klai-chat.js" data-widget-id="e2e-preview" data-mode="preview"></script></body></html>'
  await page.route('https://widget.e2e/**', (route) => {
    const url = route.request().url()
    if (url.endsWith('.js')) return route.fulfill({ contentType: 'text/javascript', body: bundle })
    if (url.endsWith('/preview-frame')) return route.fulfill({ contentType: 'text/html', body: frameHtml })
    return route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><body style="margin:0"><iframe src="/preview-frame" width="440" height="650" style="border:0"></iframe></body></html>' })
  })
  await page.goto('https://widget.e2e/')
  const frame = page.frames().find((f) => f.url().endsWith('/preview-frame'))!
  await frame.waitForFunction('window.KlaiWidget !== undefined')
  await frame.evaluate((config) => {
    ;(window as any).__preview = (window as any).KlaiWidget.mountPreview(document.getElementById('klai-preview-host')!, {
      widgetId: 'e2e-preview', config, fetchConfig: async () => config,
    })
  }, base)
  const chat = page.frameLocator('iframe')
  // Unset overrides keep the standard CSS: 14 / 12.5 / 14.
  await expect(chat.locator('.klai-textarea')).toHaveCSS('font-size', '14px')
  await expect(chat.locator('.klai-starter')).toHaveCSS('font-size', '12.5px')
  await expect(chat.locator('.klai-hero-title')).toHaveCSS('font-size', '14px')
  await frame.evaluate((config) => (window as any).__preview.updateConfig(config), opted)
  await expect(chat.locator('.klai-textarea')).toHaveCSS('font-size', '16.5px')
  await expect(chat.locator('.klai-starter')).toHaveCSS('font-size', '14.3px')
  await expect(chat.locator('.klai-hero-title')).toHaveCSS('font-size', '15.4px')
  await expect(chat.locator('.klai-hero-title')).toHaveCSS('font-family', /Klai Widget Geist/)
  const face = await frame.evaluate(() => document.fonts.load('16px "Klai Widget Geist"')
    .then((faces) => ({ count: faces.length, loaded: faces.every((f) => f.status === 'loaded') })))
  expect(face.count).toBeGreaterThan(0)
  expect(face.loaded).toBe(true)
  await frame.evaluate((config) => (window as any).__preview.updateConfig(config), base)
  await expect(chat.locator('.klai-textarea')).toHaveCSS('font-size', '14px')
  await expect(chat.locator('.klai-starter')).toHaveCSS('font-size', '12.5px')
  await expect(chat.locator('.klai-hero-title')).toHaveCSS('font-size', '14px')
  expect(fontRequests).toEqual([])
})
