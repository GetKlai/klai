/**
 * SPEC-PORTAL-KENNIS-001 Phase C - KB list page (simpel, plat).
 *
 * Per KB row: icon · name · "N bronnen · M chunks" · status badge.
 * Click row = navigate to /app/knowledge/$kbSlug (default Bronnen tab).
 *
 * No expand chevron, no per-row sync/delete actions, no
 * personal/team/org tabs. The TalkWithData expand-in-list pattern moved
 * to the KB detail page where it has room to breathe.
 */
import { createFileRoute, Link } from '@tanstack/react-router'
import { useState } from 'react'
import { useAuth } from '@/lib/auth'
import { useQuery } from '@tanstack/react-query'
import { Building2, ChevronRight, FolderOpen, Plus, Search, User } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { QueryErrorState } from '@/components/ui/query-error-state'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { kbQueryKeys } from '@/lib/kb-query-keys'

export const Route = createFileRoute('/app/knowledge/')({
  component: () => (
    <ProductGuard product="knowledge">
      <KnowledgePage />
    </ProductGuard>
  ),
})

// -- Types ------------------------------------------------------------------

interface KnowledgeBase {
  id: number
  name: string
  slug: string
  description: string | null
  visibility: string
  docs_enabled: boolean
  owner_type: string
  owner_user_id: string | null
  default_org_role: string | null
}

interface KBsResponse {
  knowledge_bases: KnowledgeBase[]
}

interface KBStatsSummary {
  items: number
  connectors: number
  chunks: number
  sources: number
  gaps_7d: number
  usage_30d: number
  unique_users_30d: number
  active_days_30d: number
}

interface KBStatsSummaryResponse {
  stats: Record<string, KBStatsSummary>
}

// -- Status mapping ---------------------------------------------------------

type Status = 'klaar' | 'bezig' | 'probleem' | 'leeg'

/**
 * Derive a user-visible status from aggregate stats.
 *
 * v1 maps via aggregates only - per-connector statuses live on the KB
 * detail page where each bron renders its own status. The "Probleem"
 * tier is reserved for that view.
 */
function deriveStatus(stats: KBStatsSummary | undefined): Status {
  if (!stats) return 'leeg'
  if (stats.chunks > 0) return 'klaar'
  if (stats.sources > 0 || stats.connectors > 0) return 'bezig'
  return 'leeg'
}

function StatusBadge({ status }: { status: Status }) {
  const labelMap = {
    klaar: m.kb_status_klaar(),
    bezig: m.kb_status_bezig(),
    probleem: m.kb_status_probleem(),
    leeg: m.kb_status_leeg(),
  } as const
  // Subtle treatment: only Probleem stands out. Klaar / Bezig / Leeg are
  // all "no problem yet" tiers and shouldn't shout.
  const variantMap = {
    klaar: 'success' as const,
    bezig: 'secondary' as const,
    probleem: 'destructive' as const,
    leeg: 'secondary' as const,
  }
  return <Badge variant={variantMap[status]}>{labelMap[status]}</Badge>
}

// -- KB row -----------------------------------------------------------------

function KbIcon({ ownerType }: { ownerType: string }) {
  if (ownerType === 'user') return <User className="h-4 w-4" />
  if (ownerType === 'org') return <Building2 className="h-4 w-4" />
  return <FolderOpen className="h-4 w-4" />
}

function KbRow({
  kb,
  stats,
  statsState,
  isMine,
}: {
  kb: KnowledgeBase
  stats: KBStatsSummary | undefined
  statsState: 'loading' | 'unavailable' | 'ready'
  isMine: boolean
}) {
  const status = stats ? deriveStatus(stats) : null
  const statsLabel = stats
    ? [
        stats.sources === 1
          ? m.kb_count_bron_singular()
          : m.kb_count_bronnen({ count: String(stats.sources) }),
        stats.chunks === 1
          ? m.kb_count_chunk_singular()
          : m.kb_count_chunks({ count: String(stats.chunks) }),
      ].join(' · ')
    : statsState === 'loading'
      ? m.knowledge_page_stat_loading()
      : m.knowledge_detail_volume_unavailable()

  return (
    <Link
      to="/app/knowledge/$kbSlug/sources"
      params={{ kbSlug: kb.slug }}
      className="group flex items-center gap-3 px-2 py-3.5 klai-hover"
    >
      <div className="flex h-8 w-8 shrink-0 items-center justify-center text-gray-400">
        <KbIcon ownerType={kb.owner_type} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2 flex-wrap">
          <span className="text-[15px] font-display text-gray-900 truncate">{kb.name}</span>
          {isMine && (
            <Badge variant="secondary" className="text-[10px] py-0 px-1.5">
              Mijn
            </Badge>
          )}
          <span className="text-xs text-gray-400">
            {statsLabel}
          </span>
        </div>
        {kb.description && (
          <p className="text-xs text-gray-400 mt-0.5 truncate">{kb.description}</p>
        )}
      </div>
      {status && <StatusBadge status={status} />}
      <ChevronRight className="h-4 w-4 text-gray-300 shrink-0" />
    </Link>
  )
}

