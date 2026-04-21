/**
 * Route guard helper that redirects unauthenticated visitors to a fallback
 * path (defaults to `/`).
 *
 * Why this exists: `useCurrentUser()` is gated by `enabled: auth.isAuthenticated`
 * so its `isPending` stays `true` forever for visitors without a session. Route
 * guards that wait on `userLoading` before checking authentication leave those
 * visitors stuck on a loading spinner. Using this hook (or mirroring its order)
 * keeps the redirect path consistent across `/app`, `/admin`, `/setup/*`, and
 * `/provisioning`.
 */

import { useEffect } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'

interface UseAuthGuardRedirectOptions {
  /** Path to send unauthenticated visitors to. Defaults to `/`. */
  readonly fallback?: string
}

export function useAuthGuardRedirect({
  fallback = '/',
}: UseAuthGuardRedirectOptions = {}): void {
  const auth = useAuth()
  const navigate = useNavigate()

  useEffect(() => {
    if (auth.isLoading) return
    if (!auth.isAuthenticated) {
      void navigate({ to: fallback })
    }
  }, [auth.isLoading, auth.isAuthenticated, fallback, navigate])
}
