import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { TanStackRouterVite } from '@tanstack/router-plugin/vite'
import { paraglideVitePlugin } from '@inlang/paraglide-js'
import { sentryVitePlugin } from '@sentry/vite-plugin'
import { visualizer } from 'rollup-plugin-visualizer'
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
    process.env.ANALYZE === 'true' && visualizer({
      open: true,
      filename: 'dist/bundle-report.html',
      gzipSize: true,
      brotliSize: true,
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
    proxy: Object.fromEntries(
      // Proxy all backend paths. Default: production (for frontend-only dev).
      // Set VITE_API_PROXY_TARGET=http://localhost:8010 to use local backends.
      ['/api', '/research', '/scribe', '/docs/api'].map((path) => [
        path,
        {
          target: process.env.VITE_API_PROXY_TARGET || 'https://getklai.getklai.com',
          changeOrigin: true,
          secure: true,
        },
      ]),
    ),
  },
})
