import { AuthProvider, useAuth } from 'react-oidc-context'
import { useEffect, type ReactNode } from 'react'
import * as Sentry from '@sentry/react'

// Configure in .env.local:
//   VITE_OIDC_AUTHORITY=https://auth.getklai.com
//   VITE_OIDC_CLIENT_ID=<client id from Zitadel>
const oidcConfig = {
  authority: import.meta.env.VITE_OIDC_AUTHORITY as string,
  client_id: import.meta.env.VITE_OIDC_CLIENT_ID as string,
  redirect_uri: `${window.location.origin}/callback`,
  post_logout_redirect_uri: `${window.location.origin}/logged-out`,
  scope: 'openid profile email',
  // Always call Zitadel end_session on logout (clears Zitadel session too)
  revokeTokensOnSignout: true,
  // Automatically renew the access token before it expires using a hidden iframe.
  // The iframe triggers an OIDC authorize request; Zitadel redirects to /login
  // where sso-complete auto-finalizes using the encrypted klai_sso cookie.
  // This keeps both the portal session and the SSO cookie alive indefinitely
  // (as long as the Zitadel session is valid).
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

export function KlaiAuthProvider({ children }: { children: ReactNode }) {
  return (
    <AuthProvider {...oidcConfig}>
      <SentryUserSync />
      {children}
    </AuthProvider>
  )
}
