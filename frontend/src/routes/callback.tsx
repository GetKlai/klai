import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useRef } from 'react'
import { useAuth } from 'react-oidc-context'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? ''

const ADMIN_ROLES = ['org:owner', 'org:admin']

export const Route = createFileRoute('/callback')({
  component: CallbackPage,
})

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
          const isAdmin = me.roles?.some((r: string) => ADMIN_ROLES.includes(r)) ?? false
          sessionStorage.setItem('klai:isAdmin', String(isAdmin))
          window.location.replace(isAdmin ? '/admin' : '/app')
          return
        }
      } catch {
        // fall through to default
      }
      // If /api/me fails (e.g. backend not running), go to /app
      sessionStorage.setItem('klai:isAdmin', 'false')
      window.location.replace('/app')
    }

    resolveDestination()
  }, [auth.isLoading, auth.isAuthenticated, auth.user])

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--color-off-white)]">
      <div className="space-y-3 text-center">
        <div className="mx-auto h-6 w-6 animate-spin rounded-full border-2 border-[var(--color-purple-accent)] border-t-transparent" />
        <p className="text-sm text-[var(--color-muted-foreground)]">Inloggen…</p>
      </div>
    </div>
  )
}
