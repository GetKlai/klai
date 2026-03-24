import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useRef } from 'react'
import { useAuth } from 'react-oidc-context'
import * as m from '@/paraglide/messages'
import { useLocale } from '@/lib/locale'
import { API_BASE } from '@/lib/api'
import { STORAGE_KEYS } from '@/lib/storage'
import { authLogger } from '@/lib/logger'

const ADMIN_ROLES = ['org:owner', 'org:admin']

export const Route = createFileRoute('/callback')(({
  component: CallbackPage,
}))

function CallbackPage() {
  const auth = useAuth()
  const redirected = useRef(false)
  useLocale() // subscribe to locale changes so Paraglide re-renders on switch

  useEffect(() => {
    if (auth.isLoading || !auth.isAuthenticated || redirected.current) return
    redirected.current = true

    async function resolveDestination() {
      try {
        const res = await fetch(`${API_BASE}/api/me`, {
          headers: { Authorization: `Bearer ${auth.user!.access_token}` },
        })
        if (res.ok) {
          const me = await res.json()

          // Provisioning still running — send to loading screen
          if (me.provisioning_status === 'pending' || me.provisioning_status === 'failed') {
            window.location.replace('/provisioning')
            return
          }

          // Provisioning done — ensure user is on their tenant subdomain
          if (me.workspace_url) {
            const currentHost = window.location.hostname
            const workspaceHost = new URL(me.workspace_url).hostname
            if (currentHost !== workspaceHost) {
              // User logged in from my.getklai.com — send them to their subdomain
              window.location.replace(me.workspace_url)
              return
            }
          }

          // Resolve role first so MFA redirect can use it
          const isAdmin = me.roles?.some((r: string) => ADMIN_ROLES.includes(r)) ?? false
          sessionStorage.setItem(STORAGE_KEYS.isAdmin, String(isAdmin))
          const isGroupAdmin = me.portal_role === 'group-admin'
          sessionStorage.setItem(STORAGE_KEYS.isGroupAdmin, String(isGroupAdmin))

          // MFA not yet enrolled — send to setup
          if (!me.mfa_enrolled) {
            sessionStorage.setItem(STORAGE_KEYS.mfaPolicy, me.mfa_policy ?? 'optional')
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
            window.location.replace('/admin')
          } else if (isGroupAdmin) {
            window.location.replace('/admin')
          } else {
            window.location.replace('/app')
          }
          return
        }
      } catch (err) {
        authLogger.error('Post-login routing failed: /api/me unreachable', err)
      }

      // /api/me failed (e.g. backend not running) — go to /app
      authLogger.warn('Post-login routing fell through to default /app')
      sessionStorage.setItem(STORAGE_KEYS.isAdmin, 'false')
      sessionStorage.setItem(STORAGE_KEYS.isGroupAdmin, 'false')
      window.location.replace('/app')
    }

    void resolveDestination()
  }, [auth.isLoading, auth.isAuthenticated, auth.user])

  if (auth.error) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--color-off-white)]">
        <div className="space-y-3 text-center max-w-sm px-4">
          <p className="text-sm font-medium text-[var(--color-destructive-text)]">{m.callback_error_heading()}</p>
          <p className="text-xs text-[var(--color-muted-foreground)] font-mono break-all">{auth.error.message}</p>
          <a href="/" className="block text-xs text-[var(--color-purple-muted)] hover:underline">{m.callback_error_back()}</a>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--color-off-white)]">
      <div className="space-y-3 text-center">
        <div className="mx-auto h-6 w-6 animate-spin rounded-full border-2 border-[var(--color-purple-accent)] border-t-transparent" />
        <p className="text-sm text-[var(--color-muted-foreground)]">{m.callback_loading()}</p>
      </div>
    </div>
  )
}
