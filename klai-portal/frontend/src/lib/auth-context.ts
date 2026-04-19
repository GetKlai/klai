/**
 * Non-component exports of the BFF auth module (SPEC-AUTH-008 Phase B).
 *
 * Kept in a component-free file so ESLint's `react-refresh/only-export-components`
 * rule stays happy on `lib/auth.tsx` (which holds the provider component).
 * Consumers still import everything from `@/lib/auth` — that module re-exports
 * these symbols.
 */

import { createContext, useContext, useMemo } from 'react'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface UserProfile {
  sub: string
  // Optional OIDC claims kept for backward compatibility with the pre-BFF
  // react-oidc-context `user.profile.*` access pattern. With cookie auth we
  // do not hold these client-side; components should fetch them from /api/me
  // via useCurrentUser instead. Undefined is acceptable as a fallback.
  name?: string
  email?: string
  given_name?: string
  family_name?: string
  preferred_username?: string
}

export interface AuthUser {
  access_token: undefined
  profile: UserProfile
  csrf_token: string
  access_token_expires_at: number
}

export interface SessionResponse {
  authenticated: boolean
  zitadel_user_id: string
  csrf_token: string
  access_token_expires_at: number
}

type Unsubscribe = () => void

export interface AuthEvents {
  addUserSignedOut: (cb: () => void) => Unsubscribe
  addUserLoaded: (cb: () => void) => Unsubscribe
  addUserUnloaded: (cb: () => void) => Unsubscribe
  addSilentRenewError: (cb: (err: Error) => void) => Unsubscribe
  addAccessTokenExpiring: (cb: () => void) => Unsubscribe
  addAccessTokenExpired: (cb: () => void) => Unsubscribe
}

export interface AuthContextValue {
  isLoading: boolean
  isAuthenticated: boolean
  user: AuthUser | null
  error: Error | null
  events: AuthEvents
  signinRedirect: (opts?: {
    returnTo?: string
    extraQueryParams?: Record<string, string>
    state?: unknown
  }) => Promise<void>
  removeUser: () => Promise<void>
  signoutRedirect: (opts?: { post_logout_redirect_uri?: string }) => Promise<void>
  signinSilent: () => Promise<null>
  clearStaleState: () => Promise<void>
  activeNavigator: undefined
  refetch: () => Promise<void>
}

// Legacy type alias for call sites migrated from `react-oidc-context`.
export type AuthContextProps = AuthContextValue

// Legacy `User` shape — matches what call sites destructure from `auth.user`.
export type User = AuthUser

// ---------------------------------------------------------------------------
// Context + hooks
// ---------------------------------------------------------------------------

export const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within <KlaiAuthProvider>')
  return ctx
}

/**
 * Preferred hook for new code — same data as useAuth, clearer name, narrower
 * surface (no OIDC-flavoured lifecycle methods). Existing callers keep using
 * useAuth during the migration.
 */
export function useSession() {
  const auth = useAuth()
  return useMemo(
    () => ({
      isLoading: auth.isLoading,
      isAuthenticated: auth.isAuthenticated,
      user: auth.user,
      error: auth.error,
      signinRedirect: auth.signinRedirect,
      signOut: auth.removeUser,
      refetch: auth.refetch,
    }),
    [auth],
  )
}

// ---------------------------------------------------------------------------
// Cookie helpers
// ---------------------------------------------------------------------------

export const CSRF_COOKIE_NAME = '__Secure-klai_csrf'

export function readCsrfCookie(): string | null {
  if (typeof document === 'undefined') return null
  const entry = document.cookie.split(';').find((c) => c.trimStart().startsWith(`${CSRF_COOKIE_NAME}=`))
  if (!entry) return null
  return decodeURIComponent(entry.split('=').slice(1).join('=').trim())
}

// ---------------------------------------------------------------------------
// No-op event facade — shared by the dev and BFF providers.
// ---------------------------------------------------------------------------

export function makeNoopEvents(): AuthEvents {
  const noop = () => () => {}
  return {
    addUserSignedOut: noop,
    addUserLoaded: noop,
    addUserUnloaded: noop,
    addSilentRenewError: noop,
    addAccessTokenExpiring: noop,
    addAccessTokenExpired: noop,
  }
}

// ---------------------------------------------------------------------------
// Redirect helper
// ---------------------------------------------------------------------------

/** Start the OIDC authorisation-code flow via the backend. */
export function redirectToStart(returnTo: string | undefined): void {
  const safe = returnTo && returnTo.startsWith('/') && !returnTo.startsWith('//') ? returnTo : '/app'
  const url = `/api/auth/oidc/start?return_to=${encodeURIComponent(safe)}`
  window.location.href = url
}
