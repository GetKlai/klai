import { Link, useLocation } from '@tanstack/react-router'
import { useState } from 'react'
import { useAuth } from 'react-oidc-context'
import { useQuery } from '@tanstack/react-query'
import { LayoutGrid, LogOut, PanelLeftClose, PanelLeftOpen, Shield, UserCircle, type LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useLocale } from '@/lib/locale'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { apiFetch } from '@/lib/apiFetch'
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

// ---------------------------------------------------------------------------
// Knowledge types
// ---------------------------------------------------------------------------

interface KBItem {
  id: number
  name: string
  slug: string
  owner_type: string
  owner_user_id: string | null
}

interface KBStats {
  items: number
  connectors: number
}

export function Sidebar({ navItems }: SidebarProps) {
  const auth = useAuth()
  const location = useLocation()
  const { locale, switchLocale } = useLocale()
  const { user } = useCurrentUser()
  const token = auth.user?.access_token

  const inAdmin = location.pathname.startsWith('/admin')
  const isAdmin = inAdmin || user?.isAdmin === true
  const hasKnowledge = user?.isAdmin || user?.products.includes('knowledge')

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
      {/* Logo + toggle */}
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
            'text-[var(--color-sidebar-foreground)]/60 hover:bg-black/5 hover:text-[var(--color-sidebar-foreground)]',
          )}
        >
          {collapsed
            ? <PanelLeftOpen size={18} strokeWidth={1.5} />
            : <PanelLeftClose size={18} strokeWidth={1.5} />
          }
        </button>
      </div>

      {/* Navigation */}
      <nav className="py-4">
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
                    'text-[var(--color-sidebar-foreground)]/60 hover:bg-black/5 hover:text-[var(--color-sidebar-foreground)]',
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
                    'text-[var(--color-sidebar-foreground)]/60 hover:bg-black/5 hover:text-[var(--color-sidebar-foreground)]',
                    collapsed ? 'justify-center' : 'gap-3 px-3'
                  )}
                  activeProps={{
                    className: 'bg-black/5 text-[var(--color-sidebar-foreground)] font-medium',
                  }}
                >
                  <item.icon size={18} strokeWidth={1.5} />
                  {!collapsed && item.label}
                </Link>
              )}
            </li>
          ))}
        </ul>
      </nav>

      {/* Knowledge collections — always visible */}
      {!collapsed && hasKnowledge && !inAdmin && (
        <KnowledgeCollections token={token} myUserId={auth.user?.profile?.sub} />
      )}

      {/* Spacer */}
      <div className="flex-1" />

      {/* Admin/App switcher */}
      {isAdmin && (
        <div className="border-t border-[var(--color-sidebar-border)] py-3">
          <Link
            to={inAdmin ? '/app' : '/admin'}
            title={collapsed ? (inAdmin ? m.sidebar_go_to_app() : m.sidebar_go_to_admin()) : undefined}
            className={cn(
              'flex items-center rounded-md py-2 mx-3 text-sm transition-colors',
              'text-[var(--color-sidebar-foreground)]/60 hover:bg-black/5 hover:text-[var(--color-sidebar-foreground)]',
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
            'text-[var(--color-sidebar-foreground)]/60 hover:bg-black/5 hover:text-[var(--color-sidebar-foreground)]',
            collapsed ? 'justify-center' : 'gap-3 px-3'
          )}
          activeProps={{
            className: 'bg-black/5 text-[var(--color-sidebar-foreground)] font-medium',
          }}
        >
          <UserCircle size={18} strokeWidth={1.5} />
          {!collapsed && m.sidebar_account()}
        </Link>
        <button
          onClick={() => {
            navigator.sendBeacon('/api/auth/logout')
            void auth.signoutRedirect()
          }}
          title={collapsed ? m.sidebar_logout() : undefined}
          className={cn(
            'flex w-full items-center rounded-md py-2 mx-3 text-sm transition-colors',
            'text-[var(--color-sidebar-foreground)]/60 hover:bg-black/5 hover:text-[var(--color-sidebar-foreground)]',
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

// ---------------------------------------------------------------------------
// Knowledge collections in sidebar
// ---------------------------------------------------------------------------

function KnowledgeCollections({ token, myUserId }: { token: string | undefined; myUserId: string | undefined }) {
  const { data: kbsData } = useQuery<{ knowledge_bases: KBItem[] }>({
    queryKey: ['app-knowledge-bases'],
    queryFn: async () => apiFetch<{ knowledge_bases: KBItem[] }>('/api/app/knowledge-bases', token),
    enabled: !!token,
  })

  const { data: statsData } = useQuery<{ stats: Record<string, KBStats> }>({
    queryKey: ['app-knowledge-bases-stats-summary'],
    queryFn: async () => apiFetch<{ stats: Record<string, KBStats> }>('/api/app/knowledge-bases/stats-summary', token),
    enabled: !!token,
  })

  const allKbs = kbsData?.knowledge_bases ?? []
  const stats = statsData?.stats ?? {}

  const personalKb = allKbs.find(
    (kb) => kb.slug === `personal-${myUserId}` && kb.owner_type === 'user',
  )
  const otherKbs = allKbs.filter((kb) => kb.slug !== personalKb?.slug)

  if (allKbs.length === 0) return null

  return (
    <div className="border-t border-[var(--color-sidebar-border)] pt-3 px-3">
      <p className="px-3 mb-2 text-[10px] font-medium text-[var(--color-sidebar-muted-foreground)] uppercase tracking-wider">
        Collecties
      </p>
      <ul className="space-y-0.5">
        {personalKb && (
          <li>
            <Link
              to="/app/knowledge/$kbSlug/overview"
              params={{ kbSlug: personalKb.slug }}
              className="flex w-full items-center gap-2.5 rounded-md px-3 py-1.5 text-xs text-[var(--color-sidebar-foreground)]/60 hover:bg-black/5 hover:text-[var(--color-sidebar-foreground)] transition-colors"
            >
              <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--color-success)]" />
              <span className="truncate">{m.chat_kb_bar_personal_label()}</span>
              {(stats[personalKb.slug]?.items ?? 0) > 0 && (
                <span className="ml-auto text-[10px] text-[var(--color-sidebar-muted-foreground)] tabular-nums">
                  {stats[personalKb.slug]?.items}
                </span>
              )}
            </Link>
          </li>
        )}
        {otherKbs.map((kb) => (
          <li key={kb.slug}>
            <Link
              to="/app/knowledge/$kbSlug/overview"
              params={{ kbSlug: kb.slug }}
              className="flex w-full items-center gap-2.5 rounded-md px-3 py-1.5 text-xs text-[var(--color-sidebar-foreground)]/60 hover:bg-black/5 hover:text-[var(--color-sidebar-foreground)] transition-colors"
            >
              <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--color-success)]" />
              <span className="truncate">{kb.name}</span>
              {(stats[kb.slug]?.items ?? 0) > 0 && (
                <span className="ml-auto text-[10px] text-[var(--color-sidebar-muted-foreground)] tabular-nums">
                  {stats[kb.slug]?.items}
                </span>
              )}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  )
}