// -- Sort -------------------------------------------------------------------

/**
 * Rank for the KB list ordering. Lower rank = closer to the top.
 *
 * 0 - Personal KBs (owned by the current user - only one per user in practice)
 * 1 - Default org KB (slug "org", rendered as "Organisatiekennis")
 * 2 - Every other org-owned KB (team / topic collections)
 */
function kbSortRank(kb: KnowledgeBase): 0 | 1 | 2 {
  if (kb.owner_type === 'user') return 0
  if (kb.slug === 'org') return 1
  return 2
}

// -- Page -------------------------------------------------------------------

function KnowledgePage() {
  const auth = useAuth()
  const myUserId = auth.user?.profile?.sub
  const [search, setSearch] = useState('')

  const {
    data: kbsData,
    isLoading: kbsLoading,
    error: kbsError,
    refetch: refetchKbs,
  } = useQuery<KBsResponse>({
    queryKey: ['app-knowledge-bases'],
    queryFn: () => apiFetch<KBsResponse>('/api/app/knowledge-bases'),
    enabled: auth.isAuthenticated,
    retry: false,
  })

  const {
    data: statsData,
    isLoading: statsLoading,
    isError: statsError,
  } = useQuery<KBStatsSummaryResponse>({
    queryKey: kbQueryKeys.statsSummary(),
    queryFn: () => apiFetch<KBStatsSummaryResponse>('/api/app/knowledge-bases/stats-summary'),
    enabled: auth.isAuthenticated,
    retry: false,
  })

  const statsBySlug = statsData?.stats ?? {}
  const allKbs = kbsData?.knowledge_bases ?? []

  // Display order: personal KBs first, then the default org KB
  // ("Organisatiekennis", slug='org' per default_knowledge_bases.py),
  // then every other org-owned collection. Array.sort is stable since
  // ES2019, so within each rank the API order is preserved.
  const sortedKbs = [...allKbs].sort((a, b) => kbSortRank(a) - kbSortRank(b))

  const filteredKbs = search.trim()
    ? sortedKbs.filter((kb) => {
        const q = search.toLowerCase()
        return kb.name.toLowerCase().includes(q) || (kb.description ?? '').toLowerCase().includes(q)
      })
    : sortedKbs

  const totalCount = allKbs.length
  const countLabel =
    totalCount === 1 ? m.kb_list_count_one() : m.kb_list_count({ count: String(totalCount) })

  return (
    <div className="mx-auto max-w-3xl px-6 py-10 space-y-8">
      {/* Header - title + subtitle stacked, primary action top-right.
          Matches the dashboard pattern (/app/) and Scribe layout. */}
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1">
          <h1 className="page-title text-[26px] font-display-bold text-gray-900">
            {m.kb_list_title()}
          </h1>
          <p className="text-sm text-gray-400">
            {kbsLoading ? m.kb_list_subtitle() : countLabel}
          </p>
        </div>
        <Link to="/app/knowledge/new">
          <Button size="sm">
            <Plus className="mr-2 h-4 w-4" />
            {m.kb_list_new_collection()}
          </Button>
        </Link>
      </div>

      {/* Korte uitleg — geen kader, gewoon tekst. Zelfde stijl als /app/instructions. */}
      <div className="space-y-3 text-sm text-gray-600">
        <p>{m.kb_intro_body()}</p>
        <p>{m.kb_intro_examples()}</p>
        <p>{m.kb_intro_invoke()}</p>
      </div>

      {/* Search */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400 pointer-events-none" />
        <Input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={m.kb_list_search_placeholder()}
          className="pl-10"
        />
      </div>

      {/* List */}
      <div>
        {kbsLoading ? (
          <div className="border-t border-b border-gray-200 divide-y divide-gray-200">
            {[1, 2, 3].map((i) => (
              <div key={i} className="h-[60px] bg-gray-50 animate-pulse" />
            ))}
          </div>
        ) : kbsError ? (
          <QueryErrorState error={kbsError} onRetry={refetchKbs} />
        ) : filteredKbs.length === 0 ? (
        <div className="rounded-lg border border-dashed border-gray-200 py-10 text-center">
          {search.trim() ? (
            <p className="text-sm text-gray-400">{m.kb_list_empty_search({ q: search.trim() })}</p>
          ) : (
            <>
              <p className="text-sm font-medium text-gray-900">{m.kb_list_empty_no_collections()}</p>
              <Link to="/app/knowledge/new" className="inline-block mt-4">
                <Button variant="default">
                  <Plus className="h-4 w-4" />
                  {m.kb_list_empty_cta()}
                </Button>
              </Link>
            </>
          )}
        </div>
      ) : (
        <div className="border-t border-b border-gray-200 divide-y divide-gray-200">
          {filteredKbs.map((kb) => (
            <KbRow
              key={kb.slug}
              kb={kb}
              stats={statsBySlug[kb.slug]}
              statsState={statsLoading ? 'loading' : statsError ? 'unavailable' : 'ready'}
              isMine={kb.owner_type === 'user' && !!myUserId && kb.slug === `personal-${myUserId}`}
            />
          ))}
        </div>
      )}
      </div>
    </div>
  )
}
