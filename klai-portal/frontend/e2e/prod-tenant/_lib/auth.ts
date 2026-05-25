/**
 * Auth helpers for prod-tenant e2e.
 *
 * J01-login.spec.ts uses these to log in once and persist storage-state.
 * J02..J11 import the persisted state via Playwright project deps.
 *
 * docs/testing/test-suite-plan.md §4.2 + §4.3.
 */
import { authenticator } from 'otplib'
import type { Page } from '@playwright/test'
import { expect } from '@playwright/test'

function requireEnv(name: string): string {
  const v = process.env[name]
  if (!v) {
    throw new Error(
      `[e2e/prod-tenant] missing required env var: ${name}. ` +
        `Set E2E_USER_EMAIL, E2E_USER_PASSWORD, E2E_TOTP_SECRET, E2E_BASE_URL ` +
        `before running. See docs/testing/test-suite-plan.md §7.`,
    )
  }
  return v
}

/**
 * Log in as the e2e bot via email + password + TOTP.
 *
 * On entry: page may be on any URL.
 * On exit: page is on /app/* (chat or default app landing).
 *
 * Throws if any step times out or returns the wrong URL.
 */
export async function loginAsE2EBot(page: Page): Promise<void> {
  const email = requireEnv('E2E_USER_EMAIL')
  const password = requireEnv('E2E_USER_PASSWORD')
  const totpSecret = requireEnv('E2E_TOTP_SECRET')

  // Always start from the canonical login page so redirects don't leak
  // half-finished auth state from prior runs.
  await page.goto('/login')

  // Wait for the email input - explicit selector defensively guards
  // against the form taking a tick to mount.
  await page.waitForSelector('input[type="email"]', { timeout: 10_000 })
  await page.fill('input[type="email"]', email)
  await page.fill('input[type="password"]', password)

  // Submit the password form. Selector is intentionally broad; the
  // exact button label depends on locale (NL "Inloggen" / EN "Sign in").
  await page.locator('button[type="submit"]').first().click()

  // TOTP step. The form may not always appear (e.g. if MFA is disabled
  // - which we explicitly do NOT want for the e2e bot, see plan §1).
  // Wait up to 10s; failure here is a hard fail.
  await page.waitForSelector('input[name="totp"], input[autocomplete="one-time-code"]', {
    timeout: 10_000,
  })

  const code = authenticator.generate(totpSecret)
  // Try both common selectors for the OTP input.
  const totpInput = page.locator('input[name="totp"], input[autocomplete="one-time-code"]').first()
  await totpInput.fill(code)

  // Submit TOTP. Same broad selector as above.
  await page.locator('button[type="submit"]').first().click()

  // Land on /app/*. Allow up to 15s for any post-login redirects.
  await page.waitForURL(/\/app(\/|$)/, { timeout: 15_000 })

  // Sanity-check that auth actually took. /api/me should return 200.
  // We do this via fetch within the page context so the auth cookie is included.
  const me = await page.evaluate(async () => {
    const r = await fetch('/api/me', { credentials: 'include' })
    return { status: r.status, ok: r.ok }
  })
  expect(me.ok, '/api/me should return 200 after successful login').toBe(true)
}

/**
 * Persist the browser context's auth state (cookies + localStorage)
 * to disk for J02..J11 to reuse via the playwright projects' storageState.
 */
export async function persistAuthState(page: Page, path: string): Promise<void> {
  await page.context().storageState({ path })
}

/**
 * Log out by clicking the logout entry. Used by J10.
 *
 * Defensive: tolerates either a "Logout" button in the user menu, or a
 * direct navigation to /api/auth/logout if the menu element is not found.
 */
export async function logoutE2EBot(page: Page): Promise<void> {
  // Try clicking a user-menu-driven logout first.
  const menuButton = page.locator('[data-testid="user-menu"], [aria-label*="user" i]').first()
  if (await menuButton.count()) {
    await menuButton.click()
    const logoutItem = page
      .locator('button, a')
      .filter({ hasText: /uitloggen|sign out|log out|logout/i })
      .first()
    if (await logoutItem.count()) {
      await logoutItem.click()
      await page.waitForURL(/\/(logged-out|login)/, { timeout: 10_000 })
      return
    }
  }

  // Fallback: navigate the logout endpoint directly.
  await page.goto('/api/auth/logout')
  await page.waitForURL(/\/(logged-out|login)/, { timeout: 10_000 })
}
