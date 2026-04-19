import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import { useAuth } from 'react-oidc-context'
import * as m from '@/paraglide/messages'
import { useLocale } from '@/lib/locale'
import { API_BASE } from '@/lib/api'
import { authLogger } from '@/lib/logger'

const ADMIN_ROLES = ['org:owner', 'org:admin']
const RETRY_DELAY_MS = 1000

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

/** Fetch /api/me with a single retry on transient failure. */
async function fetchMeWithRetry(token: string): Promise<MeResponse> {
  const url = `${API_BASE}/api/me`
  const headers = { Authorization: `Bearer ${token}` }

  try {
    const res = await fetch(url, { headers })
    if (res.ok) return (await res.json()) as MeResponse
    throw new Error(`HTTP ${res.status}`)
  } catch (err) {
    authLogger.warn('Post-login /api/me failed, retrying', { error: err })
    await new Promise((resolve) => setTimeout(resolve, RETRY_DELAY_MS))
    const res = await fetch(url, { headers })
    if (res.ok) return (await res.json()) as MeResponse
    throw new Error(`HTTP ${res.status}`, { cause: err })
  }
}

function CallbackPage() {
  const auth = useAuth()
  const redirected = useRef(false)
  const [postLoginError, setPostLoginError] = useState<Error | null>(null)
  useLocale() // subscribe to locale changes so Paraglide re-renders on switch

  useEffect(() => {
    if (auth.isLoading || !auth.isAuthenticated || redirected.current) return
    redirected.current = true

    async function resolveDestination() {
      let me: MeResponse
      try {
        me = await fetchMeWithRetry(auth.user!.access_token)
      } catch (err) {
        authLogger.error('Post-login routing failed: /api/me unreachable', err)
        setPostLoginError(err instanceof Error ? err : new Error(String(err)))
        redirected.current = false
        return
      }

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

      // Provisioning done — ensure user is on their tenant subdomain
      // Skip workspace redirect in local dev (localhost/127.0.0.1)
      if (me.workspace_url) {
        const currentHost = window.location.hostname
        const isLocalDev = currentHost === 'localhost' || currentHost === '127.0.0.1'
        if (!isLocalDev) {
          const workspaceHost = new URL(me.workspace_url).hostname
          if (currentHost !== workspaceHost) {
            // User logged in from my.getklai.com — send them to their subdomain.
            // The tenant origin has empty localStorage, so its `/` route will
            // fire signinRedirect and complete silently via the Zitadel SSO
            // session cookie on auth.getklai.com.
            window.location.replace(me.workspace_url)
            return
          }
        }
      }

      // Resolve role for routing decisions (useCurrentUser handles persistence)
      const isAdmin = me.roles?.some((r: string) => ADMIN_ROLES.includes(r)) ?? false

      // MFA not yet enrolled — send to setup
      if (!me.mfa_enrolled) {
        window.location.replace('/setup/mfa')
        return
      }

      if (isAdmin) {
        try {
          const billingRes = await fetch(`${API_BASE}/api/billing/status`, {
            headers: { Authorization: `Bearer ${auth.user!.access_token}` },
          })
          if (billingRes.ok) {
            const billing = await billingRes.json()
            if (billing.billing_status === 'pending') {
              window.location.replace('/admin/billing')
              return
            }
          }
        } catch (err) {
          authLogger.warn('Billing status check failed during post-login routing', err)
        }
      }
      window.location.replace('/app')
    }

    void resolveDestination()
  }, [auth.isLoading, auth.isAuthenticated, auth.user])

  if (auth.error) {
    return (
      <ErrorScreen
        message={auth.error.message}
        retryable={false}
      />
    )
  }

  if (postLoginError) {
    return (
      <ErrorScreen
        message={postLoginError.message}
        retryable={true}
      />
    )
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
        <div className="flex items-center justify-center gap-4 pt-1">
          {retryable && (
            <button
              onClick={() => window.location.reload()}
              className="text-xs text-[var(--color-rl-accent-dark)] hover:underline"
            >
              {m.callback_error_retry()}
            </button>
          )}
          <a href="/" className="text-xs text-[var(--color-rl-accent-dark)] hover:underline">
            {m.callback_error_back()}
          </a>
        </div>
      </div>
    </div>
  )
}
