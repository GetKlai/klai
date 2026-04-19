import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import { useAuth } from 'react-oidc-context'
import type { User } from 'oidc-client-ts'
import * as m from '@/paraglide/messages'
import { useLocale } from '@/lib/locale'
import { API_BASE } from '@/lib/api'
import { authLogger } from '@/lib/logger'
import { Button } from '@/components/ui/button'

const ADMIN_ROLES = ['org:owner', 'org:admin']
const RETRY_DELAY_MS = 1000

/** HTTP status codes that warrant a single retry (transient server/network issues). */
const RETRYABLE_STATUS: ReadonlySet<number> = new Set([408, 429, 500, 502, 503, 504])

export const Route = createFileRoute('/callback')(({
  component: CallbackPage,
}))

interface MeResponse {
  org_found?: boolean
  provisioning_status?: string
  workspace_url?: string | null
  roles?: string[]
  mfa_enrolled?: boolean
}

/** The access_token has been rejected by portal-api — force a full reauth. */
class UnauthorizedError extends Error {
  constructor() {
    super('Unauthorized')
    this.name = 'UnauthorizedError'
  }
}

/** Any other non-OK HTTP response. Retryable if the status is in RETRYABLE_STATUS. */
class FetchError extends Error {
  readonly status: number

  constructor(status: number) {
    super(`HTTP ${status}`)
    this.name = 'FetchError'
    this.status = status
  }
}

/**
 * True when the error is likely transient (network blip, 5xx, 408, 429) and a
 * retry may succeed. 401/403/404 and client-side bugs are not retryable.
 */
function isRetryable(err: unknown): boolean {
  if (err instanceof UnauthorizedError) return false
  if (err instanceof FetchError) return RETRYABLE_STATUS.has(err.status)
  // Bare fetch() rejections (DNS failure, CORS, connection refused) surface as
  // TypeError. These are almost always transient; retry once.
  return err instanceof TypeError
}

async function fetchMe(token: string, signal: AbortSignal): Promise<MeResponse> {
  const res = await fetch(`${API_BASE}/api/me`, {
    headers: { Authorization: `Bearer ${token}` },
    signal,
  })
  if (res.ok) return (await res.json()) as MeResponse
  if (res.status === 401) throw new UnauthorizedError()
  throw new FetchError(res.status)
}

/**
 * Fetch /api/me, retrying once after a short delay on transient failure.
 * 401 responses short-circuit so the caller can force a reauth; non-retryable
 * statuses propagate immediately.
 */
async function fetchMeWithRetry(token: string, signal: AbortSignal): Promise<MeResponse> {
  try {
    return await fetchMe(token, signal)
  } catch (err) {
    if (signal.aborted || !isRetryable(err)) throw err
    authLogger.warn('Post-login /api/me failed, retrying', { error: err })
    await new Promise((resolve) => setTimeout(resolve, RETRY_DELAY_MS))
    if (signal.aborted) throw new DOMException('Aborted', 'AbortError')
    return await fetchMe(token, signal)
  }
}

function CallbackPage() {
  const auth = useAuth()
  const redirected = useRef(false)
  const [postLoginError, setPostLoginError] = useState<Error | null>(null)
  useLocale() // subscribe to locale changes so Paraglide re-renders on switch

  useEffect(() => {
    if (auth.isLoading || !auth.isAuthenticated || redirected.current) return
    const user = auth.user
    if (!user) return
    redirected.current = true

    const controller = new AbortController()
    void resolveDestination(auth, user, controller.signal, setPostLoginError, () => {
      redirected.current = false
    })

    return () => controller.abort()
  }, [auth, auth.isLoading, auth.isAuthenticated, auth.user])

  if (auth.error) {
    return <ErrorScreen message={auth.error.message} retryable={false} />
  }

  if (postLoginError) {
    return <ErrorScreen message={postLoginError.message} retryable={true} />
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--color-background)]">
      <div className="space-y-3 text-center">
        <div className="mx-auto h-6 w-6 animate-spin rounded-full border-2 border-[var(--color-rl-accent)] border-t-transparent" />
        <p className="text-sm text-[var(--color-muted-foreground)]">{m.callback_loading()}</p>
      </div>
    </div>
  )
}

