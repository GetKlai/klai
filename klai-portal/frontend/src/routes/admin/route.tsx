import { createFileRoute, Outlet } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { Sidebar } from '@/components/layout/Sidebar'
import { TopBar, TopBarSlotProvider } from '@/components/layout/TopBar'
import { KlaiAssistantLauncher } from '@/features/klai-assistant/KlaiAssistantLauncher'
import { useProtectedRoute } from '@/hooks/useProtectedRoute'
import { useAuth } from '@/lib/auth'
import { fetchMe } from '@/lib/api-me'
import { apiFetch } from '@/lib/apiFetch'
import { ADMIN_NAV_ITEMS, adminNavItemIsVisible } from './-nav'

export const Route = createFileRoute('/admin')({
  component: AdminLayout,
})

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
  const { data: platformStats } = useQuery({
    queryKey: ['platform-stats'],
    queryFn: () =>
      apiFetch<{
        new_feedback_count: number
        unread_message_count: number
        chat_error_count: number
      }>(
        '/api/admin/platform/stats',
      ),
    enabled: auth.isAuthenticated && me?.is_platform_admin === true,
    staleTime: 30_000,
  })

  // Filter nav items: role-check first, then platform-unlock-check.
  // The platform-unlock filter applies uniformly - including to platform-admin
  // callers. Platform-admins managing other tenants do so via the Uitbreidingen
  // tenant-picker on /admin/settings; their own sidebar/tegels mirror what a
  // normal admin in their own tenant would see (emulation view). Without this
  // alignment, the sidebar contradicts the Uitbreidingen status panel.
  const effectiveRole = user?.effective_role
  const platformAlertCount =
    (platformStats?.new_feedback_count ?? 0) +
    (platformStats?.unread_message_count ?? 0) +
    (platformStats?.chat_error_count ?? 0)

  const adminNav = ADMIN_NAV_ITEMS.filter((item) =>
    adminNavItemIsVisible(item, effectiveRole, me),
  ).map(({ to, label, icon, end }) => ({
    to,
    label,
    icon,
    end,
    badgeCount: to === '/admin/platform' ? platformAlertCount : undefined,
  }))

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
      <TopBarSlotProvider>
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          <TopBar />
          <main className="flex-1 overflow-y-auto">
            <Outlet />
          </main>
        </div>
      </TopBarSlotProvider>
      <KlaiAssistantLauncher />
    </div>
  )
}
