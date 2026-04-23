import type { Page } from '@playwright/test'

const BASE = process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:5173'

export const CORE_USER = {
  email: process.env.E2E_CORE_USER_EMAIL ?? 'core@klai.test',
  password: process.env.E2E_CORE_USER_PASSWORD ?? 'test-password',
}

export const COMPLETE_USER = {
  email: process.env.E2E_COMPLETE_USER_EMAIL ?? 'complete@klai.test',
  password: process.env.E2E_COMPLETE_USER_PASSWORD ?? 'test-password',
}

export const ADMIN_USER = {
  email: process.env.E2E_ADMIN_EMAIL ?? 'admin@klai.test',
  password: process.env.E2E_ADMIN_PASSWORD ?? 'test-password',
}

/**
 * Log in via the OIDC flow and land on /app.
 */
export async function loginAs(page: Page, user: { email: string; password: string }) {
  await page.goto(`${BASE}/app`)
  // The portal redirects unauthenticated users to the OIDC login page.
  await page.waitForURL('**/oidc/**', { timeout: 10_000 })
  await page.fill('input[name="email"], input[type="email"]', user.email)
  await page.fill('input[name="password"], input[type="password"]', user.password)
  await page.click('button[type="submit"]')
  await page.waitForURL('**/app**', { timeout: 15_000 })
}
