import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { useAuth, type AuthContextProps } from '@/lib/auth'
import { CheckCircle, AlertCircle } from 'lucide-react'
import * as Sentry from '@sentry/react'
import * as m from '@/paraglide/messages'
import { LocaleSwitcher } from '@/components/ui/LocaleSwitcher'
import { Button } from '@/components/ui/button'
import { useLocale } from '@/lib/locale'
import { authLogger } from '@/lib/logger'
import { fetchMe, type MeResponse } from '@/lib/api-me'
import {
  UnauthorizedError,
  delay,
  friendlyErrorKey,
  isAborted,
  isRetryable,
} from '@/lib/fetch-errors'

/** Base interval between successful status polls — provisioning takes seconds, not minutes. */
const POLL_INTERVAL_MS = 3000

/** Exponential backoff when the server or network is unreachable. */
const ERROR_BACKOFF_MS = [3000, 6000, 12000, 24000, 48000] as const

/** After this many consecutive failures, stop retrying and surface an error to the user. */
const MAX_CONSECUTIVE_ERRORS = ERROR_BACKOFF_MS.length

/** Total time budget — after this, show a "provisioning is stuck" message. */
const POLL_TIMEOUT_MS = 5 * 60 * 1000

type Status = 'polling' | 'ready' | 'failed' | 'timeout' | 'error'

export const Route = createFileRoute('/provisioning')((({
  component: ProvisioningPage,
})))

