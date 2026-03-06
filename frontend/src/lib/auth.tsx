import { AuthProvider } from 'react-oidc-context'
import type { ReactNode } from 'react'

// Configure in .env.local:
//   VITE_OIDC_AUTHORITY=https://auth.getklai.com
//   VITE_OIDC_CLIENT_ID=<client id from Zitadel>
const oidcConfig = {
  authority: import.meta.env.VITE_OIDC_AUTHORITY as string,
  client_id: import.meta.env.VITE_OIDC_CLIENT_ID as string,
  redirect_uri: `${window.location.origin}/callback`,
  post_logout_redirect_uri: `${window.location.origin}/`,
  scope: 'openid profile email',
  // Always call Zitadel end_session on logout (clears Zitadel session too)
  revokeTokensOnSignout: true,
}

export function KlaiAuthProvider({ children }: { children: ReactNode }) {
  return <AuthProvider {...oidcConfig}>{children}</AuthProvider>
}
