import { AuthProvider, useAuth } from 'react-oidc-context'
import { ErrorResponse, WebStorageStateStore } from 'oidc-client-ts'
import { useEffect, useRef, type ReactNode } from 'react'
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
  // PKCE (S256 code challenge) is enabled by default in oidc-client-ts v3 for
  // authorization code flow (response_type: 'code'). Zitadel requires PKCE for
  // public clients (SPAs). No explicit config needed.
  // Persist tokens in localStorage so sessions survive browser restarts and new
  // tabs. The default (sessionStorage) loses tokens on every browser close.
  userStore: new WebStorageStateStore({ store: window.localStorage }),
  // Fire accessTokenExpiring event 5 minutes before token expires, giving the
  // UI time to show a warning if automaticSilentRenew hasn't kicked in yet.
  accessTokenExpiringNotificationTimeInSeconds: 300,
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
  const { error: authError } = auth
  const isSigningOut = useRef(false)

  // R2: Clean up stale OIDC state (abandoned code_verifier, expired tokens)
  // on app startup. Only removes entries older than staleStateAgeInSeconds (15 min).
  useEffect(() => {
    auth.clearStaleState().catch((err: unknown) => {
      authLogger.warn('Failed to clear stale OIDC state', { error: err })
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once on mount
  }, [])

  // R1: Cross-tab logout synchronization. When a user signs out in one tab,
  // oidc-client-ts detects the localStorage user key was cleared (via the
  // browser storage event) and fires addUserSignedOut in all other tabs.
  useEffect(() => {
    const handleSignedOut = (): void => {
      if (auth.isAuthenticated && !isSigningOut.current) {
        isSigningOut.current = true
        authLogger.info('Signed out in another tab')
        void auth.removeUser()
      }
    }
    return auth.events.addUserSignedOut(handleSignedOut)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- auth.events is stable
  }, [auth.isAuthenticated])

  // Handle OIDC token renewal errors.
  // Re-authentication errors (invalid_grant, login_required) → sign out so route
  // guards redirect to login. Any other error is unexpected: report loudly.
  useEffect(() => {
    if (!authError) return
    if (authError instanceof ErrorResponse && authError.error !== null && REAUTHENTICATION_ERRORS.has(authError.error)) {
      authLogger.info('Session ended, signing out', { error: authError.error })
      isSigningOut.current = true
      void auth.removeUser()
      return
    }
    authLogger.error('Unexpected OIDC error during token renewal', authError)
    Sentry.captureException(authError)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- auth.removeUser is stable; adding auth would re-run on every render
  }, [authError])

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
