/**
 * J03 - Account language preference save round-trip.
 *
 * Regression test for the 2026-08-12 "Save failed" report: PATCH
 * /api/me/language 500'd for every user because the handler committed a
 * portal_users UPDATE without tenant context, tripping the Category-A
 * strict WITH CHECK RLS policy (fixed in PR #850 by calling set_tenant
 * before the mutating commit). Unit tests pin the set_tenant-before-commit
 * ordering; this journey proves the full chain (SPA → BFF → portal-api →
 * RLS'd UPDATE) against the real stack.
 *
 * Flow: open /app/account → flip the language select → Save → assert the
 * success state (and no failure state) → flip back and save again so the
 * bot account ends in its original state.
 */
import { test, expect } from '@playwright/test'

test('J03 - account language save succeeds and round-trips', async ({ page }) => {
  await page.goto('/app/account')

  const select = page.locator('select').filter({ has: page.locator('option[value="en"]') }).first()
  await expect(select, 'language select should render on /app/account').toBeVisible({
    timeout: 15_000,
  })

  const original = await select.inputValue()
  const flipped = original === 'en' ? 'nl' : 'en'

  const saveLanguage = async (value: string) => {
    await select.selectOption(value)
    // The save button sits in the same settings block as the select.
    const saveButton = page
      .locator('button', { hasText: /^(Save|Opslaan)$/ })
      .first()
    const patchDone = page.waitForResponse(
      (r) => r.url().includes('/api/me/language') && r.request().method() === 'PATCH',
      { timeout: 15_000 },
    )
    await saveButton.click()
    const resp = await patchDone
    expect(resp.status(), `PATCH /api/me/language must not error (got ${resp.status()})`).toBe(200)
    await expect(
      page.getByText(/Save failed|Opslaan mislukt/),
      'no failure feedback may appear',
    ).toHaveCount(0)
  }

  await saveLanguage(flipped)
  // Restore the bot account to its original preference.
  await saveLanguage(original)
})
