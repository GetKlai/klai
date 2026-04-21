import { createFileRoute } from '@tanstack/react-router'
import { useEffect } from 'react'
import { useAuth } from '@/lib/auth'
import { useLocale } from '@/lib/locale'

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
    window.location.replace('/app')
    return null
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-white">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-gray-900 border-t-transparent" />
    </div>
  )
}
