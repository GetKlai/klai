import { Link, useLocation } from '@tanstack/react-router'
import { useState } from 'react'
import { useAuth } from 'react-oidc-context'
import { LayoutGrid, LogOut, PanelLeftClose, PanelLeftOpen, Shield, UserCircle, type LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useLocale } from '@/lib/locale'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { STORAGE_KEYS } from '@/lib/storage'
import * as m from '@/paraglide/messages'

export interface NavItem {
  to?: string
  href?: string
  label: string
  icon: LucideIcon
  end?: boolean
  children?: NavItem[]
}

interface SidebarProps {
  navItems: NavItem[]
}

export function Sidebar({ navItems }: SidebarProps) {
  const auth = useAuth()
  const location = useLocation()
  const { locale, switchLocale } = useLocale()
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

  return (
    <aside
      role="navigation"
      aria-label="Main navigation"
      className={cn(
        'flex h-screen shrink-0 flex-col bg-[var(--color-sidebar)] border-r border-[var(--color-sidebar-border)] text-[var(--color-sidebar-foreground)] transition-[width] duration-200',
        collapsed ? 'w-14' : 'w-60'
      )}
    >
      {/* Logo + toggle — h-[66px] centers content at 33px → logo top-edge at 24px */}
      <div className={cn(
        'flex h-[66px] items-center',
        collapsed ? 'justify-center' : 'justify-between px-6'
      )}>
        {!collapsed && (
          <img src="/klai-logo.svg" alt="Klai" className="h-[18px] w-auto block" />
        )}
        <button
          onClick={toggle}
          title={collapsed ? m.sidebar_expand() : m.sidebar_collapse()}
          className={cn(
            'flex items-center justify-center rounded-lg p-1.5 transition-colors',
            'text-[var(--color-sidebar-foreground)]/70 hover:bg-[var(--color-sidebar-accent)] hover:text-[var(--color-sidebar-foreground)]',
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
                    'flex items-center rounded-md py-2 mx-3 text-sm transition-colors',
                    'text-[var(--color-sidebar-foreground)]/70 hover:bg-[var(--color-sidebar-accent)] hover:text-[var(--color-sidebar-foreground)]',
                    collapsed ? 'justify-center' : 'gap-3 px-3'
                  )}
                >
                  <item.icon size={18} strokeWidth={1.5} />
                  {!collapsed && item.label}
                </a>
              ) : (
                <Link
                  to={item.to ?? '/'}
                  activeOptions={item.end ? { exact: true } : undefined}
                  title={collapsed ? item.label : undefined}
                  className={cn(
                    'flex items-center rounded-md py-2 mx-3 text-sm transition-colors',
                    'text-[var(--color-sidebar-foreground)]/70 hover:bg-[var(--color-sidebar-accent)] hover:text-[var(--color-sidebar-foreground)]',
                    collapsed ? 'justify-center' : 'gap-3 px-3'
                  )}
                  activeProps={{
                    className: 'bg-[var(--color-sidebar-accent)] text-[var(--color-sidebar-accent-foreground)]',
                  }}
                >
                  <item.icon size={18} strokeWidth={1.5} />
                  {!collapsed && item.label}
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
                          'text-[var(--color-sidebar-foreground)]/70 hover:bg-[var(--color-sidebar-accent)] hover:text-[var(--color-sidebar-foreground)]',
                          'gap-2'
                        )}
                        activeProps={{
                          className: 'bg-[var(--color-sidebar-accent)] text-[var(--color-sidebar-accent-foreground)]',
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

      {/* Admin/App switcher */}
      {isAdmin && (
        <div className="border-t border-[var(--color-sidebar-border)] py-3">
          <Link
            to={inAdmin ? '/app' : '/admin'}
            title={collapsed ? (inAdmin ? m.sidebar_go_to_app() : m.sidebar_go_to_admin()) : undefined}
            className={cn(
              'flex items-center rounded-md py-2 mx-3 text-sm transition-colors',
              'text-[var(--color-sidebar-foreground)]/70 hover:bg-[var(--color-sidebar-accent)] hover:text-[var(--color-sidebar-foreground)]',
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

      {/* Locale switcher */}
      <div className={cn(
        'border-t border-[var(--color-sidebar-border)] py-2',
        collapsed ? 'flex justify-center' : 'flex items-center gap-1 px-6'
      )}>
        {collapsed ? (
          <button
            onClick={() => switchLocale(locale === 'nl' ? 'en' : 'nl')}
            title={locale === 'nl' ? 'Switch to English' : 'Wisselen naar Nederlands'}
            className="text-xs font-medium text-[var(--color-sidebar-foreground)]/70 hover:text-[var(--color-sidebar-foreground)] transition-colors"
          >
            {locale.toUpperCase()}
          </button>
        ) : (
          <>
            <button
              onClick={() => switchLocale('nl')}
              className={cn(
                'text-xs transition-colors',
                locale === 'nl'
                  ? 'font-semibold text-[var(--color-sidebar-foreground)]'
                  : 'opacity-40 hover:opacity-70 text-[var(--color-sidebar-muted-foreground)]'
              )}
            >
              NL
            </button>
            <span className="text-xs opacity-30 text-[var(--color-sidebar-muted-foreground)]">/</span>
            <button
              onClick={() => switchLocale('en')}
              className={cn(
                'text-xs transition-colors',
                locale === 'en'
                  ? 'font-semibold text-[var(--color-sidebar-foreground)]'
                  : 'opacity-40 hover:opacity-70 text-[var(--color-sidebar-muted-foreground)]'
              )}
            >
              EN
            </button>
          </>
        )}
      </div>

      {/* User + logout */}
      <div className="border-t border-[var(--color-sidebar-border)] pt-2 pb-4">
        {auth.user && !collapsed && (
          <div className="mb-2 px-6 py-2">
            <p className="truncate text-xs font-medium text-[var(--color-sidebar-foreground)]">
              {auth.user.profile.name ?? auth.user.profile.preferred_username}
            </p>
            <p className="truncate text-xs text-[var(--color-sidebar-muted-foreground)]">
              {auth.user.profile.email}
            </p>
          </div>
        )}
        <Link
          to="/app/account"
          title={collapsed ? m.sidebar_account() : undefined}
          className={cn(
            'flex items-center rounded-md py-2 mx-3 text-sm transition-colors',
            'text-[var(--color-sidebar-foreground)]/70 hover:bg-[var(--color-sidebar-accent)] hover:text-[var(--color-sidebar-foreground)]',
            collapsed ? 'justify-center' : 'gap-3 px-3'
          )}
          activeProps={{
            className: 'bg-[var(--color-sidebar-accent)] text-[var(--color-sidebar-accent-foreground)]',
          }}
        >
          <UserCircle size={18} strokeWidth={1.5} />
          {!collapsed && m.sidebar_account()}
        </Link>
        <button
          onClick={() => {
            // sendBeacon is guaranteed to fire even when the page navigates away,
            // unlike fetch which gets cancelled by signoutRedirect().
            navigator.sendBeacon('/api/auth/logout')
            void auth.signoutRedirect()
          }}
          title={collapsed ? m.sidebar_logout() : undefined}
          className={cn(
            'flex w-full items-center rounded-md py-2 mx-3 text-sm transition-colors',
            'text-[var(--color-sidebar-foreground)]/70 hover:bg-[var(--color-sidebar-accent)] hover:text-[var(--color-sidebar-foreground)]',
            collapsed ? 'justify-center' : 'gap-3 px-3'
          )}
        >
          <LogOut size={18} strokeWidth={1.5} />
          {!collapsed && m.sidebar_logout()}
        </button>
      </div>
    </aside>
  )
}
