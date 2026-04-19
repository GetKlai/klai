import { createFileRoute, Outlet, useNavigate } from '@tanstack/react-router'
import { useEffect } from 'react'
import { useAuth } from '@/lib/auth'
import { LayoutDashboard, Users, FolderKanban, Settings, CreditCard, Puzzle, Key, MessageSquare } from 'lucide-react'
import { Sidebar } from '@/components/layout/Sidebar'
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

  const adminNav = [
    { to: '/admin', label: m.admin_nav_overview(), icon: LayoutDashboard, end: true },
    { to: '/admin/users', label: m.admin_nav_users(), icon: Users },
    { to: '/admin/groups', label: m.admin_nav_groups(), icon: FolderKanban },
    { to: '/admin/billing', label: m.admin_nav_billing(), icon: CreditCard },
    { to: '/admin/api-keys', label: m.admin_nav_api_keys(), icon: Key },
    { to: '/admin/widgets', label: m.admin_nav_widgets(), icon: MessageSquare },
    { to: '/admin/mcps', label: m.admin_nav_mcps(), icon: Puzzle },
    { to: '/admin/settings', label: m.admin_nav_settings(), icon: Settings },
  ]

  useEffect(() => {
    if (auth.isLoading) return
    // Redirect unauthenticated visitors BEFORE waiting on useCurrentUser —
    // it is gated by `enabled: auth.isAuthenticated`, so its isPending
    // stays true forever for logged-out visitors, leaving them stuck on a
    // spinner instead of being bounced to login.
    if (!auth.isAuthenticated) {
      void navigate({ to: '/' })
      return
    }
    if (userLoading) return
    if (!isAdmin && !isGroupAdmin) {
      void navigate({ to: '/app' })
      return
    }
    if (user?.requires_2fa_setup) {
      window.location.replace('/setup/2fa')
    }
  }, [auth.isLoading, auth.isAuthenticated, isAdmin, isGroupAdmin, user, userLoading, navigate])

  if (auth.isLoading || !auth.isAuthenticated || userLoading || (!isAdmin && !isGroupAdmin)) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--color-background)]">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-rl-accent)] border-t-transparent" />
      </div>
    )
  }

  return (
    <div className="flex h-screen overflow-hidden bg-[var(--color-background)]">
      <Sidebar navItems={adminNav} />
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
      <HelpButton />
    </div>
  )
}
