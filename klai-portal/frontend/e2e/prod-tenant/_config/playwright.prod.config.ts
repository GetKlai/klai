import { defineConfig, devices } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

// ESM-friendly __dirname (frontend package.json is `"type": "module"`).
const __dirname = path.dirname(fileURLToPath(import.meta.url))

/**
 * Production-tenant E2E config.
 *
 * Two modes:
 *
 *  E2E_MODE=isolated-tenant   (default)  - bot logs in via email + password
 *                                          + TOTP into a dedicated e2e tenant
 *                                          (e.g. e2e.getklai.com). Fully
 *                                          headless / CI-runnable.
 *
 *  E2E_MODE=voys-attached     - attaches to an existing browser session
 *                               that the user logged in manually (e.g. Voys
 *                               tenant via Google SSO). J01-login is SKIPPED.
 *                               Storage-state must already exist on disk;
 *                               capture it once via:
 *                                 npm run e2e:capture-session
 *                               Local-only - cannot run in CI.
 *
 * Required env (isolated-tenant mode):
 *   E2E_BASE_URL          e.g. https://e2e.getklai.com
 *   E2E_USER_EMAIL        e.g. e2e@getklai.com
 *   E2E_USER_PASSWORD     password for the e2e user
 *   E2E_TOTP_SECRET       Base32-encoded TOTP secret captured during MFA setup
 *
 * Required env (voys-attached mode):
 *   E2E_BASE_URL          e.g. https://voys.getklai.com
 *   (storageState.voys.json is captured by the helper script - no
 *    credentials in env)
 *
 * Run:
 *   npm run test:e2e:prod              (isolated-tenant)
 *   npm run test:e2e:prod:voys         (voys-attached)
 *
 * docs/testing/test-suite-plan.md §4 + §7.
 */

const E2E_MODE = (process.env.E2E_MODE ?? 'isolated-tenant') as
  | 'isolated-tenant'
  | 'voys-attached'

if (E2E_MODE === 'isolated-tenant') {
  const requiredEnv = ['E2E_BASE_URL', 'E2E_USER_EMAIL', 'E2E_USER_PASSWORD', 'E2E_TOTP_SECRET'] as const
  for (const k of requiredEnv) {
    if (!process.env[k]) {
      console.warn(`[playwright.prod.config] missing env: ${k}`)
    }
  }
} else if (E2E_MODE === 'voys-attached') {
  if (!process.env.E2E_BASE_URL) {
    console.warn('[playwright.prod.config] missing env: E2E_BASE_URL (voys-attached mode)')
  }
}

const STORAGE_STATE_ISOLATED = path.resolve(__dirname, 'storageState.json')
const STORAGE_STATE_VOYS = path.resolve(__dirname, 'storageState.voys.json')
const STORAGE_STATE = E2E_MODE === 'voys-attached' ? STORAGE_STATE_VOYS : STORAGE_STATE_ISOLATED

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
  // In isolated-tenant mode: J01 runs first, captures storage-state.
  // In voys-attached mode: J01 is SKIPPED - user has captured the
  //   storage-state manually via `npm run e2e:capture-session`.
  projects:
    E2E_MODE === 'voys-attached'
      ? [
          {
            name: 'authenticated-journeys',
            testIgnore: ['J01-login.spec.ts'],
            use: {
              ...devices['Desktop Chrome'],
              storageState: STORAGE_STATE,
            },
          },
        ]
      : [
          {
            name: 'login',
            testMatch: ['J01-login.spec.ts'],
            use: {
              ...devices['Desktop Chrome'],
              storageState: undefined,
            },
          },
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

export { STORAGE_STATE, E2E_MODE }
