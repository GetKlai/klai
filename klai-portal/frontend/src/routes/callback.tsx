import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import { useAuth, type AuthContextProps } from '@/lib/auth'
import * as Sentry from '@sentry/react'
import * as m from '@/paraglide/messages'
import { useLocale } from '@/lib/locale'
import { API_BASE } from '@/lib/api'
import { authLogger } from '@/lib/logger'
import { Button } from '@/components/ui/button'
import { fetchMe, type MeResponse } from '@/lib/api-me'
import {
  UnauthorizedError,
  delay,
  friendlyErrorKey,
  isAborted,
  isRetryable,
  type FriendlyErrorKey,
} from '@/lib/fetch-errors'

const ADMIN_ROLES = ['org:owner', 'org:admin']
const RETRY_DELAY_MS = 1000

export const Route = createFileRoute('/callback')(({
  component: CallbackPage,
}))

/**
 * Post-login routing outcome. `resolveDestination` computes one; the React
 * layer performs the corresponding side-effect. Keeping data and I/O apart
 * makes the resolver pure and the caller trivial to reason about.
 */
type RouteDecision =
  | { kind: 'navigate'; url: string }
  | { kind: 'reauth' }
  | { kind: 'error'; error: unknown }

// ---------------------------------------------------------------------------
// Data layer
// ---------------------------------------------------------------------------

/** Fetch /api/me with a single retry on transient failure; aborts honour signal. */
async function fetchMeWithRetry(signal: AbortSignal): Promise<MeResponse> {
  try {
    return await fetchMe(undefined, signal)
  } catch (err) {
    if (isAborted(err) || !isRetryable(err)) throw err
    authLogger.warn('Post-login /api/me failed, retrying', { error: err })
    await delay(RETRY_DELAY_MS, signal)
    return await fetchMe(undefined, signal)
  }
}

/** Soft billing-status probe. Failures never block post-login routing. */
async function fetchBillingStatus(signal: AbortSignal): Promise<string | null> {
  try {
    const res = await fetch(`${API_BASE}/api/billing/status`, {
      credentials: 'include',
      signal,
    })
    if (!res.ok) return null
    const body = (await res.json()) as { billing_status?: string }
    return body.billing_status ?? null
  } catch (err) {
    if (isAborted(err)) return null
    authLogger.warn('Billing status check failed during post-login routing', { error: err })
    return null
  }
}

/** Resolve the next URL the user should land on after login. */
async function resolveDestination(signal: AbortSignal): Promise<RouteDecision> {
  let me: MeResponse
  try {
    me = await fetchMeWithRetry(signal)
  } catch (err) {
    if (isAborted(err)) return { kind: 'error', error: err }
    if (err instanceof UnauthorizedError) {
      authLogger.info('Post-login /api/me returned 401, forcing reauth')
      return { kind: 'reauth' }
    }
    authLogger.error('Post-login routing failed: /api/me unreachable', err)
    return { kind: 'error', error: err }
  }

  if (me.org_found === false) return { kind: 'navigate', url: '/no-account' }

  if (me.provisioning_status === 'pending' || me.provisioning_status === 'failed') {
    return { kind: 'navigate', url: '/provisioning' }
  }

  const handoff = workspaceHandoff(me.workspace_url)
  if (handoff) return { kind: 'navigate', url: handoff }

  if (!me.mfa_enrolled) return { kind: 'navigate', url: '/setup/mfa' }

  const isAdmin = me.roles?.some((r) => ADMIN_ROLES.includes(r)) ?? false
  if (isAdmin) {
    const billingStatus = await fetchBillingStatus(signal)
    if (billingStatus === 'pending') return { kind: 'navigate', url: '/admin/billing' }
  }

  return { kind: 'navigate', url: '/app' }
}

/**
 * URL to hand off to the tenant subdomain, or null if the current origin
 * already matches (or local dev, or workspace_url is malformed).
 */
function workspaceHandoff(workspaceUrl: string | null | undefined): string | null {
  if (!workspaceUrl) return null
  const currentHost = window.location.hostname
  if (currentHost === 'localhost' || currentHost === '127.0.0.1') return null
  try {
    const workspaceHost = new URL(workspaceUrl).hostname
    return currentHost === workspaceHost ? null : workspaceUrl
  } catch (err) {
    authLogger.warn('Invalid workspace_url from /api/me, skipping handoff', {
      workspace_url: workspaceUrl,
      error: err,
    })
    return null
  }
}

