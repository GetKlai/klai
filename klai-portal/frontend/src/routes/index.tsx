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
  const { isLoading, isAuthenticated, activeNavigator } = auth
  const { locale } = useLocale()

  useEffect(() => {
    if (!isLoading && !isAuthenticated && activeNavigator !== 'signoutRedirect') {
      void auth.signinRedirect({ extraQueryParams: { ui_locales: locale } })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- auth.signinRedirect is stable; adding auth would re-run on every render
  }, [isLoading, isAuthenticated, activeNavigator, locale])

  if (isAuthenticated) {
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
