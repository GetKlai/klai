import { createFileRoute } from '@tanstack/react-router'
import { useEffect } from 'react'
import { useAuth } from 'react-oidc-context'

export const Route = createFileRoute('/')({
  component: LoginPage,
})

function LoginPage() {
  const auth = useAuth()

  useEffect(() => {
    if (!auth.isLoading && !auth.isAuthenticated && auth.activeNavigator !== 'signoutRedirect') {
      auth.signinRedirect()
    }
  }, [auth.isLoading, auth.isAuthenticated, auth.activeNavigator])

  if (auth.isAuthenticated) {
    const isAdmin = sessionStorage.getItem('klai:isAdmin') === 'true'
    window.location.replace(isAdmin ? '/admin' : '/app')
    return null
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--color-off-white)]">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-purple-accent)] border-t-transparent" />
    </div>
  )
}
