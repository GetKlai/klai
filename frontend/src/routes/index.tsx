import { createFileRoute } from '@tanstack/react-router'
import { useEffect } from 'react'
import { useAuth } from 'react-oidc-context'
import { useLocale } from '@/lib/locale'
import { STORAGE_KEYS } from '@/lib/storage'

export const Route = createFileRoute('/')({
  component: LoginPage,
})

function LoginPage() {
  const auth = useAuth()
  const { locale } = useLocale()

  useEffect(() => {
    if (!auth.isLoading && !auth.isAuthenticated && auth.activeNavigator !== 'signoutRedirect') {
      auth.signinRedirect({ extraQueryParams: { ui_locales: locale } })
    }
  }, [auth.isLoading, auth.isAuthenticated, auth.activeNavigator, locale])

  if (auth.isAuthenticated) {
    const isAdmin = sessionStorage.getItem(STORAGE_KEYS.isAdmin) === 'true'
    window.location.replace(isAdmin ? '/admin' : '/app')
    return null
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--color-off-white)]">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-purple-accent)] border-t-transparent" />
    </div>
  )
}
