import { Link, useLocation } from '@tanstack/react-router'
import { useState } from 'react'
import { useAuth } from 'react-oidc-context'
import { LayoutGrid, LogOut, PanelLeftClose, PanelLeftOpen, Shield, type LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface NavItem {
  to?: string
  href?: string
  label: string
  icon: LucideIcon
  end?: boolean
}

interface SidebarProps {
  navItems: NavItem[]
}

export function Sidebar({ navItems }: SidebarProps) {
  const auth = useAuth()
  const location = useLocation()

  const inAdmin = location.pathname.startsWith('/admin')
  const isAdmin = inAdmin || sessionStorage.getItem('klai:isAdmin') === 'true'

  const [collapsed, setCollapsed] = useState(() => {
    return localStorage.getItem('klai:sidebar-collapsed') === 'true'
  })

  const toggle = () => {
    const next = !collapsed
    setCollapsed(next)
    localStorage.setItem('klai:sidebar-collapsed', String(next))
  }

  return (
    <aside
      className={cn(
        'flex h-screen shrink-0 flex-col bg-[var(--color-sidebar)] text-[var(--color-sidebar-foreground)] transition-[width] duration-200',
        collapsed ? 'w-14' : 'w-60'
      )}
    >
      {/* Logo + toggle */}
      <div className="flex h-16 items-center border-b border-[var(--color-sidebar-border)] px-3">
        {!collapsed && (
          <img src="/klai-logo-white.svg" alt="Klai" className="flex-1 px-3 h-6 w-auto object-contain object-left" />
        )}
        <button
          onClick={toggle}
          title={collapsed ? 'Menu uitklappen' : 'Menu inklappen'}
          className={cn(
            'flex items-center justify-center rounded-lg p-2 transition-colors',
            'text-[var(--color-sidebar-muted-foreground)] hover:bg-[var(--color-sidebar-accent)] hover:text-[var(--color-sidebar-foreground)]',
            collapsed && 'w-full'
          )}
        >
          {collapsed
            ? <PanelLeftOpen size={16} strokeWidth={1.75} />
            : <PanelLeftClose size={16} strokeWidth={1.75} />
          }
        </button>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto px-2 py-4">
        <ul className="space-y-1">
          {navItems.map((item) => (
            <li key={item.href ?? item.to}>
              {item.href ? (
                <a
                  href={item.href}
                  rel="noopener noreferrer"
                  title={collapsed ? item.label : undefined}
                  className={cn(
                    'flex items-center rounded-lg px-3 py-2 text-sm font-medium transition-colors',
                    'text-[var(--color-sidebar-muted-foreground)] hover:bg-[var(--color-sidebar-accent)] hover:text-[var(--color-sidebar-foreground)]',
                    collapsed ? 'justify-center' : 'gap-3'
                  )}
                >
                  <item.icon size={16} strokeWidth={1.75} />
                  {!collapsed && item.label}
                </a>
              ) : (
                <Link
                  to={item.to!}
                  activeOptions={item.end ? { exact: true } : undefined}
                  title={collapsed ? item.label : undefined}
                  className={cn(
                    'flex items-center rounded-lg px-3 py-2 text-sm font-medium transition-colors',
                    'text-[var(--color-sidebar-muted-foreground)] hover:bg-[var(--color-sidebar-accent)] hover:text-[var(--color-sidebar-foreground)]',
                    collapsed ? 'justify-center' : 'gap-3'
                  )}
                  activeProps={{
                    className: 'bg-[var(--color-sidebar-accent)] text-[var(--color-sidebar-foreground)]',
                  }}
                >
                  <item.icon size={16} strokeWidth={1.75} />
                  {!collapsed && item.label}
                </Link>
              )}
            </li>
          ))}
        </ul>
      </nav>

      {/* Admin/App switcher */}
      {isAdmin && (
        <div className="border-t border-[var(--color-sidebar-border)] px-2 py-3">
          <Link
            to={inAdmin ? '/app' : '/admin'}
            title={collapsed ? (inAdmin ? 'Naar App' : 'Beheer') : undefined}
            className={cn(
              'flex items-center rounded-lg px-3 py-2 text-sm font-medium transition-colors',
              'text-[var(--color-sidebar-muted-foreground)] hover:bg-[var(--color-sidebar-accent)] hover:text-[var(--color-sidebar-foreground)]',
              collapsed ? 'justify-center' : 'gap-3'
            )}
          >
            {inAdmin
              ? <LayoutGrid size={16} strokeWidth={1.75} />
              : <Shield size={16} strokeWidth={1.75} />
            }
            {!collapsed && (inAdmin ? 'Naar App' : 'Beheer')}
          </Link>
        </div>
      )}

      {/* User + logout */}
      <div className="border-t border-[var(--color-sidebar-border)] p-2">
        {auth.user && !collapsed && (
          <div className="mb-2 px-3 py-2">
            <p className="truncate text-xs font-medium text-[var(--color-sidebar-foreground)]">
              {auth.user.profile.name ?? auth.user.profile.preferred_username}
            </p>
            <p className="truncate text-xs text-[var(--color-sidebar-muted-foreground)]">
              {auth.user.profile.email}
            </p>
          </div>
        )}
        <button
          onClick={() => auth.signoutRedirect()}
          title={collapsed ? 'Uitloggen' : undefined}
          className={cn(
            'flex w-full items-center rounded-lg px-3 py-2 text-sm font-medium transition-colors',
            'text-[var(--color-sidebar-muted-foreground)] hover:bg-[var(--color-sidebar-accent)] hover:text-[var(--color-sidebar-foreground)]',
            collapsed ? 'justify-center' : 'gap-3'
          )}
        >
          <LogOut size={16} strokeWidth={1.75} />
          {!collapsed && 'Uitloggen'}
        </button>
      </div>
    </aside>
  )
}
