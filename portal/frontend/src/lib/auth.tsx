import { AuthProvider, useAuth } from 'react-oidc-context'
import { ErrorResponse } from 'oidc-client-ts'
import { useEffect, type ReactNode } from 'react'
import * as Sentry from '@sentry/react'
import { authLogger } from '@/lib/logger'

// Configure in .env.local:
//   VITE_OIDC_AUTHORITY=https://auth.getklai.com
//   VITE_OIDC_CLIENT_ID=<client id from Zitadel>
const oidcConfig = {
  authority: import.meta.env.VITE_OIDC_AUTHORITY as string,
  client_id: import.meta.env.VITE_OIDC_CLIENT_ID as string,
  redirect_uri: `${window.location.origin}/callback`,
  post_logout_redirect_uri: `${window.location.origin}/logged-out`,
  // offline_access requests a refresh token so react-oidc-context can renew
  // the access token via the token endpoint instead of a fragile hidden iframe.
  // This means session renewal survives Zitadel restarts (refresh tokens are
  // DB-backed; they don't depend on a live Zitadel browser session).
  scope: 'openid profile email offline_access',
  // Always call Zitadel end_session on logout (clears Zitadel session too)
  revokeTokensOnSignout: true,
  // Automatically renew the access token before it expires. With offline_access
  // this uses the refresh token (token endpoint call) rather than a hidden iframe,
  // eliminating the dependency on the Zitadel session being alive.
  automaticSilentRenew: true,
}

function SentryUserSync() {
  const auth = useAuth()
  useEffect(() => {
    if (auth.isAuthenticated && auth.user?.profile) {
      Sentry.setUser({ id: auth.user.profile.sub })
    } else {
      Sentry.setUser(null)
    }
  }, [auth.isAuthenticated, auth.user])
  return null
}

// Handles OIDC token renewal errors.
// Re-authentication errors (invalid_grant, login_required) → sign out so route
// guards redirect to login. Any other error is unexpected: report loudly, don't
// silently sign the user out.
const REAUTHENTICATION_ERRORS = new Set(['invalid_grant', 'login_required'])

function AuthSessionMonitor() {
  const auth = useAuth()
  useEffect(() => {
    if (!auth.error) return
    if (auth.error instanceof ErrorResponse && auth.error.error !== null && REAUTHENTICATION_ERRORS.has(auth.error.error)) {
      authLogger.info('Session ended, signing out', { error: auth.error.error })
      void auth.removeUser()
      return
    }
    authLogger.error('Unexpected OIDC error during token renewal', auth.error)
    Sentry.captureException(auth.error)
  }, [auth.error])
  return null
}

export function KlaiAuthProvider({ children }: { children: ReactNode }) {
  return (
    <AuthProvider {...oidcConfig}>
      <SentryUserSync />
      <AuthSessionMonitor />
      {children}
    </AuthProvider>
  )
}
