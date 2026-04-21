/**
 * Shared authentication guard for all protected routes.
 *
 * Resolves the three post-auth decisions that every protected layout needs:
 *   1. No session  → bounce to the configured fallback (default `/`).
 *   2. Needs 2FA   → redirect to the MFA setup page.
 *   3. Wrong role  → redirect to the caller-supplied `noRoleFallback`.
 *
 * The redirects run in priority order. `isResolving` is `true` while any
 * dependency is still loading, so callers can show a single spinner until
 * the guard has made up its mind — no flash of content, no infinite spinner
 * when `useCurrentUser()` is disabled (which happens whenever `auth.isAuthenticated`
 * is false, because TanStack Query leaves disabled queries in `isPending: true`).
 */

import { useEffect } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useCurrentUser, type CurrentUser } from '@/hooks/useCurrentUser'

export interface UseProtectedRouteOptions {
  /** Path to send unauthenticated visitors to. Defaults to `/`. */
  readonly fallback?: string
  /** Require `isAdmin` or `isGroupAdmin` on the /api/me response. */
  readonly requireAdmin?: boolean
  /** Path to send authenticated-but-insufficient-role visitors to. Required when `requireAdmin` is true. */
  readonly noRoleFallback?: string
}

export interface UseProtectedRouteResult {
  /** User record once loaded (undefined until /api/me resolves). */
  readonly user: CurrentUser | undefined
  /** True while auth or /api/me is still loading, OR while a redirect is in-flight. */
  readonly isResolving: boolean
  /** True once the caller may safely render protected content. */
  readonly canRender: boolean
}

export function useProtectedRoute({
  fallback = '/',
  requireAdmin = false,
  noRoleFallback,
}: UseProtectedRouteOptions = {}): UseProtectedRouteResult {
  const auth = useAuth()
  const navigate = useNavigate()
  const { user, isPending: userLoading } = useCurrentUser()

  useEffect(() => {
    if (auth.isLoading) return
    // 1. Unauthenticated — redirect without waiting on /api/me, which is
    //    disabled (and thus perpetually `isPending`) when there is no session.
    if (!auth.isAuthenticated) {
      void navigate({ to: fallback })
      return
    }
    if (userLoading) return
    // 2. MFA setup pending — send to the setup flow.
    if (user?.requires_2fa_setup) {
      window.location.replace('/setup/2fa')
      return
    }
    // 3. Admin-only route with a non-admin caller.
    if (requireAdmin && user && !user.isAdmin && !user.isGroupAdmin && noRoleFallback) {
      void navigate({ to: noRoleFallback })
    }
  }, [
    auth.isLoading,
    auth.isAuthenticated,
    user,
    userLoading,
    requireAdmin,
    fallback,
    noRoleFallback,
    navigate,
  ])

  const hasRole = !requireAdmin || user?.isAdmin === true || user?.isGroupAdmin === true
  const isResolving = auth.isLoading || !auth.isAuthenticated || userLoading || !hasRole

  return {
    user,
    isResolving,
    canRender: !isResolving,
  }
}
