// Cluster detail (knowledge-gap-to-article phase 1): the questions behind one
// cluster, what the best articles still leave open, and the proposal section
// with the two final decisions. The API has no by-key endpoint, so this page
// re-fetches the widest list (30 days, decided clusters included) and finds its
// cluster by key; a decided cluster therefore stays reachable.
import { createFileRoute, Link } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, ArrowLeft } from 'lucide-react'
import { useAuth } from '@/lib/auth'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { StatCard } from '@/components/ui/stat-card'
import { PageContainer } from '@/components/ui/page-container'
import { PageHeader } from '@/components/ui/page-header'
import { ListEmptyState, ListLoadingState } from '@/components/ui/list-state'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { RoleGuard } from '@/components/layout/RoleGuard'
import { apiFetch } from '@/lib/apiFetch'
import { queryLogger } from '@/lib/logger'
import * as m from '@/paraglide/messages'
import { GapProposal } from './_components/GapProposal'
import { GapsAccessGate } from './_components/GapsAccessGate'
import {
  clusterKindLabel,
  clustersPath,
  articleLabel,
  clusterTitle,
  decisionLabel,
  isWebUrl,
  type ClustersResponse,
} from './-cluster-types'
import { formatRelativeTime } from './-gap-types'

export const Route = createFileRoute('/app/knowledge/gaps/clusters/$clusterKey')({
  component: () => (
    <ProductGuard product="knowledge">
      <RoleGuard minRole="kb_manager">
        <GapsAccessGate width="3xl">
          <GapClusterDetailPage />
        </GapsAccessGate>
      </RoleGuard>
    </ProductGuard>
  ),
})

const backButton = (
  <Button variant="outline" size="sm" asChild>
    <Link to="/app/knowledge/gaps/clusters">
      <ArrowLeft className="h-4 w-4 mr-2" />
      {m.gaps_clusters_back()}
    </Link>
  </Button>
)

export function GapClusterDetailPage() {
  const { clusterKey } = Route.useParams()
  const auth = useAuth()

  const detailQuery = useQuery<ClustersResponse>({
    queryKey: ['app-gap-clusters', 'detail', clusterKey],
    queryFn: async () => {
      try {
        return await apiFetch<ClustersResponse>(clustersPath(30, true))
      } catch (err) {
        queryLogger.warn('Gap cluster detail fetch failed', { error: err })
        throw err
      }
    },
    enabled: auth.isAuthenticated && clusterKey !== '',
    retry: false,
  })

  const cluster = detailQuery.data?.clusters.find((row) => row.cluster_key === clusterKey)

  if (detailQuery.isLoading) {
    return (
      <PageContainer width="3xl" gap="6">
        <ListLoadingState label={m.admin_shared_loading()} />
      </PageContainer>
    )
  }
  if (detailQuery.isError) {
    return (
      <PageContainer width="3xl" gap="6">
        <div className="flex justify-end">{backButton}</div>
        <QueryErrorState error={detailQuery.error} onRetry={() => void detailQuery.refetch()} />
      </PageContainer>
    )
  }
  if (!cluster) {
    return (
      <PageContainer width="3xl" gap="6">
        <div className="flex justify-end">{backButton}</div>
        <ListEmptyState icon={AlertTriangle} title={m.gaps_clusters_not_found()} />
      </PageContainer>
    )
  }

  return (
    <PageContainer width="3xl" gap="6">
      <PageHeader title={clusterTitle(cluster)} actions={backButton} />

      <p className="-mb-3 text-xs text-gray-500">{m.gaps_clusters_window_note()}</p>

      <div className="grid gap-3 sm:grid-cols-3">
        <StatCard
          label={m.gaps_clusters_column_count()}
          value={cluster.count}
          sub={m.gaps_clusters_split({ widget: cluster.widget_count, internal: cluster.internal_count })}
        />
        <StatCard label={m.gaps_clusters_column_kind()} value={<Badge variant="info">{clusterKindLabel(cluster.kind)}</Badge>} />
        <StatCard label={m.gaps_clusters_column_last_seen()} value={formatRelativeTime(cluster.last_seen)} />
      </div>

      <div>
        <Badge variant={cluster.decision === 'none' ? 'outline' : 'secondary'}>
          {cluster.decision === 'none' ? m.gaps_status_open() : decisionLabel(cluster.decision)}
        </Badge>
      </div>

      <div>
        <h2 className="mb-2 text-sm font-semibold text-gray-900">{m.gaps_clusters_questions_heading()}</h2>
        {cluster.questions.length > 0 ? (
          <ul className="list-disc space-y-1 pl-5 text-sm text-gray-900">
            {cluster.questions.map((question) => (
              <li key={question}>{question}</li>
            ))}
          </ul>
        ) : (
          // Below telemetry level full a widget cluster has no text either, so only
          // a cluster without widget signals is an internal-chat one.
          <p className="text-sm text-gray-600">{cluster.widget_count === 0 ? m.gaps_clusters_internal_only() : '—'}</p>
        )}
      </div>

      {cluster.open_parts.length > 0 && (
        <div>
          <h2 className="mb-2 text-sm font-semibold text-gray-900">{m.gaps_clusters_open_parts_heading()}</h2>
          <ul className="list-disc space-y-1 pl-5 text-sm text-gray-900">
            {cluster.open_parts.map((part) => (
              <li key={part}>{part}</li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <h2 className="mb-2 text-sm font-semibold text-gray-900">{m.gaps_clusters_articles_heading()}</h2>
        {cluster.articles.length > 0 ? (
          <ul className="space-y-1 text-sm text-gray-900">
            {cluster.articles.map((article) => (
              <li key={`${article.title ?? ''}|${article.url ?? ''}`}>
                {isWebUrl(article.url) ? (
                  <a href={article.url} target="_blank" rel="noopener noreferrer" className="underline">
                    {articleLabel(article)}
                  </a>
                ) : (
                  (articleLabel(article) ?? m.gaps_clusters_no_article())
                )}
                {article.score != null && (
                  <>
                    {' '}
                    <span className="text-gray-500">
                      {m.gaps_clusters_article_score({ score: String(Math.round(article.score * 100)) })}
                    </span>
                  </>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-gray-600">{m.gaps_clusters_no_article()}</p>
        )}
      </div>

      <GapProposal cluster={cluster} />
    </PageContainer>
  )
}
