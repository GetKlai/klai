import { Link, useLocation } from '@tanstack/react-router'
import { useState } from 'react'
import { useAuth } from 'react-oidc-context'
import { ChevronDown, LogOut, PanelLeftClose, PanelLeftOpen, UserCircle, type LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useLocale } from '@/lib/locale'
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
  accountItems?: NavItem[]
}

/* Shared classes for every clickable sidebar item */
const ITEM_BASE = 'flex items-center rounded-md py-2 mx-3 text-[14px] font-semibold transition-colors text-[var(--color-sidebar-foreground)] hover:bg-black/5'
const ITEM_ACTIVE = 'bg-black/[0.06]'
const ICON_PROPS = { size: 18, strokeWidth: 2 } as const

export function Sidebar({ navItems, accountItems }: SidebarProps) {
  const auth = useAuth()
  const location = useLocation()
  const { locale, switchLocale } = useLocale()

  const [collapsed, setCollapsed] = useState(() => {
    return localStorage.getItem(STORAGE_KEYS.sidebarCollapsed) === 'true'
  })

  /* Track which collapsible sections are open; auto-expand based on current path */
  const [openSections, setOpenSections] = useState<Set<string>>(() => {
    const open = new Set<string>()
    for (const item of navItems) {
      if (item.children?.some((child) => child.to && location.pathname.startsWith(child.to))) {
        open.add(item.label)
      }
    }
    if (accountItems?.some((child) => child.to && location.pathname.startsWith(child.to))) {
      open.add('__account__')
    }
    return open
  })

  const toggleSection = (key: string) => {
    setOpenSections((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const toggle = () => {
    const next = !collapsed
    setCollapsed(next)
    localStorage.setItem(STORAGE_KEYS.sidebarCollapsed, String(next))
  }

  const gap = collapsed ? 'justify-center' : 'gap-3 px-3'
  const hasAccountItems = accountItems && accountItems.length > 0
  const accountOpen = openSections.has('__account__')

  return (
    <aside
      role="navigation"
      aria-label="Main navigation"
      className={cn(
        'flex h-screen shrink-0 flex-col bg-[var(--color-sidebar)] border-r border-[var(--color-sidebar-border)]/50 text-[var(--color-sidebar-foreground)] transition-[width] duration-200 font-[system-ui]',
        collapsed ? 'w-14' : 'w-60',
      )}
    >
      {/* Logo + toggle */}
      <div className={cn('flex h-[66px] items-center', collapsed ? 'justify-center' : 'justify-between px-6')}>
        {!collapsed && <img src="/klai-logo.svg" alt="Klai" className="h-[18px] w-auto block" />}
        <button
          onClick={toggle}
          title={collapsed ? m.sidebar_expand() : m.sidebar_collapse()}
          className="flex items-center justify-center rounded-lg p-1.5 text-[var(--color-sidebar-foreground)] hover:bg-black/5 transition-colors"
        >
          {collapsed ? <PanelLeftOpen {...ICON_PROPS} /> : <PanelLeftClose {...ICON_PROPS} />}
        </button>
      </div>

      {/* Navigation */}
      <nav className="py-4">
        <ul className="space-y-1">
          {navItems.map((item) => (
            <li key={item.label}>
              {item.children ? (
                /* Collapsible section */
                <>
                  <button
                    onClick={() => toggleSection(item.label)}
                    title={collapsed ? item.label : undefined}
                    className={cn(ITEM_BASE, 'w-full', gap, !collapsed && 'justify-between')}
                  >
                    <span className={cn('flex items-center', collapsed ? '' : 'gap-3')}>
                      <item.icon {...ICON_PROPS} />
                      {!collapsed && item.label}
                    </span>
                    {!collapsed && (
                      <ChevronDown
                        size={14}
                        className={cn('transition-transform', openSections.has(item.label) && 'rotate-180')}
                      />
                    )}
                  </button>
                  {!collapsed && openSections.has(item.label) && (
                    <ul className="mt-1 space-y-0.5">
                      {item.children.map((child) => (
                        <li key={child.to}>
                          <Link
                            to={child.to ?? '/'}
                            activeOptions={child.end ? { exact: true } : undefined}
                            title={collapsed ? child.label : undefined}
                            className={cn(ITEM_BASE, 'gap-3 pl-10 pr-3')}
                            activeProps={{ className: ITEM_ACTIVE }}
                          >
                            <child.icon {...ICON_PROPS} />
                            {child.label}
                          </Link>
                        </li>
                      ))}
                    </ul>
                  )}
                </>
              ) : item.href ? (
                <a
                  href={item.href}
                  rel="noopener noreferrer"
                  title={collapsed ? item.label : undefined}
                  className={cn(ITEM_BASE, gap)}
                >
                  <item.icon {...ICON_PROPS} />
                  {!collapsed && item.label}
                </a>
              ) : (
                <Link
                  to={item.to ?? '/'}
                  activeOptions={item.end ? { exact: true } : undefined}
                  title={collapsed ? item.label : undefined}
                  className={cn(ITEM_BASE, gap)}
                  activeProps={{ className: ITEM_ACTIVE }}
                >
                  <item.icon {...ICON_PROPS} />
                  {!collapsed && item.label}
                </Link>
              )}
            </li>
          ))}
        </ul>
      </nav>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Locale switcher */}
      <div className={cn(
        'border-t border-[var(--color-sidebar-border)] py-2',
        collapsed ? 'flex justify-center' : 'flex items-center gap-1 px-6',
      )}>
        {collapsed ? (
          <button
            onClick={() => switchLocale(locale === 'nl' ? 'en' : 'nl')}
            title={locale === 'nl' ? 'Switch to English' : 'Wisselen naar Nederlands'}
            className="text-xs font-semibold text-[var(--color-sidebar-foreground)]/70 hover:text-[var(--color-sidebar-foreground)] transition-colors"
          >
            {locale.toUpperCase()}
          </button>
        ) : (
          <>
            <button
              onClick={() => switchLocale('nl')}
              className={cn(
                'text-xs font-semibold transition-colors',
                locale === 'nl'
                  ? 'text-[var(--color-sidebar-foreground)]'
                  : 'text-[var(--color-sidebar-foreground)]/30 hover:text-[var(--color-sidebar-foreground)]/60',
              )}
            >
              NL
            </button>
            <span className="text-xs text-[var(--color-sidebar-foreground)]/20">/</span>
            <button
              onClick={() => switchLocale('en')}
              className={cn(
                'text-xs font-semibold transition-colors',
                locale === 'en'
                  ? 'text-[var(--color-sidebar-foreground)]'
                  : 'text-[var(--color-sidebar-foreground)]/30 hover:text-[var(--color-sidebar-foreground)]/60',
              )}
            >
              EN
            </button>
          </>
        )}
      </div>

      {/* User + account + logout */}
      <div className="border-t border-[var(--color-sidebar-border)] pt-2 pb-4">
        {auth.user && !collapsed && (
          <div className="mb-2 px-6 py-2">
            <p className="truncate text-xs font-semibold text-[var(--color-sidebar-foreground)]">
              {auth.user.profile.name ?? auth.user.profile.preferred_username}
            </p>
            <p className="truncate text-xs text-[var(--color-sidebar-foreground)]/50">
              {auth.user.profile.email}
            </p>
          </div>
        )}

        {/* Account link — expandable when accountItems provided */}
        {hasAccountItems && !collapsed ? (
          <>
            <button
              onClick={() => toggleSection('__account__')}
              className={cn(ITEM_BASE, 'w-full', gap, 'justify-between')}
            >
              <span className="flex items-center gap-3">
                <UserCircle {...ICON_PROPS} />
                {m.sidebar_account()}
              </span>
              <ChevronDown
                size={14}
                className={cn('transition-transform', accountOpen && 'rotate-180')}
              />
            </button>
            {accountOpen && (
              <ul className="mt-1 space-y-0.5">
                {accountItems.map((child) => (
                  <li key={child.to}>
                    <Link
                      to={child.to ?? '/'}
                      activeOptions={child.end ? { exact: true } : undefined}
                      className={cn(ITEM_BASE, 'gap-3 pl-10 pr-3')}
                      activeProps={{ className: ITEM_ACTIVE }}
                    >
                      <child.icon {...ICON_PROPS} />
                      {child.label}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <Link
            to="/app/account"
            title={collapsed ? m.sidebar_account() : undefined}
            className={cn(ITEM_BASE, gap)}
            activeProps={{ className: ITEM_ACTIVE }}
          >
            <UserCircle {...ICON_PROPS} />
            {!collapsed && m.sidebar_account()}
          </Link>
        )}

        <button
          onClick={() => {
            navigator.sendBeacon('/api/auth/logout')
            void auth.signoutRedirect()
          }}
          title={collapsed ? m.sidebar_logout() : undefined}
          className={cn(ITEM_BASE, 'w-full', gap)}
        >
          <LogOut {...ICON_PROPS} />
          {!collapsed && m.sidebar_logout()}
        </button>
      </div>
    </aside>
  )
}
