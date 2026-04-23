import { defineConfig, devices } from '@playwright/test'

/**
 * Playwright E2E test configuration.
 * Requires a running dev-stack on http://localhost:5173.
 * Set PLAYWRIGHT_BASE_URL env var to override.
 *
 * Seed users (set via env vars or .env.test.local):
 *   E2E_CORE_USER_EMAIL / E2E_CORE_USER_PASSWORD    — core plan user
 *   E2E_COMPLETE_USER_EMAIL / E2E_COMPLETE_USER_PASSWORD  — complete plan user
 *   E2E_ADMIN_EMAIL / E2E_ADMIN_PASSWORD            — admin user
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: 'html',
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:5173',
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
