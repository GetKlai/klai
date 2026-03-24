import { createFileRoute, Outlet, useNavigate } from '@tanstack/react-router'
import { useEffect } from 'react'
import { useAuth } from 'react-oidc-context'
import { LayoutDashboard, Users, FolderKanban, Settings, CreditCard, Cable } from 'lucide-react'
import { Sidebar } from '@/components/layout/Sidebar'
import { HelpButton } from '@/components/help/HelpButton'
import * as m from '@/paraglide/messages'
import { API_BASE } from '@/lib/api'
import { STORAGE_KEYS } from '@/lib/storage'
import { authLogger } from '@/lib/logger'

export const Route = createFileRoute('/admin')({
  component: AdminLayout,
})

function AdminLayout() {
  const auth = useAuth()
  const navigate = useNavigate()
  const isAdmin = sessionStorage.getItem(STORAGE_KEYS.isAdmin) === 'true'

  const adminNav = [
    { to: '/admin', label: m.admin_nav_overview(), icon: LayoutDashboard, end: true },
    { to: '/admin/users', label: m.admin_nav_users(), icon: Users },
    { to: '/admin/groups', label: m.admin_nav_groups(), icon: FolderKanban },
    { to: '/admin/billing', label: m.admin_nav_billing(), icon: CreditCard },
    { to: '/admin/settings', label: m.admin_nav_settings(), icon: Settings },
    { to: '/admin/connectors', label: m.admin_nav_connectors(), icon: Cable },
  ]

  useEffect(() => {
    if (auth.isLoading) return
    if (!auth.isAuthenticated) {
      void navigate({ to: '/' })
      return
    }
    if (!isAdmin) {
      void navigate({ to: '/app' })
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
      .catch((err) => authLogger.warn('2FA re-check failed in admin route guard', err))
  }, [auth.isLoading, auth.isAuthenticated, isAdmin, auth.user, navigate])

  if (auth.isLoading || !auth.isAuthenticated || !isAdmin) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--color-off-white)]">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-purple-accent)] border-t-transparent" />
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
