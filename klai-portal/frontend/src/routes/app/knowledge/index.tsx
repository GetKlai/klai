/**
 * SPEC-PORTAL-KENNIS-001 — KB list (refactored).
 *
 * Senior UI pass: less noise, more hierarchy.
 *   - Status indicator → small colored dot, only when not Klaar
 *   - "chunks" removed from the list view (too technical for browse)
 *   - Type icons get a subtle color tint per owner type
 *   - Hover hint: chevron darkens
 */
import { createFileRoute, Link } from '@tanstack/react-router'
import { useState } from 'react'
import { useAuth } from '@/lib/auth'
import { useQuery } from '@tanstack/react-query'
import { Building2, ChevronRight, FolderOpen, Plus, Search, User } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { QueryErrorState } from '@/components/ui/query-error-state'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { ProductGuard } from '@/components/layout/ProductGuard'

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
  bronnen: number
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

function deriveStatus(stats: KBStatsSummary | undefined): Status {
  if (!stats) return 'leeg'
  if (stats.chunks > 0) return 'klaar'
  if (stats.bronnen > 0 || stats.connectors > 0) return 'bezig'
  return 'leeg'
}

/**
 * Status as a small colored dot. Klaar shows nothing — that's the default
 * happy path and a green dot would be visual noise on every row.
 */
function StatusDot({ status }: { status: Status }) {
  if (status === 'klaar') return null
  const colorMap = {
    bezig: 'bg-amber-500',
    probleem: 'bg-red-500',
    leeg: 'bg-gray-300',
  }
  const labelMap = {
    bezig: m.kb_status_bezig(),
    probleem: m.kb_status_probleem(),
    leeg: m.kb_status_leeg(),
  } as const
  return (
    <span
      className="inline-flex items-center gap-1.5 text-xs text-gray-500"
      title={labelMap[status]}
    >
      <span className={`h-2 w-2 rounded-full ${colorMap[status]}`} aria-hidden />
      {labelMap[status]}
    </span>
  )
}

// -- KB row -----------------------------------------------------------------

function KbIcon({ ownerType }: { ownerType: string }) {
  // Subtle tint per owner type — visual hierarchy without shouting.
  if (ownerType === 'user') {
    return (
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-600">
        <User className="h-4 w-4" />
      </div>
    )
  }
  if (ownerType === 'org') {
    return (
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-amber-50 text-amber-700">
        <Building2 className="h-4 w-4" />
      </div>
    )
  }
  return (
    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gray-50 text-gray-500">
      <FolderOpen className="h-4 w-4" />
    </div>
  )
}

function KbRow({
  kb,
  stats,
  isMine,
}: {
  kb: KnowledgeBase
  stats: KBStatsSummary | undefined
  isMine: boolean
}) {
  const bronnen = stats?.bronnen ?? 0
  const status = deriveStatus(stats)

  // Bronnen count in plain Dutch — the user's mental model is sources, not chunks.
  const bronnenLabel = bronnen === 0
    ? 'Nog geen bronnen'
    : bronnen === 1
      ? '1 bron'
      : `${bronnen} bronnen`

  return (
    <Link
      to="/app/knowledge/$kbSlug/bronnen"
      params={{ kbSlug: kb.slug }}
      className="group flex items-center gap-3 px-2 py-3 hover:bg-gray-50 transition-colors"
    >
      <KbIcon ownerType={kb.owner_type} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="text-[15px] font-display text-gray-900 group-hover:underline truncate">
            {kb.name}
          </span>
          {isMine && (
            <span className="text-[10px] uppercase tracking-wide text-blue-600 font-medium shrink-0">
              Mijn
            </span>
          )}
        </div>
        <div className="flex items-center gap-3 text-xs text-gray-400 mt-0.5">
          <span>{bronnenLabel}</span>
          {status !== 'klaar' && <StatusDot status={status} />}
          {kb.description && <span className="truncate">· {kb.description}</span>}
        </div>
      </div>
      <ChevronRight className="h-4 w-4 text-gray-300 shrink-0 transition-colors group-hover:text-gray-500" />
    </Link>
  )
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

  const { data: statsData } = useQuery<KBStatsSummaryResponse>({
    queryKey: ['app-knowledge-bases-stats-summary'],
    queryFn: () => apiFetch<KBStatsSummaryResponse>('/api/app/knowledge-bases/stats-summary'),
    enabled: auth.isAuthenticated,
    retry: false,
  })

  const statsBySlug = statsData?.stats ?? {}
  const allKbs = kbsData?.knowledge_bases ?? []

  const filteredKbs = search.trim()
    ? allKbs.filter((kb) => {
        const q = search.toLowerCase()
        return kb.name.toLowerCase().includes(q) || (kb.description ?? '').toLowerCase().includes(q)
      })
    : allKbs

  // Sort: own personal first, then org, then alpha
  const sortedKbs = [...filteredKbs].sort((a, b) => {
    const aMine = a.owner_type === 'user' && a.slug === `personal-${myUserId}`
    const bMine = b.owner_type === 'user' && b.slug === `personal-${myUserId}`
    if (aMine && !bMine) return -1
    if (!aMine && bMine) return 1
    if (a.owner_type === 'org' && b.owner_type !== 'org') return -1
    if (a.owner_type !== 'org' && b.owner_type === 'org') return 1
    return a.name.localeCompare(b.name)
  })

  return (
    <div className="mx-auto max-w-3xl px-6 pt-14 pb-10">
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-[26px] font-display-bold text-gray-900 leading-none">
          {m.kb_list_title()}
        </h1>
        <Link to="/app/knowledge/new">
          <Button variant="default">
            <Plus className="h-4 w-4" />
            {m.kb_list_new_collection()}
          </Button>
        </Link>
      </div>

      {/* Search */}
      <div className="relative mt-6">
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
      <div className="mt-6">
        {kbsLoading ? (
          <div className="border-t border-b border-gray-200 divide-y divide-gray-200">
            {[1, 2, 3].map((i) => (
              <div key={i} className="flex items-center gap-3 px-2 py-3">
                <div className="h-9 w-9 rounded-lg bg-gray-50 animate-pulse" />
                <div className="flex-1 space-y-1.5">
                  <div className="h-3 w-40 rounded bg-gray-50 animate-pulse" />
                  <div className="h-2.5 w-24 rounded bg-gray-50 animate-pulse" />
                </div>
              </div>
            ))}
          </div>
        ) : kbsError ? (
          <QueryErrorState error={kbsError} onRetry={refetchKbs} />
        ) : sortedKbs.length === 0 ? (
          <div className="rounded-xl border border-dashed border-gray-200 py-12 text-center">
            {search.trim() ? (
              <p className="text-sm text-gray-400">
                {m.kb_list_empty_search({ q: search.trim() })}
              </p>
            ) : (
              <>
                <FolderOpen className="h-8 w-8 text-gray-300 mx-auto mb-3" />
                <p className="text-base font-display text-gray-900">
                  {m.kb_list_empty_no_collections()}
                </p>
                <p className="text-sm text-gray-400 mt-1 max-w-sm mx-auto">
                  Verzamel kennis in collecties — bestanden, links of koppelingen.
                </p>
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
            {sortedKbs.map((kb) => (
              <KbRow
                key={kb.slug}
                kb={kb}
                stats={statsBySlug[kb.slug]}
                isMine={kb.owner_type === 'user' && !!myUserId && kb.slug === `personal-${myUserId}`}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
