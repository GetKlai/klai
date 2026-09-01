import { defineConfig, devices } from '@playwright/test'

const port = Number(process.env.CONDUCTOR_PORT ?? 5174)
const baseURL = `http://127.0.0.1:${port}`

/**
 * Visual baselines are Linux artifacts. Regenerate them from macOS with:
 * `npm run test:visual:update` (runs Playwright in the matching official image).
 */
export default defineConfig({
  testDir: '.',
  testMatch: ['ui-catalog.visual.spec.ts', 'ui-catalog.a11y.spec.ts'],
  outputDir: 'test-results',
  snapshotPathTemplate: '{testDir}/__screenshots__/{arg}{ext}',
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  workers: 1,
  reporter: 'line',
  expect: {
    toHaveScreenshot: {
      animations: 'disabled',
      maxDiffPixelRatio: 0.001,
    },
  },
  use: {
    ...devices['Desktop Chrome'],
    baseURL,
    colorScheme: 'light',
    locale: 'nl-NL',
    reducedMotion: 'reduce',
    timezoneId: 'Europe/Amsterdam',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'visual-chromium',
      testMatch: 'ui-catalog.visual.spec.ts',
      use: { browserName: 'chromium' },
    },
    {
      name: 'a11y-chromium',
      testMatch: 'ui-catalog.a11y.spec.ts',
      use: { browserName: 'chromium' },
    },
  ],
  webServer: {
    command: `npm run dev -- --host 0.0.0.0 --port ${port} --strictPort`,
    url: `${baseURL}/dev/ui`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
})
