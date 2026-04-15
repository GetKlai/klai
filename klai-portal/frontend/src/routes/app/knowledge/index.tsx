import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery } from '@tanstack/react-query'
import { Plus, ChevronRight, User, Building2, FolderOpen, FileText, Zap } from 'lucide-react'
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
          className="flex items-center gap-1.5 rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800 transition-colors"
        >
          <Plus className="h-4 w-4" />
          {m.knowledge_new_button()}
        </button>
      </div>

      {error ? (
        <QueryErrorState
          error={error instanceof Error ? error : new Error(String(error))}
          onRetry={() => void refetch()}
        />
      ) : isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-20 rounded-lg bg-gray-50 animate-pulse" />
          ))}
        </div>
      ) : allKbs.length === 0 ? (
        <div className="rounded-lg border border-dashed border-gray-200 py-16 text-center">
          <FolderOpen className="h-10 w-10 text-gray-300 mx-auto mb-3" />
          <p className="text-base font-medium text-gray-900">Nog geen collecties</p>
          <p className="text-sm text-gray-400 mt-1">Maak je eerste collectie aan om kennis toe te voegen.</p>
          <button
            type="button"
            onClick={() => void navigate({ to: '/app/knowledge/new' })}
            className="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800 transition-colors"
          >
            <Plus className="h-4 w-4" />
            Eerste collectie aanmaken
          </button>
        </div>
      ) : (
        <div className="space-y-2">
          {personalKb && (
            <CollectionRow kb={personalKb} stats={statsBySlug[personalKb.slug]} icon={<User className="h-5 w-5" />} label="Persoonlijk" />
          )}
          {orgKb && (
            <CollectionRow kb={orgKb} stats={statsBySlug[orgKb.slug]} icon={<Building2 className="h-5 w-5" />} />
          )}
          {otherKbs.map((kb) => (
            <CollectionRow key={kb.id} kb={kb} stats={statsBySlug[kb.slug]} icon={<FolderOpen className="h-5 w-5" />} />
          ))}
        </div>
      )}
    </div>
  )
}

function CollectionRow({
  kb, stats, icon, label,
}: {
  kb: KnowledgeBase
  stats: KBStatsSummary | undefined
  icon: React.ReactNode
  label?: string
}) {
  const itemCount = stats?.items ?? 0
  const connectorCount = stats?.connectors ?? 0

  return (
    <Link
      to="/app/knowledge/$kbSlug/overview"
      params={{ kbSlug: kb.slug }}
      className="group flex items-center gap-4 rounded-lg border border-gray-200 px-5 py-4 transition-all hover:border-gray-200 hover:shadow-sm"
    >
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-gray-50 text-gray-400 group-hover:bg-gray-50 group-hover:text-gray-900 transition-colors">
        {icon}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-gray-900">{label ?? kb.name}</p>
        {kb.description && (
          <p className="text-xs text-gray-400 truncate mt-0.5">{kb.description}</p>
        )}
      </div>
      <div className="flex items-center gap-4 shrink-0">
        {itemCount > 0 && (
          <span className="flex items-center gap-1.5 text-xs text-gray-400">
            <FileText className="h-3.5 w-3.5" />
            {itemCount}
          </span>
        )}
        {connectorCount > 0 && (
          <span className="flex items-center gap-1.5 text-xs text-gray-400">
            <Zap className="h-3.5 w-3.5" />
            {connectorCount}
          </span>
        )}
        <ChevronRight size={16} className="text-gray-400 group-hover:text-gray-900 transition-colors" />
      </div>
    </Link>
  )
}
