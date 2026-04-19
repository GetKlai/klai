import { AuthContext, AuthProvider, useAuth } from 'react-oidc-context'
import { User, WebStorageStateStore } from 'oidc-client-ts'
import { useEffect, useMemo, useRef, type ReactNode } from 'react'
import * as Sentry from '@sentry/react'
import { authLogger } from '@/lib/logger'
import { registerTokenRefresher } from '@/lib/apiFetch'
import { extractOidcErrorCode, isReauthenticationRequired } from '@/lib/oidc-error'

const AUTH_DEV_MODE = import.meta.env.VITE_AUTH_DEV_MODE === 'true'

// Configure in .env.local:
//   VITE_OIDC_AUTHORITY=https://auth.getklai.com
//   VITE_OIDC_CLIENT_ID=<client id from Zitadel>
const oidcConfig = AUTH_DEV_MODE
  ? null
  : {
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
// Dev mode auth provider — bypasses OIDC entirely for local development.
// Requires VITE_AUTH_DEV_MODE=true in .env.local.
// The backend must also have AUTH_DEV_MODE=true and DEBUG=true.
// ---------------------------------------------------------------------------

function DevAuthProvider({ children }: { children: ReactNode }) {
  const mockAuth = useMemo(
    () => ({
      // AuthState
      user: {
        access_token: 'dev-token',
        token_type: 'Bearer',
        profile: { sub: 'dev-user', iss: 'dev', aud: 'dev', exp: 0, iat: 0 },
        expires_in: 99999,
        expired: false,
        scopes: ['openid', 'profile', 'email'],
        toStorageString: () => '',
      } as unknown as User,
      isLoading: false,
      isAuthenticated: true,
      activeNavigator: undefined,
      error: undefined,

      // AuthContextProps methods (no-ops in dev mode)
      settings: {} as never,
      events: {
        addUserSignedOut: () => () => {},
        addUserLoaded: () => () => {},
        addUserUnloaded: () => () => {},
        addSilentRenewError: () => () => {},
        addAccessTokenExpiring: () => () => {},
        addAccessTokenExpired: () => () => {},
      } as never,
      clearStaleState: async () => {},
      removeUser: async () => {},
      signinPopup: () => Promise.resolve({} as User),
      signinSilent: () => Promise.resolve(null),
      signinRedirect: () => Promise.resolve(),
      signinResourceOwnerCredentials: () => Promise.resolve({} as User),
      signoutRedirect: () => Promise.resolve(),
      signoutPopup: () => Promise.resolve(),
      signoutSilent: () => Promise.resolve(),
      querySessionStatus: () => Promise.resolve(null),
      revokeTokens: () => Promise.resolve(),
      startSilentRenew: () => {},
      stopSilentRenew: () => {},
    }),
    [],
  )

  useEffect(() => {
    authLogger.warn(
      '🔓 Auth dev mode active — authentication is bypassed. Never use in production!',
    )
  }, [])

  return <AuthContext.Provider value={mockAuth}>{children}</AuthContext.Provider>
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

/**
 * Guard against invalid sessions from two sources:
 *
 * 1. Cross-tab logout — another tab cleared the localStorage user key
 * 2. Token renewal failure — refresh token expired, revoked, or the OP's
 *    session cookie is gone (iframe silent-renew returns login_required)
 *
 * Both paths call removeUser() exactly once, protected by a shared ref
 * to prevent re-entrant signout loops across tabs. After removeUser, the
 * root route fires signinRedirect on the next render, which is silent if
 * Zitadel still has a session cookie on auth.getklai.com or shows the
 * login form otherwise.
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
    // No active error — clear the signout guard so a future error (after a
    // successful re-authentication in the same tab) is handled again.
    if (!authError) {
      isSigningOut.current = false
      return
    }

    if (isReauthenticationRequired(authError)) {
      if (isSigningOut.current) return
      authLogger.info('Session ended, signing out', {
        error: extractOidcErrorCode(authError),
      })
      isSigningOut.current = true
      void auth.removeUser()
      return
    }

    const errorCode = extractOidcErrorCode(authError) ?? 'unknown'
    authLogger.error('Unexpected OIDC error during token renewal', authError)
    Sentry.captureException(authError, {
      tags: { domain: 'auth', phase: 'silent-renew', error_code: errorCode },
      fingerprint: ['oidc-silent-renew', errorCode],
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- auth.removeUser is stable
  }, [authError])
}

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

/** Register the OIDC signinSilent as the apiFetch token refresher. */
function useTokenRefresherRegistration(): void {
  const auth = useAuth()

  useEffect(() => {
    registerTokenRefresher(async () => {
      const user = await auth.signinSilent()
      return user?.access_token ?? null
    })
  }, [auth])
}

/** Activates all session lifecycle hooks inside the AuthProvider context. */
function AuthSession(): null {
  useSentryUserSync()
  useStaleStateCleanup()
  useSessionGuard()
  useTokenRefresherRegistration()
  return null
}

export function KlaiAuthProvider({ children }: { children: ReactNode }) {
  if (AUTH_DEV_MODE) {
    return <DevAuthProvider>{children}</DevAuthProvider>
  }

  return (
    <AuthProvider {...oidcConfig!}>
      <AuthSession />
      {children}
    </AuthProvider>
  )
}
