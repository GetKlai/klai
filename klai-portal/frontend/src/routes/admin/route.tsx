import { createFileRoute, Outlet } from '@tanstack/react-router'
import { LayoutDashboard, Users, FolderKanban, Settings, CreditCard, Puzzle, Key, MessageSquare } from 'lucide-react'
import { Sidebar } from '@/components/layout/Sidebar'
import { HelpButton } from '@/components/help/HelpButton'
import * as m from '@/paraglide/messages'
import { useProtectedRoute } from '@/hooks/useProtectedRoute'

export const Route = createFileRoute('/admin')({
  component: AdminLayout,
})

function AdminLayout() {
  const { canRender } = useProtectedRoute({
    requireAdmin: true,
    noRoleFallback: '/app',
  })

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

  if (!canRender) {
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