// ---------------------------------------------------------------------------
// React layer
// ---------------------------------------------------------------------------

function CallbackPage() {
  const auth = useAuth()
  const { isLoading, isAuthenticated } = auth

  const redirected = useRef(false)
  const [postLoginError, setPostLoginError] = useState<unknown>(null)
  useLocale() // subscribe to locale changes so Paraglide re-renders on switch

  useEffect(() => {
    if (isLoading || !isAuthenticated || redirected.current) return

    redirected.current = true
    const controller = new AbortController()

    void applyDecision(auth, controller.signal, setPostLoginError)

    return () => {
      controller.abort()
      // Reset so a subsequent remount (React strict mode, state changes) can
      // re-run the resolution instead of stalling on the stale flag.
      redirected.current = false
    }
    // `auth` identity is unstable — its reference changes on every provider
    // render, which would abort and retry the fetch on benign updates. Depend
    // only on the primitives that actually gate the flow.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoading, isAuthenticated])

  if (auth.error) {
    return <ErrorScreen error={auth.error} retryable={false} />
  }

  if (postLoginError !== null) {
    return <ErrorScreen error={postLoginError} retryable={isRetryable(postLoginError)} />
  }

  return <LoadingScreen />
}

/**
 * Bridge from `resolveDestination` (pure) to the browser (side effects).
 * Records errors in Sentry with a fingerprint so retries don't spam unique
 * events, and reports the error back to the React tree for the UI.
 */
async function applyDecision(
  auth: AuthContextProps,
  signal: AbortSignal,
  onError: (err: unknown) => void,
): Promise<void> {
  const decision = await resolveDestination(signal)
  if (signal.aborted) return

  switch (decision.kind) {
    case 'navigate':
      window.location.replace(decision.url)
      return
    case 'reauth':
      await auth.removeUser()
      return
    case 'error':
      if (isAborted(decision.error)) return
      reportError(decision.error)
      onError(decision.error)
      return
  }
}

function reportError(error: unknown): void {
  if (!(error instanceof Error)) return
  Sentry.captureException(error, {
    tags: {
      domain: 'auth',
      phase: 'post-login',
      error_kind: friendlyErrorKey(error),
    },
    fingerprint: ['callback-post-login', friendlyErrorKey(error)],
  })
}

// ---------------------------------------------------------------------------
// UI
// ---------------------------------------------------------------------------

function LoadingScreen() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--color-background)]">
      <div className="space-y-3 text-center">
        <div className="mx-auto h-6 w-6 animate-spin rounded-full border-2 border-[var(--color-rl-accent)] border-t-transparent" />
        <p className="text-sm text-[var(--color-muted-foreground)]">{m.callback_loading()}</p>
      </div>
    </div>
  )
}

interface ErrorScreenProps {
  error: unknown
  retryable: boolean
}

function ErrorScreen({ error, retryable }: ErrorScreenProps) {
  const friendly = friendlyMessage(friendlyErrorKey(error))
  const technical = error instanceof Error ? error.message : String(error)

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--color-background)]">
      <div className="space-y-3 text-center max-w-sm px-4">
        <p className="text-sm font-medium text-[var(--color-destructive-text)]">
          {m.callback_error_heading()}
        </p>
        <p className="text-sm text-[var(--color-muted-foreground)]">{friendly}</p>
        <p className="text-xs font-mono break-all opacity-60 text-[var(--color-muted-foreground)]">
          {technical}
        </p>
        <div className="flex items-center justify-center gap-2 pt-1">
          {retryable && (
            <Button variant="link" size="sm" onClick={() => window.location.reload()}>
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

function friendlyMessage(key: FriendlyErrorKey): string {
  switch (key) {
    case 'network':
      return m.error_network()
    case 'server_temporary':
      return m.error_server_temporary()
    case 'not_found':
      return m.error_not_found()
    case 'forbidden':
      return m.error_forbidden()
    case 'generic':
      return m.error_generic()
  }
}
