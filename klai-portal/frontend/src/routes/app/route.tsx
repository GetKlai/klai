import { createFileRoute, Outlet, useNavigate } from '@tanstack/react-router'
import { useEffect } from 'react'
import { MessageSquare, BookOpen, Sliders, Scale, Users, Puzzle } from 'lucide-react'
import { Sidebar, type NavItem } from '@/components/layout/Sidebar'
import { HelpButton } from '@/components/help/HelpButton'
import { useAuth } from '@/lib/auth'
import * as m from '@/paraglide/messages'
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
  const navigate = useNavigate()
  const { isLoading, isAuthenticated } = useAuth()
  const { user, isPending: userLoading } = useCurrentUser()

  const productNavItems: NavItem[] = [
    { to: '/app', label: 'Chat', icon: MessageSquare, end: true },
    { to: '/app/knowledge', label: m.sidebar_knowledge(), icon: BookOpen },
    { to: '/app/templates', label: 'Templates', icon: Sliders },
    { to: '/app/rules', label: m.sidebar_rules(), icon: Scale },
  ]

  const adminNavItems: NavItem[] = [
    { to: '/admin/users', label: m.sidebar_team(), icon: Users },
    { to: '/admin/mcps', label: m.sidebar_mcps(), icon: Puzzle },
  ]


  const isAdmin = user?.isAdmin === true
  const isGroupAdmin = user?.isGroupAdmin === true
  const products = user?.products ?? []
  const filteredProductNav = isAdmin
    ? productNavItems
    : productNavItems.filter((item) => {
        const required = PRODUCT_ROUTES[item.to ?? '']
        return !required || required.some((p) => products.includes(p))
      })
  const appNav = (isAdmin || isGroupAdmin)
    ? [...filteredProductNav, ...adminNavItems]
    : filteredProductNav

  useEffect(() => {
    if (isLoading || userLoading) return
    if (!isAuthenticated) {
      void navigate({ to: '/' })
      return
    }
    if (user?.requires_2fa_setup) {
      window.location.replace('/setup/2fa')
    }
  }, [isLoading, isAuthenticated, user, userLoading, navigate])

  if (isLoading || userLoading || !isAuthenticated) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-white">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-gray-900 border-t-transparent" />
      </div>
    )
  }

  return (
    <div className="flex h-screen overflow-hidden bg-white">
      <Sidebar navItems={appNav} />
      <main className="flex-1 overflow-y-auto bg-white">
                <Outlet />
      </main>
      <HelpButton />
    </div>
  )
}
