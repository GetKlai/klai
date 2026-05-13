/**
 * J01 — Login + TOTP, persist storage-state for J02..J11.
 *
 * Runs ONLY in `isolated-tenant` mode (E2E_MODE default). The
 * playwright config skips this spec in `voys-attached` mode because
 * the storage-state is captured manually via `npm run e2e:capture-session`.
 *
 * Verifies the full auth chain: Zitadel email+password → TOTP step →
 * portal-api session cookie → /app/* landing → /api/me 200. The
 * persisted storage-state is consumed by all downstream journeys
 * via the `authenticated-journeys` project's `dependencies: ['login']`.
 *
 * docs/testing/test-suite-plan.md §6 (J01).
 */
import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import { loginAsE2EBot, persistAuthState } from './_lib/auth'
import { STORAGE_STATE } from './_config/playwright.prod.config'

test('J01 — login + TOTP and persist storage-state', async ({ page }) => {
  await loginAsE2EBot(page)

  await expect(page).toHaveURL(/\/app(\/|$)/)

  await persistAuthState(page, STORAGE_STATE)

  expect(fs.existsSync(STORAGE_STATE), `storageState should exist at ${STORAGE_STATE}`).toBe(true)
})
