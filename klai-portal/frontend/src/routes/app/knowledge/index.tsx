import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery } from '@tanstack/react-query'
import { Plus, ChevronRight } from 'lucide-react'
import { QueryErrorState } from '@/components/ui/query-error-state'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { queryLogger } from '@/lib/logger'
import { ProductGuard } from '@/components/layout/ProductGuard'

export const Route = createFileRoute('/app/knowledge/')({
  component: () => (
    <ProductGuard product="knowledge">
      <KnowledgePage />
    </ProductGuard>
  ),
})

interface KnowledgeBase {
  id: number
  name: string
  slug: string
  description: string | null
  visibility: string
  owner_type: string
}

interface KBsResponse { knowledge_bases: KnowledgeBase[] }
interface KBStatsSummary { items: number; connectors: number }
interface KBStatsSummaryResponse { stats: Record<string, KBStatsSummary> }

function KnowledgePage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const myUserId = auth.user?.profile?.sub
  const navigate = useNavigate()

  const { data: kbsData, isLoading, error, refetch } = useQuery<KBsResponse>({
    queryKey: ['app-knowledge-bases'],
    queryFn: async () => {
      try { return await apiFetch<KBsResponse>('/api/app/knowledge-bases', token) }
      catch (err) { queryLogger.warn('Knowledge bases fetch failed', { err }); throw err }
    },
    enabled: !!token,
  })

  const { data: statsData } = useQuery<KBStatsSummaryResponse>({
    queryKey: ['app-knowledge-bases-stats-summary'],
    queryFn: async () => {
      try { return await apiFetch<KBStatsSummaryResponse>('/api/app/knowledge-bases/stats-summary', token) }
      catch (err) { queryLogger.warn('KB stats summary fetch failed', { err }); throw err }
    },
    enabled: !!token,
  })

  const statsBySlug = statsData?.stats ?? {}
  const allKbs = kbsData?.knowledge_bases ?? []
  const personalKb = allKbs.find((kb) => kb.slug === `personal-${myUserId}` && kb.owner_type === 'user')
  const orgKb = allKbs.find((kb) => kb.slug === 'org' && kb.owner_type === 'org')
  const defaultSlugs = new Set([personalKb?.slug, orgKb?.slug].filter(Boolean))
  const otherKbs = allKbs.filter((kb) => !defaultSlugs.has(kb.slug))

  const totalItems = Object.values(statsBySlug).reduce((sum, s) => sum + s.items, 0)

  return (
    <div className="mx-auto max-w-3xl px-6 py-10" style={{ fontFamily: 'Inter, system-ui, sans-serif' }}>
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">
            {m.knowledge_page_title()}
          </h1>
          {!isLoading && totalItems > 0 && (
            <p className="mt-1 text-sm text-gray-400">
              {totalItems} bestanden in {allKbs.length} collecties
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={() => void navigate({ to: '/app/knowledge/new' })}
          className="flex items-center gap-1.5 rounded-full border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50"
        >
          <Plus className="h-4 w-4" />
          {m.knowledge_new_button()}
        </button>
      </div>

      {/* Content */}
      {error ? (
        <QueryErrorState
          error={error instanceof Error ? error : new Error(String(error))}
          onRetry={() => void refetch()}
        />
      ) : isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-16 rounded-lg bg-gray-50 animate-pulse" />
          ))}
        </div>
      ) : (
        <div className="space-y-2">
          {personalKb && (
            <CollectionRow kb={personalKb} stats={statsBySlug[personalKb.slug]} label="Persoonlijk" />
          )}
          {orgKb && (
            <CollectionRow kb={orgKb} stats={statsBySlug[orgKb.slug]} />
          )}
          {otherKbs.map((kb) => (
            <CollectionRow key={kb.id} kb={kb} stats={statsBySlug[kb.slug]} />
          ))}
        </div>
      )}
    </div>
  )
}

function CollectionRow({
  kb,
  stats,
  label,
}: {
  kb: KnowledgeBase
  stats: KBStatsSummary | undefined
  label?: string
}) {
  const itemCount = stats?.items ?? 0
  const connectorCount = stats?.connectors ?? 0

  return (
    <Link
      to="/app/knowledge/$kbSlug/overview"
      params={{ kbSlug: kb.slug }}
      className="group flex items-center gap-4 rounded-lg border border-gray-200 px-5 py-4 transition-all hover:border-gray-300 hover:shadow-sm"
    >
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-gray-900">{label ?? kb.name}</p>
        {kb.description && (
          <p className="text-xs text-gray-400 truncate mt-0.5">{kb.description}</p>
        )}
      </div>
      <div className="flex items-center gap-3 shrink-0">
        {itemCount > 0 && (
          <span className="text-xs text-gray-400 tabular-nums">{itemCount} bestanden</span>
        )}
        {connectorCount > 0 && (
          <span className="text-xs text-gray-400 tabular-nums">{connectorCount} bronnen</span>
        )}
        <ChevronRight size={16} className="text-gray-300 group-hover:text-gray-400 transition-colors" />
      </div>
    </Link>
  )
}
