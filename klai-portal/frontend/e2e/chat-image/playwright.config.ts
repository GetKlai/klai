import { defineConfig, devices } from '@playwright/test'

// Screen check of a built chat image; CHAT_URL is set by
// deploy/tests/chat-config-boot.sh while the image runs. See screen.check.ts.
export default defineConfig({
  testDir: '.',
  testMatch: ['screen.check.ts'],
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  workers: 1,
  timeout: 180_000,
  expect: { timeout: 15_000 },
  reporter: [['list'], ['html', { outputFolder: 'playwright-report', open: 'never' }]],
  use: {
    ...devices['Desktop Chrome'],
    // CI uses the runner's Chrome, as the prod-tenant journeys do.
    ...(process.env.PLAYWRIGHT_BROWSER_CHANNEL ? { channel: process.env.PLAYWRIGHT_BROWSER_CHANNEL } : {}),
    baseURL: process.env.CHAT_URL ?? 'http://localhost:3080',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
})
