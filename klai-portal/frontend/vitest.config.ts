import { defineConfig } from 'vitest/config'
import path from 'path'

export default defineConfig({
  test: {
    environment: 'jsdom',
    globals: true,
    // Playwright e2e specs live under tests/e2e and are run by a separate
    // harness (see SPEC-CHAT-TEMPLATES-002 Phase G). Excluded here so vitest
    // doesn't try to resolve @playwright/test.
    exclude: ['**/node_modules/**', '**/dist/**', 'tests/e2e/**'],
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
