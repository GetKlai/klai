import { createFileRoute, Outlet } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { Sidebar } from '@/components/layout/Sidebar'
import { TopBar, TopBarSlotProvider } from '@/components/layout/TopBar'
import { KlaiAssistantLauncher } from '@/features/klai-assistant/KlaiAssistantLauncher'
import { useProtectedRoute } from '@/hooks/useProtectedRoute'
import { fetchMe } from '@/lib/api-me'
import { getAppNavItems, type AppNavAccess } from './-app-tools'

export const Route = createFileRoute('/app')({
  component: AppLayout,
})

function AppLayout() {
  const { user, canRender } = useProtectedRoute()

  const products = user?.products ?? []
  // Tenant unlocks behind the review queue; reads the same ['me'] cache entry
  // the admin shell uses, and only for callers whose capability allows it.
  const { data: me } = useQuery({
    queryKey: ['me'],
    queryFn: ({ signal }) => fetchMe(signal),
    enabled: user?.hasCapability('kb.activity') === true,
  })

  const access: AppNavAccess = {
    hasCapability: (capability) => user?.hasCapability(capability) === true,
    unlockedFeatures: me?.platform_unlocked_features ?? [],
  }
  const appNav = getAppNavItems(products, access)

  if (!canRender) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--color-background)]">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-rl-accent)] border-t-transparent" />
      </div>
    )
  }

  return (
    <div className="flex h-screen overflow-hidden bg-[var(--color-background)]">
      <Sidebar navItems={appNav} />
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
