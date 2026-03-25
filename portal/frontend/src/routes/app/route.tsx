import { createFileRoute, Outlet, useNavigate } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { useAuth } from 'react-oidc-context'
import { MessageSquare, Mic, BookOpen, BookMarked, Brain } from 'lucide-react'
import { Sidebar } from '@/components/layout/Sidebar'
import { HelpButton } from '@/components/help/HelpButton'
import * as m from '@/paraglide/messages'
import { API_BASE } from '@/lib/api'
import { STORAGE_KEYS } from '@/lib/storage'
import { authLogger } from '@/lib/logger'

const PRODUCT_ROUTES: Record<string, string[]> = {
  '/app/chat': ['chat'],
  '/app/transcribe': ['scribe'],
  '/app/focus': ['chat'],
  '/app/knowledge': ['knowledge'],
  '/app/docs': ['knowledge'],
}

function getUserProducts(): string[] {
  try {
    return JSON.parse(sessionStorage.getItem(STORAGE_KEYS.products) ?? '[]') as string[]
  } catch {
    return []
  }
}

export const Route = createFileRoute('/app')({
  component: AppLayout,
})

function AppLayout() {
  const auth = useAuth()
  const navigate = useNavigate()
  const [products, setProducts] = useState<string[]>(getUserProducts)

  const allNavItems = [
    { to: '/app/chat', label: m.app_tool_chat_title(), icon: MessageSquare },
    { to: '/app/transcribe', label: m.app_tool_transcribe_title(), icon: Mic },
    { to: '/app/focus', label: m.app_tool_focus_title(), icon: BookOpen },
    { to: '/app/knowledge', label: m.app_tool_knowledge_title(), icon: Brain },
    { to: '/app/docs', label: m.app_tool_docs_title(), icon: BookMarked },
  ]

  const appNav = allNavItems.filter((item) => {
    const required = PRODUCT_ROUTES[item.to]
    return !required || required.some((p) => products.includes(p))
  })

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
        if (!me) return
        if (me.requires_2fa_setup) window.location.replace('/setup/2fa')
        const refreshed = (me.products as string[] | undefined) ?? []
        sessionStorage.setItem(STORAGE_KEYS.products, JSON.stringify(refreshed))
        setProducts(refreshed)
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
      <HelpButton />
    </div>
  )
}