/**
 * Post-login routing:
 * 1. Fetch /api/me (with retry + abort support)
 * 2. On 401 → removeUser + let the root route restart signinRedirect
 * 3. On no-org / pending-provisioning / MFA-pending → corresponding route
 * 4. On wrong subdomain → hand off to tenant origin (fresh OIDC handshake there)
 * 5. On admin with pending billing → /admin/billing
 * 6. Otherwise → /app
 */
async function resolveDestination(
  auth: ReturnType<typeof useAuth>,
  user: User,
  signal: AbortSignal,
  onError: (err: Error) => void,
  onReset: () => void,
): Promise<void> {
  let me: MeResponse
  try {
    me = await fetchMeWithRetry(user.access_token, signal)
  } catch (err) {
    if (signal.aborted) return
    if (err instanceof UnauthorizedError) {
      authLogger.info('Post-login /api/me returned 401, forcing reauth')
      await auth.removeUser()
      return
    }
    authLogger.error('Post-login routing failed: /api/me unreachable', err)
    onError(err instanceof Error ? err : new Error(String(err)))
    onReset()
    return
  }
  if (signal.aborted) return

  // SSO user with no org — show no-account page
  if (me.org_found === false) {
    window.location.replace('/no-account')
    return
  }

  // Provisioning still running — send to loading screen
  if (me.provisioning_status === 'pending' || me.provisioning_status === 'failed') {
    window.location.replace('/provisioning')
    return
  }

  // Provisioning done — ensure user is on their tenant subdomain.
  // Skip workspace redirect in local dev (localhost/127.0.0.1).
  if (me.workspace_url && !isLocalDev()) {
    try {
      const workspaceHost = new URL(me.workspace_url).hostname
      if (window.location.hostname !== workspaceHost) {
        // User logged in from my.getklai.com — hand off to their subdomain.
        // The tenant origin has empty localStorage, so its `/` route will fire
        // signinRedirect and complete silently via the Zitadel SSO cookie on
        // auth.getklai.com.
        window.location.replace(me.workspace_url)
        return
      }
    } catch (err) {
      authLogger.warn('Invalid workspace_url from /api/me, skipping handoff', {
        workspace_url: me.workspace_url,
        error: err,
      })
    }
  }

  // MFA not yet enrolled — send to setup
  if (!me.mfa_enrolled) {
    window.location.replace('/setup/mfa')
    return
  }

  const isAdmin = me.roles?.some((r) => ADMIN_ROLES.includes(r)) ?? false
  if (isAdmin && (await hasPendingBilling(user.access_token, signal))) {
    window.location.replace('/admin/billing')
    return
  }

  window.location.replace('/app')
}

function isLocalDev(): boolean {
  const host = window.location.hostname
  return host === 'localhost' || host === '127.0.0.1'
}

/** Soft check for pending billing — failures are non-fatal and default to "not pending". */
async function hasPendingBilling(token: string, signal: AbortSignal): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/api/billing/status`, {
      headers: { Authorization: `Bearer ${token}` },
      signal,
    })
    if (!res.ok) return false
    const billing = (await res.json()) as { billing_status?: string }
    return billing.billing_status === 'pending'
  } catch (err) {
    if (signal.aborted) return false
    authLogger.warn('Billing status check failed during post-login routing', err)
    return false
  }
}

interface ErrorScreenProps {
  message: string
  retryable: boolean
}

function ErrorScreen({ message, retryable }: ErrorScreenProps) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--color-background)]">
      <div className="space-y-3 text-center max-w-sm px-4">
        <p className="text-sm font-medium text-[var(--color-destructive-text)]">{m.callback_error_heading()}</p>
        <p className="text-xs text-[var(--color-muted-foreground)] font-mono break-all">{message}</p>
        <div className="flex items-center justify-center gap-2 pt-1">
          {retryable && (
            <Button
              variant="link"
              size="sm"
              onClick={() => window.location.reload()}
            >
              {m.callback_error_retry()}
            </Button>
          )}
          <Button asChild variant="link" size="sm">
            <a href="/">{m.callback_error_back()}</a>
          </Button>
        </div>
      </div>
    </div>
  )
}
