import { defineConfig } from 'vitest/config'
import path from 'path'

export default defineConfig({
  test: {
    environment: 'jsdom',
    globals: true,
    // Playwright e2e specs are run by a separate harness. Excluded here so
    // vitest doesn't try to resolve @playwright/test. Covers both legacy
    // `e2e/` root-level specs and the `tests/e2e/` layout.
    exclude: ['**/node_modules/**', '**/dist/**', '**/e2e/**'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov'],
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
})
