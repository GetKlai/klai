import { createFileRoute, Outlet } from '@tanstack/react-router'
import { useEffect } from 'react'
import { useAuth } from '@/lib/auth'
import { MessageSquare, Mic, BookOpen, BookMarked, Brain } from 'lucide-react'
import { Sidebar } from '@/components/layout/Sidebar'
import { SessionBanner } from '@/components/SessionBanner'
import { HelpButton } from '@/components/help/HelpButton'
import * as m from '@/paraglide/messages'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { useAuthGuardRedirect } from '@/hooks/useAuthGuardRedirect'

const PRODUCT_ROUTES: Record<string, string[]> = {
  '/app/chat': ['chat'],
  '/app/transcribe': ['scribe'],
  '/app/focus': ['chat'],
  '/app/knowledge': ['knowledge'],
  '/app/docs': ['knowledge'],
}

export const Route = createFileRoute('/app')({
  component: AppLayout,
})

function AppLayout() {
  const auth = useAuth()
  const { user, isPending: userLoading } = useCurrentUser()
  useAuthGuardRedirect()

  const allNavItems = [
    { to: '/app/chat', label: m.app_tool_chat_title(), icon: MessageSquare },
    { to: '/app/transcribe', label: m.app_tool_transcribe_title(), icon: Mic },
    { to: '/app/focus', label: m.app_tool_focus_title(), icon: BookOpen },
    { to: '/app/knowledge', label: m.app_tool_knowledge_title(), icon: Brain },
    { to: '/app/docs', label: m.app_tool_docs_title(), icon: BookMarked },
  ]

  const isAdmin = user?.isAdmin === true
  const products = user?.products ?? []
  const appNav = isAdmin
    ? allNavItems
    : allNavItems.filter((item) => {
        const required = PRODUCT_ROUTES[item.to]
        return !required || required.some((p) => products.includes(p))
      })

  useEffect(() => {
    if (auth.isLoading || !auth.isAuthenticated || userLoading) return
    if (user?.requires_2fa_setup) {
      window.location.replace('/setup/2fa')
    }
  }, [auth.isLoading, auth.isAuthenticated, user, userLoading])

  if (auth.isLoading || !auth.isAuthenticated || userLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--color-background)]">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-rl-accent)] border-t-transparent" />
      </div>
    )
  }

  return (
    <div className="flex h-screen overflow-hidden bg-[var(--color-background)]">
      <Sidebar navItems={appNav} />
      <main className="flex-1 overflow-y-auto">
        <SessionBanner />
        <Outlet />
      </main>
      <HelpButton />
    </div>
  )
}
