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
  // offline_access gives us a refresh token so renewal uses the token endpoint
  // instead of a hidden iframe — survives Zitadel restarts.
  scope: 'openid profile email offline_access',
  revokeTokensOnSignout: true,
  automaticSilentRenew: true,
  // PKCE (S256) enabled by default in oidc-client-ts v3. Zitadel requires it.
  // localStorage so sessions survive browser restarts and new tabs.
  userStore: new WebStorageStateStore({ store: window.localStorage }),
  // Fire accessTokenExpiring 5 min before expiry for the SessionBanner.
  accessTokenExpiringNotificationTimeInSeconds: 300,
}

// ---------------------------------------------------------------------------
// Session lifecycle hooks
// ---------------------------------------------------------------------------

/** Sync authenticated user identity to Sentry for error attribution. */
function useSentryUserSync(): void {
  const { isAuthenticated, user } = useAuth()

  useEffect(() => {
    if (isAuthenticated && user?.profile) {
      Sentry.setUser({ id: user.profile.sub })
    } else {
      Sentry.setUser(null)
    }
  }, [isAuthenticated, user])
}

/** Remove expired OIDC artifacts (abandoned code_verifier, stale tokens) on mount. */
function useStaleStateCleanup(): void {
  const auth = useAuth()

  useEffect(() => {
    auth.clearStaleState().catch((err: unknown) => {
      authLogger.warn('Failed to clear stale OIDC state', { error: err })
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once on mount
  }, [])
}

const REAUTHENTICATION_ERRORS = new Set(['invalid_grant', 'login_required'])

/**
 * Guard against invalid sessions from two sources:
 *
 * 1. Cross-tab logout — another tab cleared the localStorage user key
 * 2. Token renewal failure — refresh token expired or revoked by Zitadel
 *
 * Both paths call removeUser() exactly once, protected by a shared ref
 * to prevent re-entrant signout loops across tabs.
 */
function useSessionGuard(): void {
  const auth = useAuth()
  const { error: authError } = auth
  const isSigningOut = useRef(false)

  useEffect(() => {
    return auth.events.addUserSignedOut(() => {
      if (auth.isAuthenticated && !isSigningOut.current) {
        isSigningOut.current = true
        authLogger.info('Signed out in another tab')
        void auth.removeUser()
      }
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- auth.events is stable
  }, [auth.isAuthenticated])

  useEffect(() => {
    if (!authError) return

    const isReauthError =
      authError instanceof ErrorResponse &&
      authError.error !== null &&
      REAUTHENTICATION_ERRORS.has(authError.error)

    if (isReauthError) {
      authLogger.info('Session ended, signing out', { error: authError.error })
      isSigningOut.current = true
      void auth.removeUser()
      return
    }

    authLogger.error('Unexpected OIDC error during token renewal', authError)
    Sentry.captureException(authError)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- auth.removeUser is stable
  }, [authError])
}

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

/** Activates all session lifecycle hooks inside the AuthProvider context. */
function AuthSession(): null {
  useSentryUserSync()
  useStaleStateCleanup()
  useSessionGuard()
  return null
}

export function KlaiAuthProvider({ children }: { children: ReactNode }) {
  return (
    <AuthProvider {...oidcConfig}>
      <AuthSession />
      {children}
    </AuthProvider>
  )
}
