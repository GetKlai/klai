/**
 * SPEC-PORTAL-KENNIS-001 — KB detail shell (refactored).
 *
 * Senior UI pass:
 *   - Breadcrumb (Kennis › Naam) instead of a generic "Terug" link
 *   - Title block carries meta directly (N bronnen)
 *   - Primary action "+ Bron toevoegen" sits in the header always visible
 *   - Tab bar simplified: label only, underline indicator
 *   - Geavanceerd hidden when the user lacks the kb_manager role
 */
import { createFileRoute, Link, Outlet, redirect, useLocation } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery } from '@tanstack/react-query'
import { ChevronRight, Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { queryLogger } from '@/lib/logger'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { meetsMinRole } from '@/lib/profiles'
import type { KBTab, KnowledgeBase, KBStats, MembersResponse } from './-kb-types'

const VALID_TABS = new Set<KBTab>([
  'overview',
  'connectors',
  'members',
  'items',
  'taxonomy',
  'settings',
  'advanced',
])

const TAB_PATH_MAP: Record<string, string> = {
  overview: '/app/knowledge/$kbSlug/bronnen',
  items: '/app/knowledge/$kbSlug/bronnen',
  connectors: '/app/knowledge/$kbSlug/bronnen',
  members: '/app/knowledge/$kbSlug/settings',
  taxonomy: '/app/knowledge/$kbSlug/advanced',
  settings: '/app/knowledge/$kbSlug/settings',
  advanced: '/app/knowledge/$kbSlug/advanced',
}

type KBSearch = {
  tab?: KBTab
  edit?: string
}

export const Route = createFileRoute('/app/knowledge/$kbSlug')({
  validateSearch: (search: Record<string, unknown>): KBSearch => ({
    tab: (VALID_TABS as Set<string>).has(search.tab as string) ? (search.tab as KBTab) : undefined,
    edit: typeof search.edit === 'string' ? search.edit : undefined,
  }),
  beforeLoad: ({ search, params }) => {
    if (search.tab) {
      const target = TAB_PATH_MAP[search.tab] ?? '/app/knowledge/$kbSlug/bronnen'
      throw redirect({
        to: target,
        params: { kbSlug: params.kbSlug },
        search: { edit: search.edit },
      })
    }
  },
  component: () => (
    <ProductGuard product="knowledge">
      <KbLayout />
    </ProductGuard>
  ),
})

// -- Tab definitions --------------------------------------------------------

type TabId = 'bronnen' | 'instellingen' | 'geavanceerd'

const TAB_DEFS: { id: TabId; to: string; label: string; matches: string[] }[] = [
  {
    id: 'bronnen',
    to: '/app/knowledge/$kbSlug/bronnen',
    label: 'Bronnen',
    matches: ['/bronnen', '/overview', '/items', '/connectors'],
  },
  {
    id: 'instellingen',
    to: '/app/knowledge/$kbSlug/settings',
    label: 'Instellingen',
    matches: ['/settings', '/members', '/instellingen'],
  },
  {
    id: 'geavanceerd',
    to: '/app/knowledge/$kbSlug/advanced',
    label: 'Geavanceerd',
    matches: ['/advanced', '/taxonomy', '/geavanceerd'],
  },
]

function activeTabId(pathname: string): TabId {
  for (const def of TAB_DEFS) {
    if (def.matches.some((suffix) => pathname.endsWith(suffix))) {
      return def.id
    }
  }
  return 'bronnen'
}

interface KBStatsSummaryResponse {
  stats: Record<string, { bronnen?: number; chunks?: number }>
}

// -- Layout -----------------------------------------------------------------

function KbLayout() {
  const { kbSlug } = Route.useParams()
  const auth = useAuth()
  const location = useLocation()
  const { user: currentUser } = useCurrentUser()

  const { data: kb, isLoading, isError } = useQuery<KnowledgeBase>({
    queryKey: ['app-knowledge-base', kbSlug],
    queryFn: async () => {
      try {
        return await apiFetch<KnowledgeBase>(`/api/app/knowledge-bases/${kbSlug}`)
      } catch (err) {
        queryLogger.warn('KB fetch failed', { slug: kbSlug, error: err })
        throw err
      }
    },
    enabled: auth.isAuthenticated,
    retry: false,
  })

  // Prefetch stats so child tabs render without an extra spinner.
  useQuery<KBStats>({
    queryKey: ['kb-stats', kbSlug],
    queryFn: async () => apiFetch<KBStats>(`/api/app/knowledge-bases/${kbSlug}/stats`),
    enabled: auth.isAuthenticated && !!kb,
  })

  // Bron count for the header meta line. Reuses the cached stats-summary
  // query that the KB list page already populated; falls back gracefully
  // when navigated to directly.
  const { data: statsData } = useQuery<KBStatsSummaryResponse>({
    queryKey: ['app-knowledge-bases-stats-summary'],
    queryFn: () => apiFetch<KBStatsSummaryResponse>('/api/app/knowledge-bases/stats-summary'),
    enabled: auth.isAuthenticated,
  })
  const kbStats = kb ? statsData?.stats[kb.slug] : undefined
  const bronnenCount = kbStats?.bronnen ?? 0

  const { data: members } = useQuery<MembersResponse>({
    queryKey: ['kb-members', kbSlug],
    queryFn: async () => apiFetch<MembersResponse>(`/api/app/knowledge-bases/${kbSlug}/members`),
    enabled: auth.isAuthenticated && !!kb,
  })

  const myUserId = auth.user?.profile?.sub
  const isCreator = !!(myUserId && kb?.created_by === myUserId)
  const isOwnerRole = !!(myUserId && members?.users.some((u) => u.user_id === myUserId && u.role === 'owner'))
  const isAdmin = currentUser?.isAdmin === true
  const _isOwner = isCreator || isOwnerRole || isAdmin // reserved for per-tab gating
  void _isOwner
  // Geavanceerd contains taxonomy + advanced settings — both gated behind
  // org-level kb_manager. Hide the tab when the user can't use it (e.g.
  // for own personal KBs).
  const canSeeGeavanceerd = isAdmin || meetsMinRole(currentUser?.effective_role, 'kb_manager')

  if (isLoading) {
    return (
      <div className="mx-auto max-w-3xl px-6 pt-6 pb-10">
        <div className="h-4 w-32 rounded bg-gray-50 animate-pulse mb-2" />
        <div className="pt-4">
          <div className="h-7 w-64 rounded bg-gray-50 animate-pulse" />
          <div className="h-4 w-48 rounded bg-gray-50 animate-pulse mt-3" />
        </div>
      </div>
    )
  }

  if (isError || !kb) {
    return (
      <div className="mx-auto max-w-3xl px-6 pt-14 pb-10 text-gray-400">
        {m.knowledge_detail_not_found()}
      </div>
    )
  }

  const visibleTabs = TAB_DEFS.filter((tab) => {
    if (tab.id === 'geavanceerd') return canSeeGeavanceerd
    return true
  })
  const activeId = activeTabId(location.pathname)

  const bronnenLabel = bronnenCount === 0
    ? 'Nog geen bronnen'
    : bronnenCount === 1
      ? '1 bron'
      : `${bronnenCount} bronnen`

  return (
    <div className="mx-auto max-w-3xl px-6 pt-6 pb-10">
      {/* Breadcrumb — primary navigation context */}
      <nav className="flex items-center gap-1 text-sm text-gray-400 mb-2">
        <Link to="/app/knowledge" className="hover:text-gray-900 transition-colors">
          Kennis
        </Link>
        <ChevronRight className="h-3.5 w-3.5" />
        <span className="text-gray-600 truncate">{kb.name}</span>
      </nav>

      {/* Title row + primary action */}
      <div className="pt-4 flex items-start justify-between gap-4 mb-1">
        <div className="min-w-0 flex-1">
          <h1 className="text-[26px] font-display-bold text-gray-900 leading-none truncate">
            {kb.name}
          </h1>
        </div>
        <Link to="/app/knowledge/$kbSlug/add-source" params={{ kbSlug }} className="shrink-0">
          <Button variant="default">
            <Plus className="h-4 w-4" />
            Bron toevoegen
          </Button>
        </Link>
      </div>

      {/* Meta + description */}
      <div className="text-sm text-gray-400 mb-6 space-y-1">
        <p>
          {bronnenLabel}
          {kb.description && <> · {kb.description}</>}
        </p>
      </div>

      {/* Tab bar — labels only, underline indicator */}
      <div className="border-b border-gray-200 mb-6">
        <nav className="-mb-px flex gap-6">
          {visibleTabs.map(({ id, to, label }) => {
            const isActive = activeId === id
            return (
              <Link
                key={id}
                to={to}
                params={{ kbSlug }}
                className={`pb-3 text-sm font-medium border-b-2 transition-colors ${
                  isActive
                    ? 'border-gray-900 text-gray-900'
                    : 'border-transparent text-gray-400 hover:text-gray-700'
                }`}
              >
                {label}
              </Link>
            )
          })}
        </nav>
      </div>

      {/* Active tab content */}
      <Outlet />
    </div>
  )
}
