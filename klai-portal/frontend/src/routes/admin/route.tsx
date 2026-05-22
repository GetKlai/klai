import { createFileRoute, Outlet } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { LayoutDashboard, Users, FolderKanban, Settings, CreditCard, Puzzle, Key, MessageSquare, Skull, ShieldCheck, Globe2, type LucideIcon } from 'lucide-react'
import { Sidebar } from '@/components/layout/Sidebar'
import { HelpButton } from '@/components/help/HelpButton'
import * as m from '@/paraglide/messages'
import { useProtectedRoute } from '@/hooks/useProtectedRoute'
import { meetsMinRole, type ProfileRole } from '@/lib/profiles'
import { useAuth } from '@/lib/auth'
import { fetchMe } from '@/lib/api-me'

export const Route = createFileRoute('/admin')({
  component: AdminLayout,
})

// SPEC-PORTAL-PROFILES-001 P3.2: per-tab minimum role on the admin sidebar.
// kb_manager and group_manager can enter /admin for groups + templates.
// Everything else remains admin-only.
//
// SPEC-PORTAL-EXTENSIONS-UNIFY-001 Phase 4: api-keys, widgets, and mcps are
// additionally platform-unlock-gated via `requiresFeature`. The nav-item is
// hidden when the caller's org does not have the feature unlocked, unless
// the caller is a platform-admin (Klai staff always sees everything).
const ADMIN_NAV_ITEMS: Array<{
  to: string
  label: string
  icon: LucideIcon
  minRole: ProfileRole
  end?: boolean
  requiresFeature?: string
  platformAdminOnly?: boolean
}> = [
  { to: '/admin', label: m.admin_nav_overview(), icon: LayoutDashboard, minRole: 'kb_manager', end: true },
  // SPEC-PLATFORM-ADMIN-001: cross-tenant console, alleen voor Klai-staff
  // (is_platform_admin). Bovenaan zodat het opvalt voor wie het mag zien.
  { to: '/admin/platform', label: 'Platform', icon: Globe2, minRole: 'admin', platformAdminOnly: true },
  { to: '/admin/users', label: m.admin_nav_users(), icon: Users, minRole: 'admin' },
  // SPEC-PORTAL-ADMIN-UI-001 REQ-11: Profiles between Users and Groups.
  { to: '/admin/profiles', label: m.admin_nav_profiles(), icon: ShieldCheck, minRole: 'admin' },
  { to: '/admin/groups', label: m.admin_nav_groups(), icon: FolderKanban, minRole: 'group_manager' },
  { to: '/admin/billing', label: m.admin_nav_billing(), icon: CreditCard, minRole: 'admin' },
  { to: '/admin/api-keys', label: m.admin_nav_api_keys(), icon: Key, minRole: 'admin', requiresFeature: 'partner_api' },
  { to: '/admin/widgets', label: m.admin_nav_widgets(), icon: MessageSquare, minRole: 'admin', requiresFeature: 'widgets' },
  // Templates is samengevoegd met /app/templates (één pagina, scope-tabs,
  // per-rij edit-rechten via canMutate). De /admin/templates routes
  // redirecten naar de /app variant. Admins zien Templates voortaan in
  // het /app sidebar.
  { to: '/admin/mcps', label: m.admin_nav_mcps(), icon: Puzzle, minRole: 'admin', requiresFeature: 'custom_mcps' },
  { to: '/admin/settings', label: m.admin_nav_settings(), icon: Settings, minRole: 'admin' },
  { to: '/admin/danger-zone', label: m.admin_nav_danger_zone(), icon: Skull, minRole: 'admin' },
]

function AdminLayout() {
  // Allow entry when user meets the minimum of all nav item thresholds (i.e., kb_manager).
  const { user, canRender } = useProtectedRoute({
    requireMinRole: 'kb_manager',
    noRoleFallback: '/app',
  })

  const auth = useAuth()
  const { data: me } = useQuery({
    queryKey: ['me'],
    queryFn: ({ signal }) => fetchMe(signal),
    enabled: auth.isAuthenticated,
  })

  // Filter nav items: role-check first, then platform-unlock-check.
  // The platform-unlock filter applies uniformly — including to platform-admin
  // callers. Platform-admins managing other tenants do so via the Uitbreidingen
  // tenant-picker on /admin/settings; their own sidebar/tegels mirror what a
  // normal admin in their own tenant would see (emulation view). Without this
  // alignment, the sidebar contradicts the Uitbreidingen status panel.
  const effectiveRole = user?.effective_role
  const unlocked = me?.platform_unlocked_features ?? []

  const adminNav = ADMIN_NAV_ITEMS.filter((item) => {
    if (!meetsMinRole(effectiveRole, item.minRole)) return false
    // Platform-admin-only items (cross-tenant console) hidden for
    // everyone except Klai staff in the platform org.
    if (item.platformAdminOnly && !me?.is_platform_admin) return false
    if (item.requiresFeature) {
      // While /api/me is loading, keep the item visible to avoid a flash of
      // missing nav.
      if (!me) return true
      return unlocked.includes(item.requiresFeature)
    }
    return true
  }).map(({ to, label, icon, end }) => ({ to, label, icon, end }))

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