function ProvisioningPage() {
  useLocale()
  const auth = useAuth()
  const { isLoading, isAuthenticated } = auth

  const [status, setStatus] = useState<Status>('polling')
  const [dots, setDots] = useState('')

  // Animated dots for the loading state
  useEffect(() => {
    const id = setInterval(() => {
      setDots((d) => (d.length >= 3 ? '' : d + '.'))
    }, 500)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    if (isLoading || !isAuthenticated) return

    const controller = new AbortController()
    void pollProvisioning(auth, controller.signal, setStatus)

    return () => controller.abort()
    // See callback.tsx for why `auth` is excluded from deps.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoading, isAuthenticated])

  return (
    <div className="flex min-h-screen flex-col bg-[var(--color-background)]">
      <div className="flex justify-end px-6 pt-5">
        <LocaleSwitcher />
      </div>
      <div className="flex flex-1 items-center justify-center">
        <div className="w-full max-w-sm space-y-6 px-6 text-center">
          <img src="/klai-logo.svg" alt="Klai" className="h-7 w-auto mx-auto" />
          {status === 'polling' && <PollingView dots={dots} />}
          {status === 'ready' && <ReadyView />}
          {status === 'error' && <RecoverableErrorView />}
          {(status === 'failed' || status === 'timeout') && <FatalErrorView />}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Poll loop
// ---------------------------------------------------------------------------

/**
 * Poll /api/me until the tenant's provisioning_status transitions out of
 * "pending". On success redirect to the tenant subdomain (or /admin if we're
 * already on it). On failure surface an error state appropriate to the cause:
 *
 *   - UnauthorizedError            → sign out; the root route reauths
 *   - non-retryable HTTP response  → "failed"
 *   - transient errors beyond N    → "error" (retry button)
 *   - still pending after 5 min    → "timeout"
 *   - provisioning_status=failed   → "failed"
 */
async function pollProvisioning(
  auth: AuthContextProps,
  signal: AbortSignal,
  setStatus: (s: Status) => void,
): Promise<void> {
  const startedAt = Date.now()
  let consecutiveErrors = 0

  while (!signal.aborted) {
    if (Date.now() - startedAt > POLL_TIMEOUT_MS) {
      setStatus('timeout')
      return
    }

    let me: MeResponse
    try {
      me = await fetchMe(undefined, signal)
      consecutiveErrors = 0
    } catch (err) {
      if (isAborted(err)) return
      if (err instanceof UnauthorizedError) {
        authLogger.info('Provisioning poll returned 401, forcing reauth')
        await auth.removeUser()
        return
      }
      if (!isRetryable(err)) {
        authLogger.error('Provisioning poll hit a permanent error', err)
        reportProvisioningError(err, { fatal: true })
        setStatus('failed')
        return
      }

      consecutiveErrors += 1
      if (consecutiveErrors >= MAX_CONSECUTIVE_ERRORS) {
        authLogger.error('Provisioning poll exceeded max consecutive errors', {
          attempts: consecutiveErrors,
          error: err,
        })
        reportProvisioningError(err, { fatal: false })
        setStatus('error')
        return
      }

      const backoffMs = ERROR_BACKOFF_MS[consecutiveErrors - 1] ?? POLL_INTERVAL_MS
      authLogger.warn('Provisioning poll failed, backing off', {
        attempt: consecutiveErrors,
        delay_ms: backoffMs,
        error: err,
      })
      try {
        await delay(backoffMs, signal)
      } catch (abortErr) {
        if (isAborted(abortErr)) return
        throw abortErr
      }
      continue
    }

    if (me.provisioning_status === 'ready' && me.workspace_url) {
      setStatus('ready')
      redirectToWorkspace(me.workspace_url)
      return
    }

    if (me.provisioning_status === 'failed') {
      setStatus('failed')
      return
    }

    // Still pending — poll again at the base interval.
    try {
      await delay(POLL_INTERVAL_MS, signal)
    } catch (abortErr) {
      if (isAborted(abortErr)) return
      throw abortErr
    }
  }
}

function redirectToWorkspace(workspaceUrl: string): void {
  try {
    const workspaceHost = new URL(workspaceUrl).hostname
    if (window.location.hostname !== workspaceHost) {
      window.location.replace(workspaceUrl)
      return
    }
  } catch (err) {
    authLogger.warn('Invalid workspace_url from /api/me, falling back to /admin', {
      workspace_url: workspaceUrl,
      error: err,
    })
  }
  window.location.replace('/admin')
}

function reportProvisioningError(error: unknown, opts: { fatal: boolean }): void {
  if (!(error instanceof Error)) return
  Sentry.captureException(error, {
    tags: {
      domain: 'auth',
      phase: 'provisioning-poll',
      error_kind: friendlyErrorKey(error),
      fatal: opts.fatal ? 'true' : 'false',
    },
    fingerprint: ['provisioning-poll', friendlyErrorKey(error)],
  })
}

// ---------------------------------------------------------------------------
// View states
// ---------------------------------------------------------------------------

function PollingView({ dots }: { dots: string }) {
  return (
    <>
      <div className="mx-auto h-10 w-10 animate-spin rounded-full border-2 border-[var(--color-rl-accent)] border-t-transparent" />
      <div className="space-y-2">
        <p className="text-xl font-semibold text-[var(--color-foreground)]">
          {m.provisioning_polling_title()}
          {dots}
        </p>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.provisioning_polling_subtitle()}
        </p>
      </div>
    </>
  )
}

function ReadyView() {
  return (
    <>
      <CheckCircle size={40} className="mx-auto text-[var(--color-rl-accent)]" strokeWidth={1.5} />
      <p className="text-xl font-semibold text-[var(--color-foreground)]">
        {m.provisioning_ready_title()}
      </p>
      <p className="text-sm text-[var(--color-muted-foreground)]">
        {m.provisioning_ready_subtitle()}
      </p>
    </>
  )
}

function RecoverableErrorView() {
  return (
    <>
      <AlertCircle size={40} className="mx-auto text-[var(--color-destructive)]" strokeWidth={1.5} />
      <div className="space-y-2">
        <p className="text-xl font-semibold text-[var(--color-foreground)]">
          {m.provisioning_error_title()}
        </p>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.provisioning_error_body()}
        </p>
      </div>
      <Button variant="link" size="sm" onClick={() => window.location.reload()}>
        {m.provisioning_error_retry()}
      </Button>
    </>
  )
}

function FatalErrorView() {
  return (
    <>
      <AlertCircle size={40} className="mx-auto text-[var(--color-destructive)]" strokeWidth={1.5} />
      <div className="space-y-2">
        <p className="text-xl font-semibold text-[var(--color-foreground)]">
          {m.provisioning_failed_title()}
        </p>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.provisioning_failed_body()}{' '}
          <a
            href="mailto:support@getklai.com"
            className="font-medium text-[var(--color-rl-accent-dark)] underline"
          >
            support@getklai.com
          </a>
          .
        </p>
      </div>
    </>
  )
}
