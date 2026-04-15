import { createFileRoute, Link, Outlet, redirect } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery } from '@tanstack/react-query'
import {
  Shield, BarChart2, Zap, List, Settings, ArrowLeft
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { queryLogger } from '@/lib/logger'
import { ProductGuard } from '@/components/layout/ProductGuard'
import type { KBTab, KnowledgeBase, KBStats, MembersResponse } from './-kb-types'

const VALID_TABS = new Set<KBTab>(['overview', 'connectors', 'members', 'items', 'taxonomy', 'settings', 'advanced'])

const TAB_PATH_MAP: Record<string, string> = {
  overview: '/app/knowledge/$kbSlug/overview',
  items: '/app/knowledge/$kbSlug/items',
  connectors: '/app/knowledge/$kbSlug/connectors',
  members: '/app/knowledge/$kbSlug/members',
  taxonomy: '/app/knowledge/$kbSlug/taxonomy',
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
      const target = TAB_PATH_MAP[search.tab] ?? TAB_PATH_MAP.overview
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

function KbLayout() {
  const { kbSlug } = Route.useParams()
  const auth = useAuth()
  const token = auth.user?.access_token
  const { data: kb, isLoading, isError } = useQuery<KnowledgeBase>({
    queryKey: ['app-knowledge-base', kbSlug],
    queryFn: async () => {
      try {
        return await apiFetch<KnowledgeBase>(`/api/app/knowledge-bases/${kbSlug}`, token)
      } catch (err) {
        queryLogger.warn('KB fetch failed', { slug: kbSlug, error: err })
        throw err
      }
    },
    enabled: !!token,
    retry: false,
  })

  // Prefetch KB stats into the TanStack Query cache so child routes
  // (overview, settings, advanced) render immediately without an extra fetch.
  useQuery<KBStats>({
    queryKey: ['kb-stats', kbSlug],
    queryFn: async () => apiFetch<KBStats>(`/api/app/knowledge-bases/${kbSlug}/stats`, token),
    enabled: !!token && !!kb,
  })

  const { data: members } = useQuery<MembersResponse>({
    queryKey: ['kb-members', kbSlug],
    queryFn: async () => apiFetch<MembersResponse>(`/api/app/knowledge-bases/${kbSlug}/members`, token),
    enabled: !!token && !!kb,
  })

  const myUserId = auth.user?.profile?.sub
  const isCreator = !!(myUserId && kb?.created_by === myUserId)
  const isOwner = isCreator || !!(myUserId && members?.users.some((u) => u.user_id === myUserId && u.role === 'owner'))
  const isPersonal = kb?.owner_type === 'user'

  if (isLoading) {
    return (
      <div
        className="mx-auto max-w-3xl px-6 py-10"
        style={{ fontFamily: 'Inter, system-ui, sans-serif' }}
      >
        <div className="h-8 w-48 rounded-lg bg-gray-100 animate-pulse mb-4" />
        <div className="h-4 w-96 rounded-lg bg-gray-100 animate-pulse" />
      </div>
    )
  }

  if (isError || !kb) {
    return (
      <div
        className="mx-auto max-w-3xl px-6 py-10 text-gray-400"
        style={{ fontFamily: 'Inter, system-ui, sans-serif' }}
      >
        {m.knowledge_detail_not_found()}
      </div>
    )
  }

  // Tabs: Overzicht (bronnen + bestanden), Toegang, Instellingen (owner only)
  const tabEntries: { id: KBTab; to: string; icon: React.ElementType; label: string; badge?: number }[] = [
    { id: 'overview', to: '/app/knowledge/$kbSlug/overview', icon: BarChart2, label: 'Overzicht' },
    { id: 'members', to: '/app/knowledge/$kbSlug/members', icon: Shield, label: 'Toegang' },
    ...(isOwner ? [{ id: 'settings' as KBTab, to: '/app/knowledge/$kbSlug/settings', icon: Settings, label: 'Instellingen' }] : []),
  ]

  return (
    <div
      className="mx-auto max-w-3xl px-6 py-10 space-y-8"
      style={{ fontFamily: 'Inter, system-ui, sans-serif' }}
    >
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">{kb.name}</h1>
          {kb.description && (
            <p className="text-sm text-gray-400 mt-1">{kb.description}</p>
          )}
        </div>
        <Link
          to="/app/knowledge"
          className="flex items-center gap-1.5 text-sm text-gray-400 hover:text-gray-600 transition-colors"
        >
          <ArrowLeft className="h-4 w-4" />
          Terug
        </Link>
      </div>

      {/* Tab bar */}
      <div className="border-b border-gray-200">
        <nav className="-mb-px flex gap-6">
          {tabEntries.map(({ id, to, icon: TabIcon, label, badge }) => (
            <Link
              key={id}
              to={to}
              params={{ kbSlug }}
              activeProps={{
                className: 'border-gray-900 text-gray-900',
              }}
              inactiveProps={{
                className: 'border-transparent text-gray-400 hover:text-gray-900',
              }}
              className="flex items-center gap-1.5 pb-3 text-sm font-medium border-b-2 transition-colors"
              onClick={(e) => {
                // Prevent navigation if already on this tab
                if (window.location.pathname.endsWith(`/${id}`)) {
                  e.preventDefault()
                }
              }}
            >
              <TabIcon className="h-4 w-4" />
              {label}
              {badge != null && <Badge variant="accent" className="ml-1 text-xs px-1.5 py-0 min-w-[18px]">{String(badge)}</Badge>}
            </Link>
          ))}
        </nav>
      </div>

      {/* Active tab content rendered by child route */}
      <Outlet />
    </div>
  )
}
