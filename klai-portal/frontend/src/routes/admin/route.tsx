import { createFileRoute, Outlet } from '@tanstack/react-router'
import { LayoutDashboard, Users, FolderKanban, Settings, CreditCard, Puzzle, Key, MessageSquare, Sliders, Skull, type LucideIcon } from 'lucide-react'
import { Sidebar } from '@/components/layout/Sidebar'
import { HelpButton } from '@/components/help/HelpButton'
import * as m from '@/paraglide/messages'
import { useProtectedRoute } from '@/hooks/useProtectedRoute'
import { meetsMinRole, type ProfileRole } from '@/lib/profiles'

export const Route = createFileRoute('/admin')({
  component: AdminLayout,
})

// SPEC-PORTAL-PROFILES-001 P3.2: per-tab minimum role on the admin sidebar.
// kb_manager and group_manager can enter /admin for groups + templates.
// Everything else remains admin-only.
const ADMIN_NAV_ITEMS: Array<{ to: string; label: string; icon: LucideIcon; minRole: ProfileRole; end?: boolean }> = [
  { to: '/admin', label: m.admin_nav_overview(), icon: LayoutDashboard, minRole: 'kb_manager', end: true },
  { to: '/admin/users', label: m.admin_nav_users(), icon: Users, minRole: 'admin' },
  { to: '/admin/groups', label: m.admin_nav_groups(), icon: FolderKanban, minRole: 'group_manager' },
  { to: '/admin/billing', label: m.admin_nav_billing(), icon: CreditCard, minRole: 'admin' },
  { to: '/admin/api-keys', label: m.admin_nav_api_keys(), icon: Key, minRole: 'admin' },
  { to: '/admin/widgets', label: m.admin_nav_widgets(), icon: MessageSquare, minRole: 'admin' },
  { to: '/admin/templates', label: m.admin_nav_templates(), icon: Sliders, minRole: 'kb_manager' },
  { to: '/admin/mcps', label: m.admin_nav_mcps(), icon: Puzzle, minRole: 'admin' },
  { to: '/admin/settings', label: m.admin_nav_settings(), icon: Settings, minRole: 'admin' },
  { to: '/admin/danger-zone', label: m.admin_nav_danger_zone(), icon: Skull, minRole: 'admin' },
]

function AdminLayout() {
  // Allow entry when user meets the minimum of all nav item thresholds (i.e., kb_manager).
  const { user, canRender } = useProtectedRoute({
    requireMinRole: 'kb_manager',
    noRoleFallback: '/app',
  })

  // Filter nav items to only show what this user's role allows.
  const effectiveRole = user?.effective_role
  const adminNav = ADMIN_NAV_ITEMS
    .filter((item) => meetsMinRole(effectiveRole, item.minRole))
    .map(({ to, label, icon, end }) => ({ to, label, icon, end }))

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
