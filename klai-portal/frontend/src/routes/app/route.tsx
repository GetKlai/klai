import { createFileRoute, Outlet, useNavigate } from '@tanstack/react-router'
import { useEffect } from 'react'
import { useAuth } from 'react-oidc-context'
import { MessageSquare, BookOpen } from 'lucide-react'
import { Sidebar, type NavItem } from '@/components/layout/Sidebar'
import { SessionBanner } from '@/components/SessionBanner'
import { HelpButton } from '@/components/help/HelpButton'
// i18n messages used by child components via Sidebar
import { useCurrentUser } from '@/hooks/useCurrentUser'

const PRODUCT_ROUTES: Record<string, string[]> = {
  '/app': ['chat'],
  '/app/chat': ['chat'],
  '/app/transcribe': ['scribe'],
  '/app/focus': ['chat'],
  '/app/knowledge': ['knowledge'],
  '/app/docs': ['knowledge'],
  '/app/rules': ['chat'],
}

export const Route = createFileRoute('/app')({
  component: AppLayout,
})

function AppLayout() {
  const auth = useAuth()
  const navigate = useNavigate()
  const { user, isPending: userLoading } = useCurrentUser()

  const allNavItems: NavItem[] = [
    { to: '/app', label: 'Chat', icon: MessageSquare, end: true },
    { to: '/app/knowledge', label: 'Kennis', icon: BookOpen },
  ]

  const isAdmin = user?.isAdmin === true
  const products = user?.products ?? []
  const appNav = isAdmin
    ? allNavItems
    : allNavItems.filter((item) => {
        const required = PRODUCT_ROUTES[item.to ?? '']
        return !required || required.some((p) => products.includes(p))
      })

  useEffect(() => {
    if (auth.isLoading || userLoading) return
    if (!auth.isAuthenticated) {
      void navigate({ to: '/' })
      return
    }
    if (user?.requires_2fa_setup) {
      window.location.replace('/setup/2fa')
    }
  }, [auth.isLoading, auth.isAuthenticated, user, userLoading, navigate])

  if (auth.isLoading || userLoading || !auth.isAuthenticated) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--color-background)]">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-rl-accent)] border-t-transparent" />
      </div>
    )
  }

  return (
    <div className="flex h-screen overflow-hidden bg-[var(--color-background)]">
      <Sidebar navItems={appNav} />
      <main className="flex-1 overflow-y-auto bg-white" style={{ fontFamily: 'Inter, system-ui, sans-serif' }}>
        <SessionBanner />
        <Outlet />
      </main>
      <HelpButton />
    </div>
  )
}
