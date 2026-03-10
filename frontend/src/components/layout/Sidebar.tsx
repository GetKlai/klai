import { Link, useLocation } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { LayoutGrid, LogOut, Shield, type LucideIcon } from 'lucide-react'
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

  return (
    <aside className="flex h-screen w-60 shrink-0 flex-col bg-[var(--color-sidebar)] text-[var(--color-sidebar-foreground)]">
      {/* Logo */}
      <div className="flex h-16 items-center border-b border-[var(--color-sidebar-border)] px-6">
        <span className="font-serif text-xl font-bold tracking-tight text-[var(--color-sand-light)]">
          Klai
        </span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto px-3 py-4">
        <ul className="space-y-1">
          {navItems.map((item) => (
            <li key={item.href ?? item.to}>
              {item.href ? (
                <a
                  href={item.href}
                  rel="noopener noreferrer"
                  className={cn(
                    'flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
                    'text-[var(--color-sidebar-muted-foreground)] hover:bg-[var(--color-sidebar-accent)] hover:text-[var(--color-sidebar-foreground)]'
                  )}
                >
                  <item.icon size={16} strokeWidth={1.75} />
                  {item.label}
                </a>
              ) : (
                <Link
                  to={item.to!}
                  activeOptions={item.end ? { exact: true } : undefined}
                  className={cn(
                    'flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
                    'text-[var(--color-sidebar-muted-foreground)] hover:bg-[var(--color-sidebar-accent)] hover:text-[var(--color-sidebar-foreground)]'
                  )}
                  activeProps={{
                    className: 'bg-[var(--color-sidebar-accent)] text-[var(--color-sidebar-foreground)]',
                  }}
                >
                  <item.icon size={16} strokeWidth={1.75} />
                  {item.label}
                </Link>
              )}
            </li>
          ))}
        </ul>
      </nav>

      {/* Admin/App switcher — only visible to admins */}
      {isAdmin && (
        <div className="border-t border-[var(--color-sidebar-border)] px-3 py-3">
          <Link
            to={inAdmin ? '/app' : '/admin'}
            className={cn(
              'flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
              'text-[var(--color-sidebar-muted-foreground)] hover:bg-[var(--color-sidebar-accent)] hover:text-[var(--color-sidebar-foreground)]'
            )}
          >
            {inAdmin ? (
              <LayoutGrid size={16} strokeWidth={1.75} />
            ) : (
              <Shield size={16} strokeWidth={1.75} />
            )}
            {inAdmin ? 'Naar App' : 'Beheer'}
          </Link>
        </div>
      )}

      {/* User + logout */}
      <div className="border-t border-[var(--color-sidebar-border)] p-3">
        {auth.user && (
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
          className={cn(
            'flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
            'text-[var(--color-sidebar-muted-foreground)] hover:bg-[var(--color-sidebar-accent)] hover:text-[var(--color-sidebar-foreground)]'
          )}
        >
          <LogOut size={16} strokeWidth={1.75} />
          Uitloggen
        </button>
      </div>
    </aside>
  )
}
