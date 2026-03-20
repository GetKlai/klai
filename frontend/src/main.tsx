import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider, createRouter } from '@tanstack/react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import * as Sentry from '@sentry/react'
import { KlaiAuthProvider } from '@/lib/auth'
import { LocaleProvider } from '@/lib/locale'
import { Toaster } from '@/components/ui/sonner'
import { routeTree } from './routeTree.gen'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
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

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}

// GlitchTip generates UUID keys with hyphens; @sentry/react v8+ only accepts keys without hyphens.
// Strip hyphens from the key portion. GlitchTip's Python backend accepts both forms.
const rawDsn = import.meta.env.VITE_SENTRY_DSN as string | undefined
const dsn = rawDsn?.replace(/^(https?:\/\/)([^@]+)(@)/, (_, proto, key, at) => proto + key.replaceAll('-', '') + at)

Sentry.init({
  dsn,
  environment: import.meta.env.MODE,
  sendDefaultPii: false,
  integrations: [
    Sentry.tanstackRouterBrowserTracingIntegration(router),
    // Disable console + DOM breadcrumbs: console logs may contain user data,
    // DOM click trails are behavioural data we don't need for debugging.
    Sentry.breadcrumbsIntegration({ console: false, dom: false }),
  ],
  tracesSampleRate: 0.05,
  beforeSend(event) {
    // Strip IP address — we identify by user ID only, not by network location.
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
