/**
 * SPEC-PORTAL-KENNIS-001 Phase C — KB list page (simpel, plat).
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
import { Building2, FolderOpen, Plus, Search, User } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
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

/**
 * Derive a user-visible status from aggregate stats.
 *
 * v1 maps via aggregates only — per-connector statuses live on the KB
 * detail page where each bron renders its own status. The "Probleem"
 * tier is reserved for that view.
 */
function deriveStatus(stats: KBStatsSummary | undefined): Status {
  if (!stats) return 'leeg'
  if (stats.chunks > 0) return 'klaar'
  if (stats.bronnen > 0 || stats.connectors > 0) return 'bezig'
  return 'leeg'
}

function StatusBadge({ status }: { status: Status }) {
  const labelMap = {
    klaar: m.kb_status_klaar(),
    bezig: m.kb_status_bezig(),
    probleem: m.kb_status_probleem(),
    leeg: m.kb_status_leeg(),
  } as const
  const variantMap = {
    klaar: 'success' as const,
    bezig: 'warning' as const,
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

function KbRow({ kb, stats }: { kb: KnowledgeBase; stats: KBStatsSummary | undefined }) {
  const bronnen = stats?.bronnen ?? 0
  const chunks = stats?.chunks ?? 0
  const status = deriveStatus(stats)

  const bronnenLabel = bronnen === 1 ? m.kb_count_bron_singular() : m.kb_count_bronnen({ count: String(bronnen) })
  const chunksLabel = chunks === 1 ? m.kb_count_chunk_singular() : m.kb_count_chunks({ count: String(chunks) })

  return (
    <Link
      // Phase D will replace /overview with /bronnen as the default tab.
      // Keeping /overview here means Phase C ships green without depending
      // on Phase D — the link still works against main's existing routes.
      to="/app/knowledge/$kbSlug/overview"
      params={{ kbSlug: kb.slug }}
      className="group flex items-center gap-4 rounded-lg border border-gray-200 px-4 py-4 hover:bg-gray-50 transition-colors"
    >
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gray-50 text-gray-400">
        <KbIcon ownerType={kb.owner_type} />
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-[15px] font-display text-gray-900 group-hover:underline truncate">{kb.name}</p>
        <p className="text-xs text-gray-400 mt-0.5 truncate">
          {bronnenLabel} · {chunksLabel}
        </p>
      </div>
      <StatusBadge status={status} />
    </Link>
  )
}

// -- Page -------------------------------------------------------------------

function KnowledgePage() {
  const auth = useAuth()
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

  return (
    <div className="mx-auto max-w-3xl px-6 py-10 space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-[26px] font-display-bold text-gray-900">{m.kb_list_title()}</h1>
        <Link to="/app/knowledge/new">
          <Button variant="default">
            <Plus className="h-4 w-4" />
            {m.kb_list_new_collection()}
          </Button>
        </Link>
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
      {kbsLoading ? (
        <div className="space-y-2">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-[72px] rounded-lg bg-gray-50 animate-pulse" />
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
        <div className="space-y-2">
          {filteredKbs.map((kb) => (
            <KbRow key={kb.slug} kb={kb} stats={statsBySlug[kb.slug]} />
          ))}
        </div>
      )}
    </div>
  )
}
