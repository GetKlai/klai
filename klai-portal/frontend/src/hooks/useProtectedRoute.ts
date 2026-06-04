/**
 * Shared authentication guard for all protected routes.
 *
 * Resolves the three post-auth decisions that every protected layout needs:
 *   1. No session  → bounce to the configured fallback (default `/`).
 *   2. Needs 2FA   → redirect to the MFA setup page.
 *   3. Wrong role  → redirect to the caller-supplied `noRoleFallback`.
 *
 * SPEC-PORTAL-PROFILES-001 P3.2: `requireMinRole` replaces the blanket
 * `requireAdmin` check for admin/* layouts. `requireAdmin` is kept for
 * backward compat but `requireMinRole: 'kb_manager'` lets sub-admin roles
 * (kb_manager, group_manager) enter the admin section.
 *
 * The redirects run in priority order. `isResolving` is `true` while any
 * dependency is still loading, so callers can show a single spinner until
 * the guard has made up its mind - no flash of content, no infinite spinner
 * when `useCurrentUser()` is disabled (which happens whenever `auth.isAuthenticated`
 * is false, because TanStack Query leaves disabled queries in `isPending: true`).
 */

import { useEffect } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useCurrentUser, type CurrentUser } from '@/hooks/useCurrentUser'
import { meetsMinRole, type ProfileRole } from '@/lib/profiles'

interface BaseOptions {
  /** Path to send unauthenticated visitors to. Defaults to `/`. */
  readonly fallback?: string
}

interface AdminOptions extends BaseOptions {
  /** Require `isAdmin` or `isGroupAdmin` on the /api/me response. */
  readonly requireAdmin: true
  /** Path to send authenticated-but-insufficient-role visitors to. */
  readonly noRoleFallback: string
  readonly requireMinRole?: never
}

interface MinRoleOptions extends BaseOptions {
  /** Require effective_role >= minRole on the profile ladder. */
  readonly requireMinRole: ProfileRole
  /** Path to send authenticated-but-insufficient-role visitors to. */
  readonly noRoleFallback: string
  readonly requireAdmin?: never
}

interface NonAdminOptions extends BaseOptions {
  readonly requireAdmin?: false
  readonly requireMinRole?: never
  readonly noRoleFallback?: never
}

/**
 * Discriminated union: setting `requireAdmin: true` or `requireMinRole` makes
 * `noRoleFallback` compile-time mandatory.
 */
export type UseProtectedRouteOptions = AdminOptions | MinRoleOptions | NonAdminOptions

export interface UseProtectedRouteResult {
  /** User record once loaded (undefined until /api/me resolves). */
  readonly user: CurrentUser | undefined
  /** True while auth or /api/me is still loading, OR while a redirect is in-flight. */
  readonly isResolving: boolean
  /** True once the caller may safely render protected content. */
  readonly canRender: boolean
}

function hasRequiredRole(user: CurrentUser | undefined, options: UseProtectedRouteOptions): boolean {
  if (!user) return false
  if ('requireAdmin' in options && options.requireAdmin) {
    return user.isAdmin === true || user.isGroupAdmin === true
  }
  if ('requireMinRole' in options && options.requireMinRole) {
    return meetsMinRole(user.effective_role, options.requireMinRole)
  }
  return true
}

function workspaceHandoffUrl(workspaceUrl: string | null | undefined): string | null {
  if (!workspaceUrl) return null
  const current = window.location
  if (current.hostname === 'localhost' || current.hostname === '127.0.0.1') return null
  try {
    const workspace = new URL(workspaceUrl)
    if (current.hostname === workspace.hostname) return null
    const target = new URL(`${current.pathname}${current.search}${current.hash}`, workspace.origin)
    return target.toString()
  } catch {
    return null
  }
}

function isMfaSetupPath(pathname: string): boolean {
  return pathname === '/setup/mfa' || pathname === '/setup/2fa'
}

export function useProtectedRoute(
  options: UseProtectedRouteOptions = {},
): UseProtectedRouteResult {
  const fallback = options.fallback ?? '/'
  const requireAdmin = 'requireAdmin' in options && options.requireAdmin === true
  const requireMinRole = 'requireMinRole' in options ? options.requireMinRole : undefined
  const needsRoleCheck = requireAdmin || !!requireMinRole
  const noRoleFallback = needsRoleCheck ? options.noRoleFallback : undefined
  const auth = useAuth()
  const navigate = useNavigate()
  const { user, isPending: userLoading } = useCurrentUser()
  const workspaceRedirectUrl = user ? workspaceHandoffUrl(user.workspace_url) : null
  const currentPath = window.location.pathname
  const blockedByMfaSetup = !!user?.requires_2fa_setup && !isMfaSetupPath(currentPath)

  useEffect(() => {
    if (auth.isLoading) return
    // 1. Unauthenticated - redirect without waiting on /api/me, which is
    //    disabled (and thus perpetually `isPending`) when there is no session.
    if (!auth.isAuthenticated) {
      void navigate({ to: fallback })
      return
    }
    if (userLoading) return
    if (workspaceRedirectUrl) {
      window.location.replace(workspaceRedirectUrl)
      return
    }
    // 2. MFA setup pending - send to the setup flow, but let that flow render.
    if (blockedByMfaSetup) {
      window.location.replace('/setup/mfa')
      return
    }
    // 3. Role-gated route with an insufficient-role caller.
    if (needsRoleCheck && user && !hasRequiredRole(user, options) && noRoleFallback) {
      void navigate({ to: noRoleFallback })
    }
  }, [
    auth.isLoading,
    auth.isAuthenticated,
    user,
    userLoading,
    workspaceRedirectUrl,
    blockedByMfaSetup,
    needsRoleCheck,
    fallback,
    noRoleFallback,
    navigate,
    options,
  ])

  const roleOk = !needsRoleCheck || hasRequiredRole(user, options)
  const isResolving =
    auth.isLoading || !auth.isAuthenticated || userLoading || !!workspaceRedirectUrl || blockedByMfaSetup || !roleOk

  return {
    user,
    isResolving,
    canRender: !isResolving,
  }
}
