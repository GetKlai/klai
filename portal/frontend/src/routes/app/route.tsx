import { createFileRoute, Outlet, useNavigate } from '@tanstack/react-router'
import { useEffect } from 'react'
import { useAuth } from 'react-oidc-context'
import { MessageSquare, Mic, BookOpen, BookMarked } from 'lucide-react'
import { Sidebar } from '@/components/layout/Sidebar'
import * as m from '@/paraglide/messages'
import { API_BASE } from '@/lib/api'
import { authLogger } from '@/lib/logger'

export const Route = createFileRoute('/app')({
  component: AppLayout,
})

function AppLayout() {
  const auth = useAuth()
  const navigate = useNavigate()

  const appNav = [
    { to: '/app/chat', label: m.app_tool_chat_title(), icon: MessageSquare },
    { to: '/app/transcribe', label: m.app_tool_transcribe_title(), icon: Mic },
    { to: '/app/focus', label: m.app_tool_focus_title(), icon: BookOpen },
    { to: '/app/docs', label: m.app_tool_docs_title(), icon: BookMarked },
  ]

  useEffect(() => {
    if (auth.isLoading) return
    if (!auth.isAuthenticated) {
      void navigate({ to: '/' })
      return
    }
    // Re-check 2FA requirement in case user navigated directly here without going through /callback
    fetch(`${API_BASE}/api/me`, {
      headers: { Authorization: `Bearer ${auth.user!.access_token}` },
    })
      .then((res) => (res.ok ? res.json() : null))
      .then((me) => {
        if (me?.requires_2fa_setup) window.location.replace('/setup/2fa')
      })
      .catch((err) => authLogger.warn('2FA re-check failed in app route guard', err))
  }, [auth.isLoading, auth.isAuthenticated, auth.user, navigate])

  if (auth.isLoading || !auth.isAuthenticated) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--color-off-white)]">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-purple-accent)] border-t-transparent" />
      </div>
    )
  }

  return (
    <div className="flex h-screen overflow-hidden bg-[var(--color-background)]">
      <Sidebar navItems={appNav} />
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  )
}
