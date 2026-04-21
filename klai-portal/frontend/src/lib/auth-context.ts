/**
 * Non-component exports of the BFF auth module (SPEC-AUTH-008 Phase B).
 *
 * Kept in a component-free file so ESLint's `react-refresh/only-export-components`
 * rule stays happy on `lib/auth.tsx` (which holds the provider component).
 * Consumers still import everything from `@/lib/auth` — that module re-exports
 * these symbols.
 */

import { createContext, useContext } from 'react'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface UserProfile {
  sub: string
  // Optional OIDC claims kept for forward compatibility with callers that
  // want basic identity without a separate /api/me fetch. BFF does not surface
  // these server-side yet, but they are consumed by e.g. the sidebar.
  name?: string
  email?: string
  given_name?: string
  family_name?: string
  preferred_username?: string
}

export interface AuthUser {
  profile: UserProfile
  csrf_token: string
  /** Epoch seconds. Server-side refresh runs before expiry; client uses this
   *  only for diagnostics and stale-session hints. No tokens are held here. */
  access_token_expires_at: number
}

export interface SessionResponse {
  authenticated: boolean
  zitadel_user_id: string
  csrf_token: string
  access_token_expires_at: number
}

export interface AuthContextValue {
  isLoading: boolean
  isAuthenticated: boolean
  user: AuthUser | null
  error: Error | null
  signinRedirect: (opts?: {
    returnTo?: string
    extraQueryParams?: Record<string, string>
  }) => Promise<void>
  removeUser: () => Promise<void>
  signoutRedirect: () => Promise<void>
  refetch: () => Promise<void>
}

// ---------------------------------------------------------------------------
// Context + hooks
// ---------------------------------------------------------------------------

export const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within <KlaiAuthProvider>')
  return ctx
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
// Redirect helper
// ---------------------------------------------------------------------------

/** Start the OIDC authorisation-code flow via the backend. */
export function redirectToStart(returnTo: string | undefined): void {
  const safe = returnTo && returnTo.startsWith('/') && !returnTo.startsWith('//') ? returnTo : '/app'
  const url = `/api/auth/oidc/start?return_to=${encodeURIComponent(safe)}`
  window.location.href = url
}
