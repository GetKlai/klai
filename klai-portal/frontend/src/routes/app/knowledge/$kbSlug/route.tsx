/**
 * SPEC-PORTAL-KENNIS-001 Phase D - KB detail shell with 3 tabs.
 *
 * Tabs: Bronnen (default) / Instellingen / Inzichten.
 *
 * Active-tab detection works on URL groups so legacy paths still
 * highlight the right tab while their old content renders:
 *   Bronnen      ← /bronnen, /overview, /items, /connectors
 *   Instellingen ← /settings, /members
 *   Inzichten    ← /insights, /advanced, /taxonomy
 *
 * SPEC-PORTAL-KENNIS-002 Track 1: renamed Geavanceerd → Inzichten.
 * /advanced now redirects to /insights. Gate: isAdmin || kb_manager.
 */
import { createFileRoute, Link, Outlet, redirect } from '@tanstack/react-router'
import { useLocation } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, BookOpen, Settings, SlidersHorizontal } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { queryLogger } from '@/lib/logger'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { Button } from '@/components/ui/button'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { meetsMinRole } from '@/lib/profiles'
import type { KBTab, KnowledgeBase, KBStats, MembersResponse } from './-kb-types'
import { kbQueryKeys } from '@/lib/kb-query-keys'

const VALID_TABS = new Set<KBTab>([
  'overview',
  'connectors',
  'members',
  'items',
  'taxonomy',
  'settings',
  'advanced',
  'insights',
])

const TAB_PATH_MAP: Record<string, string> = {
  overview: '/app/knowledge/$kbSlug/sources',
  items: '/app/knowledge/$kbSlug/sources',
  connectors: '/app/knowledge/$kbSlug/sources',
  members: '/app/knowledge/$kbSlug/settings',
  taxonomy: '/app/knowledge/$kbSlug/insights',
  settings: '/app/knowledge/$kbSlug/settings',
  advanced: '/app/knowledge/$kbSlug/insights',
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
      const target = TAB_PATH_MAP[search.tab] ?? '/app/knowledge/$kbSlug/sources'
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

type TabId = 'bronnen' | 'instellingen' | 'inzichten'

interface TabDef {
  id: TabId
  to: string
  icon: React.ElementType
  /** Lazy label so Paraglide resolves the active locale per render. */
  label: () => string
  matches: string[]
}

const TAB_DEFS: TabDef[] = [
  {
    id: 'bronnen',
    to: '/app/knowledge/$kbSlug/sources',
    icon: BookOpen,
    label: () => m.kb_tab_sources(),
    matches: ['/bronnen', '/overview', '/items', '/connectors', '/sources'],
  },
  {
    id: 'instellingen',
    to: '/app/knowledge/$kbSlug/settings',
    icon: Settings,
    label: () => m.kb_tab_settings(),
    matches: ['/settings', '/members', '/instellingen'],
  },
  {
    id: 'inzichten',
    to: '/app/knowledge/$kbSlug/insights',
    icon: SlidersHorizontal,
    label: () => m.kb_tab_insights(),
    matches: ['/insights', '/advanced', '/taxonomy', '/inzichten'],
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

// -- Layout -----------------------------------------------------------------

function KbLayout() {
  const { kbSlug } = Route.useParams()
  const auth = useAuth()
  const location = useLocation()
  const { user: currentUser } = useCurrentUser()

  const { data: kb, isLoading, isError } = useQuery<KnowledgeBase>({
    queryKey: kbQueryKeys.knowledgeBase(kbSlug),
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

  // Members prefetch so child tabs render without an extra spinner.
  useQuery<MembersResponse>({
    queryKey: ['kb-members', kbSlug],
    queryFn: async () => apiFetch<MembersResponse>(`/api/app/knowledge-bases/${kbSlug}/members`),
    enabled: auth.isAuthenticated && !!kb,
  })

  const isAdmin = currentUser?.isAdmin === true
  // Gate: isAdmin OR has at least kb_manager role → may see Inzichten tab.
  const canSeeInzichten = isAdmin || meetsMinRole(currentUser?.effective_role, 'kb_manager')

  if (isLoading) {
    return (
      <div className="mx-auto max-w-3xl px-6 pb-10">
        <div className="flex h-[66px] items-center">
          <div className="h-6 w-48 rounded bg-gray-50 animate-pulse" />
        </div>
      </div>
    )
  }

  if (isError || !kb) {
    return (
      <div className="mx-auto max-w-3xl px-6 pb-10 pt-10 text-gray-400">
        {m.knowledge_detail_not_found()}
      </div>
    )
  }

  const visibleTabs = TAB_DEFS.filter((tab) => tab.id !== 'inzichten' || canSeeInzichten)
  const activeId = activeTabId(location.pathname)

  return (
    <div className="mx-auto max-w-3xl px-6 pb-10">
      {/* Title strip - h-[66px] aligns the KB name with the sidebar logo */}
      <div className="flex h-[66px] items-center justify-between gap-4">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900 leading-none truncate">
          {kb.name}
        </h1>
        <Button asChild variant="ghost" size="sm">
          <Link to="/app/knowledge">
            <ArrowLeft className="h-4 w-4 mr-2" />
            {m.kb_detail_back()}
          </Link>
        </Button>
      </div>

      {kb.description && (
        <p className="text-sm text-gray-400 mb-6">{kb.description}</p>
      )}

      {/* Tab bar */}
      <div className="border-b border-gray-200 mb-6">
        <nav className="-mb-px flex gap-6">
          {visibleTabs.map(({ id, to, icon: Icon, label }) => {
            const isActive = activeId === id
            return (
              <Link
                key={id}
                to={to}
                params={{ kbSlug }}
                className={`flex items-center gap-1.5 pb-3 text-sm font-medium border-b-2 transition-colors ${
                  isActive
                    ? 'border-gray-900 text-gray-900'
                    : 'border-transparent text-gray-400 hover:text-gray-900'
                }`}
              >
                <Icon className="h-4 w-4" />
                {label()}
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
