import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useRef } from 'react'
import { useAuth } from 'react-oidc-context'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? ''

const ADMIN_ROLES = ['org:owner', 'org:admin']

export const Route = createFileRoute('/callback')(({
  component: CallbackPage,
}))

function CallbackPage() {
  const auth = useAuth()
  const redirected = useRef(false)

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

          // First login without 2FA — redirect to setup
          if (me.requires_2fa_setup) {
            window.location.replace('/setup/2fa')
            return
          }

          // On the correct subdomain — route by role
          const isAdmin = me.roles?.some((r: string) => ADMIN_ROLES.includes(r)) ?? false
          sessionStorage.setItem('klai:isAdmin', String(isAdmin))

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
            } catch {
              // Ignore — go to admin dashboard
            }
            window.location.replace('/admin')
          } else {
            window.location.replace('/app')
          }
          return
        }
      } catch {
        // fall through to default
      }

      // /api/me failed (e.g. backend not running) — go to /app
      sessionStorage.setItem('klai:isAdmin', 'false')
      window.location.replace('/app')
    }

    resolveDestination()
  }, [auth.isLoading, auth.isAuthenticated, auth.user])

  if (auth.error) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--color-off-white)]">
        <div className="space-y-3 text-center max-w-sm px-4">
          <p className="text-sm font-medium text-red-700">Inloggen mislukt</p>
          <p className="text-xs text-[var(--color-muted-foreground)] font-mono break-all">{auth.error.message}</p>
          <a href="/" className="block text-xs text-[var(--color-purple-muted)] hover:underline">Terug naar inloggen</a>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--color-off-white)]">
      <div className="space-y-3 text-center">
        <div className="mx-auto h-6 w-6 animate-spin rounded-full border-2 border-[var(--color-purple-accent)] border-t-transparent" />
        <p className="text-sm text-[var(--color-muted-foreground)]">Inloggen…</p>
      </div>
    </div>
  )
}
