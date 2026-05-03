import { defineConfig, devices } from '@playwright/test'
import path from 'node:path'

/**
 * Production-tenant E2E config.
 *
 * Targets a dedicated e2e tenant on production (default
 * https://e2e.getklai.com — see docs/testing/test-suite-plan.md §7).
 * The bot logs in once via _lib/auth.ts and reuses storage-state
 * across journeys for speed.
 *
 * Required env:
 *   E2E_BASE_URL          e.g. https://e2e.getklai.com
 *   E2E_USER_EMAIL        e.g. e2e@getklai.com
 *   E2E_USER_PASSWORD     password for the e2e user
 *   E2E_TOTP_SECRET       Base32-encoded TOTP secret captured during MFA setup
 *
 * Run:
 *   cd klai-portal/frontend
 *   E2E_BASE_URL=... E2E_USER_EMAIL=... ... \
 *     npx playwright test -c e2e/prod-tenant/_config/playwright.prod.config.ts
 *
 * Or via npm script:
 *   npm run test:e2e:prod
 */

const requiredEnv = ['E2E_BASE_URL', 'E2E_USER_EMAIL', 'E2E_USER_PASSWORD', 'E2E_TOTP_SECRET'] as const

for (const k of requiredEnv) {
  if (!process.env[k]) {
    // Soft warning during config load; tests fail fast in _lib/auth.ts.
    console.warn(`[playwright.prod.config] missing env: ${k}`)
  }
}

const STORAGE_STATE = path.resolve(__dirname, 'storageState.json')

export default defineConfig({
  testDir: path.resolve(__dirname, '..'),
  testMatch: ['J*.spec.ts'],
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [
    ['list'],
    ['html', { outputFolder: 'playwright-report-prod-tenant', open: 'never' }],
    ['junit', { outputFile: 'playwright-report-prod-tenant/junit.xml' }],
  ],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'https://e2e.getklai.com',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    // J01 logs in and persists storage-state for the rest.
    {
      name: 'login',
      testMatch: ['J01-login.spec.ts'],
      use: {
        ...devices['Desktop Chrome'],
        storageState: undefined,
      },
    },
    // All other journeys depend on J01 having run.
    {
      name: 'authenticated-journeys',
      testIgnore: ['J01-login.spec.ts'],
      dependencies: ['login'],
      use: {
        ...devices['Desktop Chrome'],
        storageState: STORAGE_STATE,
      },
    },
  ],
})

export { STORAGE_STATE }
