/**
 * BFF session provider — SPEC-AUTH-008 Phase B.
 *
 * Cookie-based auth replacement for the former react-oidc-context integration.
 * The browser holds one `__Secure-klai_session` cookie (HttpOnly) + a readable
 * `__Secure-klai_csrf` cookie; portal-api does the OIDC + token lifecycle.
 *
 * This file exports only the provider component. Hooks, types, and helpers
 * live in `auth-context.ts` and are re-exported here so consumers keep the
 * stable `@/lib/auth` import surface.
 */

/* eslint-disable react-refresh/only-export-components -- intentional re-export
   of hooks + types from auth-context so consumers stay on @/lib/auth */

import * as Sentry from '@sentry/react'
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { authLogger } from '@/lib/logger'
import {
  AuthContext,
  type AuthContextValue,
  type AuthUser,
  type SessionResponse,
  makeNoopEvents,
  readCsrfCookie,
  redirectToStart,
} from '@/lib/auth-context'

// Re-export non-component surface so consumers import everything from `@/lib/auth`.
export {
  useAuth,
  useSession,
  readCsrfCookie,
  type AuthContextValue,
  type AuthContextProps,
  type AuthUser,
  type User,
  type UserProfile,
} from '@/lib/auth-context'

const AUTH_DEV_MODE = import.meta.env.VITE_AUTH_DEV_MODE === 'true'

// ---------------------------------------------------------------------------
// Dev mode — bypass auth entirely (must match backend AUTH_DEV_MODE=true)
// ---------------------------------------------------------------------------

function DevAuthProvider({ children }: { children: ReactNode }) {
  useEffect(() => {
    authLogger.warn('Auth dev mode active — authentication is bypassed. Never use in production!')
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({
      isLoading: false,
      isAuthenticated: true,
      error: null,
      user: {
        access_token: undefined,
        profile: { sub: 'dev-user' },
        csrf_token: 'dev-csrf',
        access_token_expires_at: Number.MAX_SAFE_INTEGER,
      },
      events: makeNoopEvents(),
      signinRedirect: () => Promise.resolve(),
      removeUser: () => Promise.resolve(),
      signoutRedirect: () => Promise.resolve(),
      signinSilent: () => Promise.resolve(null),
      clearStaleState: () => Promise.resolve(),
      activeNavigator: undefined,
      refetch: () => Promise.resolve(),
    }),
    [],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

// ---------------------------------------------------------------------------
// Production BFF provider — fetches /api/auth/session on mount
// ---------------------------------------------------------------------------

function BffAuthProvider({ children }: { children: ReactNode }) {
  const [isLoading, setIsLoading] = useState(true)
  const [user, setUser] = useState<AuthUser | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const isSigningOut = useRef(false)

  const load = useCallback(async () => {
    try {
      const res = await fetch('/api/auth/session', {
        credentials: 'include',
        headers: { Accept: 'application/json' },
      })
      if (res.status === 401) {
        setUser(null)
        setError(null)
        return
      }
      if (!res.ok) {
        throw new Error(`session endpoint returned ${res.status}`)
      }
      const body = (await res.json()) as SessionResponse
      setUser({
        access_token: undefined,
        profile: { sub: body.zitadel_user_id },
        csrf_token: body.csrf_token,
        access_token_expires_at: body.access_token_expires_at,
      })
      setError(null)
    } catch (err) {
      authLogger.warn('BFF /api/auth/session failed', { error: err })
      setUser(null)
      setError(err instanceof Error ? err : new Error(String(err)))
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  // Sync identity to Sentry for error attribution.
  useEffect(() => {
    if (user) {
      Sentry.setUser({ id: user.profile.sub })
    } else {
      Sentry.setUser(null)
    }
  }, [user])

  const signinRedirect = useCallback((opts: { returnTo?: string } = {}): Promise<void> => {
    redirectToStart(opts.returnTo ?? window.location.pathname)
    return Promise.resolve()
  }, [])

  const removeUser = useCallback(async (): Promise<void> => {
    if (isSigningOut.current) return
    isSigningOut.current = true
    try {
      const csrf = readCsrfCookie() ?? ''
      const res = await fetch('/api/auth/bff/logout', {
        method: 'POST',
        credentials: 'include',
        headers: { 'X-CSRF-Token': csrf },
      })
      const postLogout = res.headers.get('X-Post-Logout-Redirect')
      setUser(null)
      if (postLogout) {
        // RP-initiated logout — hand the browser over to Zitadel for the
        // end_session bounce, which returns to /logged-out.
        window.location.href = postLogout
        return
      }
      window.location.href = '/logged-out'
    } catch (err) {
      authLogger.error('BFF logout failed', err)
      window.location.href = '/logged-out'
    } finally {
      isSigningOut.current = false
    }
  }, [])

  const signoutRedirect = useCallback(
    async (_opts: { post_logout_redirect_uri?: string } = {}): Promise<void> => {
      await removeUser()
    },
    [removeUser],
  )

  const value = useMemo<AuthContextValue>(
    () => ({
      isLoading,
      isAuthenticated: user !== null,
      user,
      error,
      events: makeNoopEvents(),
      signinRedirect,
      removeUser,
      signoutRedirect,
      // Silent renew is server-side in BFF — the client never holds tokens.
      signinSilent: () => Promise.resolve(null),
      clearStaleState: () => Promise.resolve(),
      activeNavigator: undefined,
      refetch: load,
    }),
    [isLoading, user, error, signinRedirect, removeUser, signoutRedirect, load],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

// ---------------------------------------------------------------------------
// Public entry point
// ---------------------------------------------------------------------------

export function KlaiAuthProvider({ children }: { children: ReactNode }) {
  if (AUTH_DEV_MODE) {
    return <DevAuthProvider>{children}</DevAuthProvider>
  }
  return <BffAuthProvider>{children}</BffAuthProvider>
}
