/**
 * J04 - Chat message feedback (thumbs-up) reaches LibreChat's own route.
 *
 * SPEC-KB-015 forwards chat feedback to portal-api's
 * POST /internal/v1/kb-feedback for KB quality scoring. That forward is
 * fire-and-forget (REQ-KB-015-06): the browser never sees it and must not
 * assert on it here. What was never proven end-to-end is the FIRST half of
 * the chain - that a real click on the feedback control in the LibreChat
 * UI reaches LibreChat's own feedback route (the route whose handler the
 * portal-forward code was injected into, see
 * deploy/librechat/klai-entrypoint.sh SPEC-KB-015 block) and that the
 * route returns success. Everything downstream of that (env vars present,
 * forwarding code present, portal-api endpoint returns 201) was already
 * verified directly against production.
 *
 * LibreChat 0.8.7's feedback control is not a simple thumbs icon: clicking
 * the "Love this" / "Needs improvement" action opens a reason-tag popover
 * (role="dialog" with aria-labelled buttons like "Accurate and Reliable");
 * selecting a tag is what actually fires the PUT request. Discovered
 * against the real e2e-tenant UI (see PR description) - not guessed.
 *
 * Flow: ask a short question -> wait for the turn to fully complete (the
 * regenerate control only renders for a finished assistant message, so it
 * is a mode-agnostic completion signal - no dependency on Strict/Open
 * banner text and no kb-preference mutation) -> click "Love this" -> pick
 * a reason tag -> assert the PUT .../feedback response is 2xx and the
 * response body reflects rating "thumbsUp" -> assert the button's
 * aria-pressed flips to "true" so a silently-failing handler is caught.
 *
 * docs/testing/test-suite-plan.md §6 (J04).
 */
import { test, expect } from '@playwright/test'
import { getChatFrame } from './_lib/chat'

test('J04 - chat feedback click reaches LibreChat feedback route and marks active', async ({ page }) => {
  test.setTimeout(120_000)

  const frame = await getChatFrame(page)
  const prompt = `Zeg alleen "hallo" en niets anders. Prod smoke ${Date.now()}`

  await frame.locator('#prompt-textarea').fill(prompt)
  await frame.locator('#send-button').click()

  // Mode-agnostic completion signal: the regenerate control is only
  // rendered for a finished assistant turn (it cannot regenerate a
  // response that hasn't finished streaming yet).
  await frame
    .locator('[data-testid="regenerate-generation-button"]')
    .waitFor({ state: 'visible', timeout: 90_000 })

  const loveButton = frame.locator('button[title="Love this"]').last()
  await expect(loveButton, '"Love this" feedback control should render on the completed message').toBeVisible()
  await expect(loveButton, 'feedback should start unset').toHaveAttribute('aria-pressed', 'false')

  await loveButton.click()

  // Selecting "Love this" opens a reason-tag popover; the PUT to
  // LibreChat's own feedback route only fires once a tag is picked.
  const tagButton = frame.locator('[role="dialog"] button[aria-label]').first()
  await expect(tagButton, 'reason-tag popover should open after clicking "Love this"').toBeVisible({
    timeout: 10_000,
  })

  const feedbackResponse = frame.page().waitForResponse(
    (response) =>
      /\/api\/messages\/[^/]+\/[^/]+\/feedback$/.test(new URL(response.url()).pathname) &&
      response.request().method() === 'PUT',
    { timeout: 15_000 },
  )

  await tagButton.click()

  const response = await feedbackResponse
  expect(response.status(), `LibreChat feedback route must not error (got ${response.status()})`).toBeGreaterThanOrEqual(200)
  expect(response.status()).toBeLessThan(300)

  const body = await response.json()
  expect(body?.feedback?.rating, 'feedback response should reflect thumbsUp rating').toBe('thumbsUp')

  await expect(loveButton, 'active state should be reflected after a successful feedback submit').toHaveAttribute(
    'aria-pressed',
    'true',
  )
})
