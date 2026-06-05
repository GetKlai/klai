import { createFileRoute, Outlet } from '@tanstack/react-router'
import { Sidebar } from '@/components/layout/Sidebar'
import { TopBar, TopBarSlotProvider } from '@/components/layout/TopBar'
import { KlaiAssistantLauncher } from '@/features/klai-assistant/KlaiAssistantLauncher'
import { useProtectedRoute } from '@/hooks/useProtectedRoute'
import { getAccessibleAppTools } from './-app-tools'

export const Route = createFileRoute('/app')({
  component: AppLayout,
})

function AppLayout() {
  const { user, canRender } = useProtectedRoute()

  const products = user?.products ?? []
  const appNav = getAccessibleAppTools(products).map((tool) => ({
    to: tool.href,
    label: tool.title(),
    icon: tool.icon,
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
