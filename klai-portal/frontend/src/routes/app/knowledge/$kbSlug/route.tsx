import { createFileRoute, Link, Outlet, redirect } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery } from '@tanstack/react-query'
import {
  Globe, Lock, Shield, BarChart2, Zap, List, FolderTree, Settings, SlidersHorizontal, ArrowLeft, Plus
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { queryLogger } from '@/lib/logger'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { ProductCapabilityGuard } from '@/components/layout/ProductCapabilityGuard'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import type { KBTab, KnowledgeBase, KBStats, MembersResponse, TaxonomyProposal } from './-kb-types'

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

// Capability requirements for KB tabs (SPEC-PORTAL-UNIFY-KB-001).
const TAB_CAPABILITIES: Partial<Record<KBTab, string>> = {
  connectors: 'kb.connectors',
  members: 'kb.members',
  taxonomy: 'kb.taxonomy',
  advanced: 'kb.advanced',
}

function KbLayout() {
  const { kbSlug } = Route.useParams()
  const auth = useAuth()
  const { user } = useCurrentUser()
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

  // Prefetch KB stats into the TanStack Query cache so child routes
  // (overview, settings, advanced) render immediately without an extra fetch.
  useQuery<KBStats>({
    queryKey: ['kb-stats', kbSlug],
    queryFn: async () => apiFetch<KBStats>(`/api/app/knowledge-bases/${kbSlug}/stats`),
    enabled: auth.isAuthenticated && !!kb,
  })

  const { data: members } = useQuery<MembersResponse>({
    queryKey: ['kb-members', kbSlug],
    queryFn: async () => apiFetch<MembersResponse>(`/api/app/knowledge-bases/${kbSlug}/members`),
    enabled: auth.isAuthenticated && !!kb,
  })

  const myUserId = auth.user?.profile?.sub
  const isCreator = !!(myUserId && kb?.created_by === myUserId)
  const isOwner = isCreator || !!(myUserId && members?.users.some((u) => u.user_id === myUserId && u.role === 'owner'))
  const isPersonal = kb?.owner_type === 'user'

  const pendingProposalsQuery = useQuery<{ proposals: TaxonomyProposal[] }>({
    queryKey: ['taxonomy-proposals-count', kbSlug],
    queryFn: async () => {
      try {
        return await apiFetch<{ proposals: TaxonomyProposal[] }>(`/api/app/knowledge-bases/${kbSlug}/taxonomy/proposals?status=pending`)
      } catch {
        return { proposals: [] }
      }
    },
    enabled: auth.isAuthenticated && !!kb,
  })
  const pendingCount = pendingProposalsQuery.data?.proposals.length ?? 0

  if (isLoading) {
    return (
      <div className="p-6">
        <div className="h-8 w-48 rounded bg-[var(--color-secondary)] animate-pulse mb-4" />
        <div className="h-4 w-96 rounded bg-[var(--color-secondary)] animate-pulse" />
      </div>
    )
  }

  if (isError || !kb) {
    return (
      <div className="p-6 text-[var(--color-muted-foreground)]">
        {m.knowledge_detail_not_found()}
      </div>
    )
  }

  // Determine active tab from URL path
  const tabEntries: { id: KBTab; to: string; icon: React.ElementType; label: string; badge?: number }[] = [
    { id: 'overview', to: '/app/knowledge/$kbSlug/overview', icon: BarChart2, label: m.knowledge_detail_tab_overview() },
    ...(isPersonal ? [{ id: 'items' as KBTab, to: '/app/knowledge/$kbSlug/items', icon: List, label: m.knowledge_detail_tab_items() }] : []),
    { id: 'connectors', to: '/app/knowledge/$kbSlug/connectors', icon: Zap, label: m.knowledge_detail_tab_connectors() },
    { id: 'members', to: '/app/knowledge/$kbSlug/members', icon: Shield, label: m.knowledge_detail_tab_members() },
    { id: 'taxonomy', to: '/app/knowledge/$kbSlug/taxonomy', icon: FolderTree, label: m.knowledge_detail_tab_taxonomy(), badge: pendingCount > 0 ? pendingCount : undefined },
    ...(isOwner ? [{ id: 'settings' as KBTab, to: '/app/knowledge/$kbSlug/settings', icon: Settings, label: m.knowledge_detail_tab_settings() }] : []),
    ...(isOwner ? [{ id: 'advanced' as KBTab, to: '/app/knowledge/$kbSlug/advanced', icon: SlidersHorizontal, label: m.knowledge_detail_tab_advanced() }] : []),
  ]

  return (
    <div className="mx-auto max-w-4xl px-6 py-10 space-y-8">
      {/* Header */}
      <div className="flex items-start gap-3">
        <div className="flex-1">
          <h1 className="page-title text-[26px] font-display-bold text-gray-900">{kb.name}</h1>
          {kb.description && (
            <p className="text-sm text-[var(--color-muted-foreground)] mt-1">{kb.description}</p>
          )}
          <div className="flex items-center gap-1.5 mt-1.5 text-xs text-[var(--color-muted-foreground)]">
            {kb.visibility === 'public' ? <Globe className="h-3.5 w-3.5" /> : <Lock className="h-3.5 w-3.5" />}
            <span>{kb.visibility === 'public' ? m.knowledge_page_kb_visibility_public() : m.knowledge_page_kb_visibility_internal()}</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" asChild>
            <Link to="/app/knowledge/$kbSlug/add-source" params={{ kbSlug }}>
              <Plus className="h-4 w-4 mr-2" />
              {m.knowledge_detail_add_source()}
            </Link>
          </Button>
          <Button variant="ghost" size="sm" asChild>
            <Link to="/app/knowledge">
              <ArrowLeft className="h-4 w-4 mr-2" />
              {m.knowledge_page_intro_heading()}
            </Link>
          </Button>
        </div>
      </div>

      {/* Tab bar */}
      <div className="border-b border-[var(--color-border)]">
        <nav className="-mb-px flex gap-6">
          {tabEntries.map(({ id, to, icon: TabIcon, label, badge }) => {
            const requiredCap = TAB_CAPABILITIES[id]
            const hasAccess = !requiredCap || user?.hasCapability(requiredCap) !== false

            if (!hasAccess) {
              // Grayed tab: visible, not clickable, tooltip on hover (D4).
              return (
                <ProductCapabilityGuard
                  key={id}
                  capability={requiredCap}
                  tooltip={m.capability_tooltip_knowledge_only()}
                >
                  <span className="flex items-center gap-1.5 pb-3 text-sm font-medium border-b-2 border-transparent text-[var(--color-muted-foreground)]">
                    <TabIcon className="h-4 w-4" />
                    {label}
                  </span>
                </ProductCapabilityGuard>
              )
            }

            return (
              <Link
                key={id}
                to={to}
                params={{ kbSlug }}
                activeProps={{
                  className: 'border-[var(--color-accent)] text-[var(--color-foreground)]',
                }}
                inactiveProps={{
                  className: 'border-transparent text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]',
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
            )
          })}
        </nav>
      </div>

      {/* Active tab content rendered by child route */}
      <Outlet />
    </div>
  )
}
