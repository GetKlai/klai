import { AuthProvider, useAuth } from 'react-oidc-context'
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

// Clears stale auth state when the OIDC session expires or silent renewal fails.
// After removeUser(), auth.isAuthenticated becomes false and the route guards
// redirect to login automatically.
function AuthSessionMonitor() {
  const auth = useAuth()
  useEffect(() => {
    if (!auth.error) return
    authLogger.warn('Auth error detected (silent renew failed or session expired), clearing auth state', auth.error)
    void auth.removeUser()
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
