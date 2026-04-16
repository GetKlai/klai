import { createFileRoute, Outlet, useNavigate } from '@tanstack/react-router'
import { useEffect } from 'react'
import { useAuth } from 'react-oidc-context'
import { MessageSquare, BookOpen, Sliders, Scale, Users, Puzzle, CreditCard, Settings } from 'lucide-react'
import { Sidebar, type NavItem } from '@/components/layout/Sidebar'
import { HelpButton } from '@/components/help/HelpButton'
import * as m from '@/paraglide/messages'
import { useCurrentUser } from '@/hooks/useCurrentUser'

export const Route = createFileRoute('/admin')({
  component: AdminLayout,
})

function AdminLayout() {
  const auth = useAuth()
  const navigate = useNavigate()
  const { user, isPending: userLoading } = useCurrentUser()
  const isAdmin = user?.isAdmin === true
  const isGroupAdmin = user?.isGroupAdmin === true

  /* Same nav structure as the app layout for a consistent sidebar */
  const adminNav: NavItem[] = [
    { to: '/app', label: 'Chat', icon: MessageSquare, end: true },
    { to: '/app/knowledge', label: m.sidebar_knowledge(), icon: BookOpen },
    { to: '/app/templates', label: 'Templates', icon: Sliders },
    { to: '/app/rules', label: m.sidebar_rules(), icon: Scale },
    { to: '/admin/users', label: m.sidebar_team(), icon: Users },
    { to: '/admin/mcps', label: m.sidebar_mcps(), icon: Puzzle },
  ]

  const accountItems: NavItem[] = [
    { to: '/admin/billing', label: m.admin_nav_billing(), icon: CreditCard },
    { to: '/admin/settings', label: m.admin_nav_settings(), icon: Settings },
  ]

  useEffect(() => {
    if (auth.isLoading || userLoading) return
    if (!auth.isAuthenticated) {
      void navigate({ to: '/' })
      return
    }
    if (!isAdmin && !isGroupAdmin) {
      void navigate({ to: '/app' })
      return
    }
    if (user?.requires_2fa_setup) {
      window.location.replace('/setup/2fa')
    }
  }, [auth.isLoading, auth.isAuthenticated, isAdmin, isGroupAdmin, user, userLoading, navigate])

  if (auth.isLoading || userLoading || !auth.isAuthenticated || (!isAdmin && !isGroupAdmin)) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-white">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-gray-900 border-t-transparent" />
      </div>
    )
  }

  return (
    <div className="flex h-screen overflow-hidden bg-white">
      <Sidebar navItems={adminNav} accountItems={accountItems} />
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
      <HelpButton />
    </div>
  )
}
