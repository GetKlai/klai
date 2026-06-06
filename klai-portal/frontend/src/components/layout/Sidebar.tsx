import { Link, useLocation } from '@tanstack/react-router'
import { useState } from 'react'
import { LayoutGrid, PanelLeftClose, PanelLeftOpen, Shield, type LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { STORAGE_KEYS } from '@/lib/storage'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import * as m from '@/paraglide/messages'

export interface NavItem {
  to?: string
  href?: string
  label: string
  icon: LucideIcon
  end?: boolean
  badgeCount?: number
  children?: NavItem[]
}

interface SidebarProps {
  navItems: NavItem[]
}

export function Sidebar({ navItems }: SidebarProps) {
  const location = useLocation()
  const { user } = useCurrentUser()

  const inAdmin = location.pathname.startsWith('/admin')
  const isAdmin = inAdmin || user?.isAdmin === true

  const [collapsed, setCollapsed] = useState(() => {
    return localStorage.getItem(STORAGE_KEYS.sidebarCollapsed) === 'true'
  })

  const toggle = () => {
    const next = !collapsed
    setCollapsed(next)
    localStorage.setItem(STORAGE_KEYS.sidebarCollapsed, String(next))
  }

  function renderBadge(count: number | undefined, label: string) {
    if (!count || count <= 0) return null
    return (
      <span
        className={cn(
          'ml-auto inline-flex min-w-5 items-center justify-center rounded-full bg-[var(--color-destructive)] px-1.5 text-[11px] font-medium leading-5 text-white',
          collapsed && 'absolute translate-x-3 -translate-y-2 px-1 min-w-4 leading-4 text-[10px]'
        )}
        aria-label={label}
      >
        {count}
      </span>
    )
  }

  return (
    <aside
      role="navigation"
      aria-label="Main navigation"
      className={cn(
        'flex h-screen shrink-0 flex-col bg-[var(--color-sidebar)] border-r border-[var(--color-sidebar-border)] text-[var(--color-sidebar-foreground)] transition-[width] duration-200',
        collapsed ? 'w-14' : 'w-60'
      )}
    >
      {/* Logo + toggle - h-16 + bottom border lines the logo up exactly with
          the global TopBar so the two rails read as one continuous header. */}
      <div className={cn(
        'flex h-16 shrink-0 items-center border-b border-[var(--color-sidebar-border)]',
        collapsed ? 'justify-center' : 'justify-between px-6'
      )}>
        {!collapsed && (
          <Link
            to={inAdmin ? '/admin' : '/app'}
            aria-label={inAdmin ? 'Admin home' : 'App home'}
            className="inline-flex items-center transition-opacity hover:opacity-70"
          >
            <img src="/klai-logo.svg" alt="Klai" className="h-[18px] w-auto block" />
          </Link>
        )}
        <button
          onClick={toggle}
          title={collapsed ? m.sidebar_expand() : m.sidebar_collapse()}
          className={cn(
            'flex items-center justify-center rounded-lg p-1.5 transition-colors',
            'text-[var(--color-sidebar-foreground)]/70 klai-hover hover:text-[var(--color-sidebar-foreground)]',
          )}
        >
          {collapsed
            ? <PanelLeftOpen size={18} strokeWidth={1.5} />
            : <PanelLeftClose size={18} strokeWidth={1.5} />
          }
        </button>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto py-4">
        <ul className="space-y-1">
          {navItems.map((item) => (
            <li key={item.href ?? item.to}>
              {item.href ? (
                <a
                  href={item.href}
                  rel="noopener noreferrer"
                  title={collapsed ? item.label : undefined}
                  className={cn(
                    'relative flex items-center rounded-md py-2 mx-3 text-sm transition-colors',
                    'text-[var(--color-sidebar-foreground)]/70 klai-hover hover:text-[var(--color-sidebar-foreground)]',
                    collapsed ? 'justify-center' : 'gap-3 px-3'
                  )}
                >
                  <item.icon size={18} strokeWidth={1.5} />
                  {!collapsed && item.label}
                  {renderBadge(item.badgeCount, item.label)}
                </a>
              ) : (
                <Link
                  to={item.to ?? '/'}
                  activeOptions={item.end ? { exact: true } : undefined}
                  title={collapsed ? item.label : undefined}
                  className={cn(
                    'relative flex items-center rounded-md py-2 mx-3 text-sm transition-colors',
                    'text-[var(--color-sidebar-foreground)]/70 klai-hover hover:text-[var(--color-sidebar-foreground)]',
                    collapsed ? 'justify-center' : 'gap-3 px-3'
                  )}
                  activeProps={{
                    className: 'bg-[var(--color-hover)] text-[var(--color-sidebar-accent-foreground)]',
                  }}
                >
                  <item.icon size={18} strokeWidth={1.5} />
                  {!collapsed && item.label}
                  {renderBadge(item.badgeCount, item.label)}
                </Link>
              )}
              {item.children && item.children.length > 0 && item.to && location.pathname.startsWith(item.to) && !collapsed && (
                <ul className="mt-1 ml-4 space-y-0.5">
                  {item.children.map((child) => (
                    <li key={child.href ?? child.to}>
                      <Link
                        to={child.to ?? '/'}
                        activeOptions={child.end ? { exact: true } : undefined}
                        className={cn(
                          'flex items-center rounded-md px-3 py-1.5 text-sm transition-colors',
                          'text-[var(--color-sidebar-foreground)]/70 klai-hover hover:text-[var(--color-sidebar-foreground)]',
                          'gap-2'
                        )}
                        activeProps={{
                          className: 'bg-[var(--color-hover)] text-[var(--color-sidebar-accent-foreground)]',
                        }}
                      >
                        <child.icon size={15} strokeWidth={1.5} />
                        {child.label}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      </nav>

      {isAdmin && (
        <div className="border-t border-[var(--color-sidebar-border)] py-3">
          <Link
            to={inAdmin ? '/app' : '/admin'}
            title={collapsed ? (inAdmin ? m.sidebar_go_to_app() : m.sidebar_go_to_admin()) : undefined}
            className={cn(
              'flex items-center rounded-md py-2 mx-3 text-sm transition-colors',
              'text-[var(--color-sidebar-foreground)]/70 klai-hover hover:text-[var(--color-sidebar-foreground)]',
              collapsed ? 'justify-center' : 'gap-3 px-3'
            )}
          >
            {inAdmin
              ? <LayoutGrid size={18} strokeWidth={1.5} />
              : <Shield size={18} strokeWidth={1.5} />
            }
            {!collapsed && (inAdmin ? m.sidebar_go_to_app() : m.sidebar_go_to_admin())}
          </Link>
        </div>
      )}
    </aside>
  )
}
