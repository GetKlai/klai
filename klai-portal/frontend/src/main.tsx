import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider, createRouter } from '@tanstack/react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import * as Sentry from '@sentry/react'
import { KlaiAuthProvider } from '@/lib/auth'
import { LocaleProvider } from '@/lib/locale'
import { Toaster } from '@/components/ui/sonner'
import { routeTree } from './routeTree.gen'
import { initVitals } from '@/lib/vitals'
import { registerNavigateSingleton } from '@/lib/navigate-singleton'
import { registerStaleChunkReloadHandler } from '@/lib/stale-chunk-reload'
import './index.css'

registerStaleChunkReloadHandler()

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
    },
    mutations: {
      onError: (error) => Sentry.captureException(error),
    },
  },
})

// Custom search parsers that keep all values as strings.
// TanStack Router's default parser coerces numeric-looking values to Number,
// which loses precision for 18-digit Zitadel IDs (exceeds MAX_SAFE_INTEGER).
function parseSearch(searchStr: string): Record<string, string> {
  const str = searchStr.startsWith('?') ? searchStr.slice(1) : searchStr
  return Object.fromEntries(new URLSearchParams(str))
}

function stringifySearch(search: Record<string, unknown>): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(search)) {
    if (value !== undefined && value !== null) {
      // eslint-disable-next-line @typescript-eslint/no-base-to-string
      params.set(key, String(value))
    }
  }
  const str = params.toString()
  return str ? `?${str}` : ''
}

const router = createRouter({
  routeTree,
  context: { queryClient },
  parseSearch,
  stringifySearch,
})

// Register the navigate singleton so apiFetch can redirect on tenant_deleting 403s.
registerNavigateSingleton(
  (to) => { void router.navigate({ to }) },
  () => { queryClient.clear() },
)

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}

// GlitchTip generates UUID keys with hyphens; @sentry/react v8+ only accepts keys without hyphens.
// Strip hyphens from the key portion. GlitchTip's Python backend accepts both forms.
const rawDsn = import.meta.env.VITE_SENTRY_DSN as string | undefined
const dsn = rawDsn?.replace(/^(https?:\/\/)([^@]+)(@)/, (_, proto, key, at) => proto + key.replaceAll('-', '') + at)

const PII_DENY = ['forwarded', '-ip', 'remote-', 'via', '-user']

Sentry.init({
  dsn,
  environment: import.meta.env.MODE,
  // v11 collects user info, cookies, headers and bodies unless told otherwise, so the v10
  // default (sendDefaultPii: false) is spelled out, using the baseline from Sentry's v11
  // migration guide. The browser SDK has no database, queue or GenAI data to collect.
  dataCollection: {
    userInfo: false,
    cookies: false,
    httpHeaders: { request: { deny: PII_DENY }, response: { deny: PII_DENY } },
    httpBodies: [],
    urlQueryParams: { deny: PII_DENY },
  },
  // GlitchTip stores transactions but drops the streamed `span` envelope items v11 sends by
  // default (apps/event_ingest/schema.py IgnoredItemType, GlitchTip 6.2.6), so stay on transactions.
  traceLifecycle: 'static',
  integrations: (defaults) => [
    // Disable console breadcrumbs: console logs may contain user data. v11 moved them from
    // breadcrumbsIntegration({ console }) to the default Console integration.
    ...defaults.filter((integration) => integration.name !== 'Console'),
    Sentry.tanstackRouterBrowserTracingIntegration(router),
    Sentry.consoleLoggingIntegration({ levels: ['warn', 'error'] }),
    // Disable DOM breadcrumbs: click trails are behavioural data we don't need for debugging.
    Sentry.breadcrumbsIntegration({ dom: false }),
  ],
  tracesSampleRate: 0.3,
  beforeSend(event) {
    // Strip IP address - we identify by user ID only, not by network location.
    if (event.user) delete event.user.ip_address
    return event
  },
  enabled: !!rawDsn,
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <LocaleProvider>
      <KlaiAuthProvider>
        <QueryClientProvider client={queryClient}>
          <RouterProvider router={router} />
          <Toaster />
        </QueryClientProvider>
      </KlaiAuthProvider>
    </LocaleProvider>
  </StrictMode>
)

initVitals()
