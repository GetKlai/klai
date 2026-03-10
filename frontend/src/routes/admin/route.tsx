import { createFileRoute, Outlet, useNavigate } from '@tanstack/react-router'
import { useEffect } from 'react'
import { useAuth } from 'react-oidc-context'
import { LayoutDashboard, Users, Settings, CreditCard } from 'lucide-react'
import { Sidebar } from '@/components/layout/Sidebar'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/admin')({
  component: AdminLayout,
})

function AdminLayout() {
  const auth = useAuth()
  const navigate = useNavigate()
  const isAdmin = sessionStorage.getItem('klai:isAdmin') === 'true'

  const adminNav = [
    { to: '/admin', label: m.admin_nav_overview(), icon: LayoutDashboard, end: true },
    { to: '/admin/users', label: m.admin_nav_users(), icon: Users },
    { to: '/admin/billing', label: m.admin_nav_billing(), icon: CreditCard },
    { to: '/admin/settings', label: m.admin_nav_settings(), icon: Settings },
  ]

  useEffect(() => {
    if (!auth.isLoading) {
      if (!auth.isAuthenticated) {
        navigate({ to: '/' })
      } else if (!isAdmin) {
        navigate({ to: '/app' })
      }
    }
  }, [auth.isLoading, auth.isAuthenticated, isAdmin, navigate])

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
    </div>
  )
}
