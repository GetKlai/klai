import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { TanStackRouterVite } from '@tanstack/router-plugin/vite'
import { paraglideVitePlugin } from '@inlang/paraglide-js'
import { sentryVitePlugin } from '@sentry/vite-plugin'
import path from 'path'

export default defineConfig({
  plugins: [
    paraglideVitePlugin({
      project: './project.inlang',
      outdir: './src/paraglide',
      emitTsDeclarations: true,
    }),
    TanStackRouterVite({ routesDirectory: './src/routes' }),
    react(),
    tailwindcss(),
    // Upload source maps to GlitchTip at build time and delete them from dist/.
    // Only runs when SENTRY_AUTH_TOKEN is set (i.e. in CI, not local dev).
    sentryVitePlugin({
      org: 'klai',
      project: 'portal-frontend',
      authToken: process.env.SENTRY_AUTH_TOKEN,
      url: 'https://errors.getklai.com',
      sourcemaps: { filesToDeleteAfterUpload: ['dist/**/*.map'] },
      silent: !process.env.SENTRY_AUTH_TOKEN,
    }),
  ],
  build: {
    sourcemap: true,
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5174,
    strictPort: true,
  },
})
