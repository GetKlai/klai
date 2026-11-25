/**
 * Screen check of a freshly built chat image, before merge.
 *
 * The production journeys (e2e/prod-tenant, J02 and J04) find the message box,
 * the send button, the finished answer, regenerate and the feedback control
 * by the selectors in prod-tenant/_lib/chat-selectors.ts. This check uses the
 * same module against the image a pull request builds, booted by
 * deploy/tests/chat-config-boot.sh with local login and a stand-in model
 * (deploy/tests/chat-model-stub.js), so a selector upstream removes fails here
 * instead of on every tenant: on 2026-11-24 v0.8.9 dropped the send button's
 * element id and only the production journeys, run after all 45 tenants had
 * moved, noticed.
 *
 * What it cannot see: OpenID login, the portal iframe, Klai's knowledge tools
 * and real model output. The production journeys still prove those, on the
 * canary tenant first (chat-image-rollout.yml).
 */
import { expect, test } from '@playwright/test'
import { CHAT } from '../prod-tenant/_lib/chat-selectors'

const STUB_REPLY = 'Hallo van het testmodel.'
const user = {
  name: 'Screen Check',
  username: 'screencheck',
  email: 'screen-check@example.com',
  password: 'screen-check-password-1',
}

test('a message is sent, answered, and can be rated through the journeys selectors', async ({ page, request }) => {
  const registered = await request.post('/api/auth/register', {
    data: { ...user, confirm_password: user.password },
  })
  expect(registered.status(), await registered.text()).toBeLessThan(300)

  await page.goto('/login')
  await page.locator('input[name="email"]').fill(user.email)
  await page.locator('input[name="password"]').fill(user.password)
  await page.locator('button[type="submit"]').click()

  const prompt = page.locator(CHAT.promptTextarea)
  await expect(prompt, 'message box').toBeVisible({ timeout: 30_000 })
  await prompt.fill('Zeg alleen hallo.')

  const send = page.locator(CHAT.sendButton)
  await expect(send, 'send button').toBeEnabled()
  await send.click()

  await expect(page.locator(CHAT.assistantTurn).last(), 'the answer renders').toContainText(STUB_REPLY, {
    timeout: 60_000,
  })
  await expect(page.locator(CHAT.regenerateButton), 'regenerate on the finished answer').toBeVisible({
    timeout: 30_000,
  })

  const love = page.getByRole('button', { name: CHAT.loveButtonName, exact: true }).last()
  await expect(love, 'feedback control').toHaveAttribute('aria-pressed', 'false')
  await love.click()
  const tag = page.locator(CHAT.feedbackTagButton).first()
  await expect(tag, 'reason-tag popover').toBeVisible()
  const feedback = page.waitForResponse(
    (response) => CHAT.feedbackRoute.test(new URL(response.url()).pathname) && response.request().method() === 'PUT',
  )
  await tag.click()
  expect((await feedback).status(), 'LibreChat feedback route').toBeLessThan(300)
  await expect(love).toHaveAttribute('aria-pressed', 'true')
})
