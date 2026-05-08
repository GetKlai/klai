/**
 * SPEC-PORTAL-KENNIS-001 Phase D — KB detail shell with 3 tabs.
 *
 * Tabs: Bronnen (default) / Instellingen / Geavanceerd.
 *
 * Active-tab detection works on URL groups so legacy paths still
 * highlight the right tab while their old content renders:
 *   Bronnen      ← /bronnen, /overview, /items, /connectors
 *   Instellingen ← /settings, /members
 *   Geavanceerd  ← /advanced, /taxonomy
 *
 * Phase F + F-bis will replace /settings + /advanced with merged
 * /instellingen + /geavanceerd pages. For v1 the tabs link to the
 * existing pages so the shell ships on its own.
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
import { useCurrentUser } from '@/hooks/useCurrentUser'
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
  taxonomy: '/app/knowledge/$kbSlug/taxonomy',
  settings: '/app/knowledge/$kbSlug/settings',
  advanced: '/app/knowledge/$kbSlug/taxonomy',
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

const TAB_DEFS: { id: TabId; to: string; icon: React.ElementType; label: string; matches: string[] }[] = [
  {
    id: 'bronnen',
    to: '/app/knowledge/$kbSlug/bronnen',
    icon: BookOpen,
    label: 'Bronnen',
    matches: ['/bronnen', '/overview', '/items', '/connectors'],
  },
  {
    id: 'instellingen',
    to: '/app/knowledge/$kbSlug/settings',
    icon: Settings,
    label: 'Instellingen',
    matches: ['/settings', '/members', '/instellingen'],
  },
  {
    id: 'geavanceerd',
    to: '/app/knowledge/$kbSlug/taxonomy',
    icon: SlidersHorizontal,
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

  // Members are needed for owner detection (controls Geavanceerd tab visibility).
  const { data: members } = useQuery<MembersResponse>({
    queryKey: ['kb-members', kbSlug],
    queryFn: async () => apiFetch<MembersResponse>(`/api/app/knowledge-bases/${kbSlug}/members`),
    enabled: auth.isAuthenticated && !!kb,
  })

  const myUserId = auth.user?.profile?.sub
  const isCreator = !!(myUserId && kb?.created_by === myUserId)
  const isOwnerRole = !!(myUserId && members?.users.some((u) => u.user_id === myUserId && u.role === 'owner'))
  const isAdmin = currentUser?.isAdmin === true
  const isOwner = isCreator || isOwnerRole || isAdmin

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

  const visibleTabs = TAB_DEFS.filter((tab) => tab.id !== 'geavanceerd' || isOwner)
  const activeId = activeTabId(location.pathname)

  return (
    <div className="mx-auto max-w-3xl px-6 pb-10">
      {/* Title strip — h-[66px] aligns the KB name with the sidebar logo */}
      <div className="flex h-[66px] items-center justify-between gap-4">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900 leading-none truncate">
          {kb.name}
        </h1>
        <Link
          to="/app/knowledge"
          className="flex items-center gap-1.5 text-sm text-gray-400 hover:text-gray-900 transition-colors shrink-0"
        >
          <ArrowLeft className="h-4 w-4" />
          Terug
        </Link>
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
