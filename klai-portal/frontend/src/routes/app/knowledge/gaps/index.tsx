// The Knowledge gaps screen: the questions the chat could not answer well, grouped
// by topic from the stored chat signals (knowledge-gap-to-article.md). The API's
// order (distinct conversations, then last seen) is the order shown here; the
// page sorts nothing itself. One filter picks article gaps (default), visitors
// who asked for a person, or both.
import { useState } from 'react'
import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, ArrowLeft, FileText } from 'lucide-react'
import { useAuth } from '@/lib/auth'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { PageContainer } from '@/components/ui/page-container'
import { PageHeader } from '@/components/ui/page-header'
import {
  DataTable,
  DataTableBody,
  DataTableCell,
  DataTableHead,
  DataTableHeader,
  DataTableRow,
} from '@/components/ui/data-table'
import { ListEmptyState, ListLoadingState } from '@/components/ui/list-state'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { RoleGuard } from '@/components/layout/RoleGuard'
import { apiFetch } from '@/lib/apiFetch'
import { queryLogger } from '@/lib/logger'
import * as m from '@/paraglide/messages'
import { GapsAccessGate } from './_components/GapsAccessGate'
import { TranscriptImportDialog } from './_components/TranscriptImportDialog'
import { formatRelativeTime, type KBsResponse } from './-gap-types'
import {
  articleLabel,
  clusterKindLabel,
  clustersPath,
  clusterTitle,
  decisionLabel,
  isWebUrl,
  type ClusterRow,
  type ClustersResponse,
  type GapView,
} from './-cluster-types'

const PERIODS = [7, 14, 30]
const VIEWS: GapView[] = ['article_gaps', 'contact_requests', 'all']
const QUESTION_MAX = 90

export const Route = createFileRoute('/app/knowledge/gaps/')({
  component: () => (
    <ProductGuard product="knowledge">
      <RoleGuard minRole="kb_manager">
        <GapsAccessGate width="6xl">
          <GapsPage />
        </GapsAccessGate>
      </RoleGuard>
    </ProductGuard>
  ),
})

const shorten = (text: string) => (text.length > QUESTION_MAX ? `${text.slice(0, QUESTION_MAX).trimEnd()}…` : text)

function viewLabel(view: GapView): string {
  switch (view) {
    case 'article_gaps':
      return m.gaps_view_article_gaps()
    case 'contact_requests':
      return m.gaps_view_contact_requests()
    case 'all':
      return m.gaps_filter_all()
  }
}

export function GapsPage() {
  const auth = useAuth()
  const navigate = useNavigate()
  const [days, setDays] = useState(7)
  const [view, setView] = useState<GapView>('article_gaps')
  const [includeDecided, setIncludeDecided] = useState(false)

  const { data, isLoading, isError, error, refetch } = useQuery<ClustersResponse>({
    queryKey: ['app-gap-clusters', 'list', days, view, includeDecided],
    queryFn: async () => {
      try {
        return await apiFetch<ClustersResponse>(clustersPath(days, includeDecided, view))
      } catch (err) {
        queryLogger.warn('Gap clusters fetch failed', { error: err })
        throw err
      }
    },
    enabled: auth.isAuthenticated,
    retry: false,
  })

  // For the transcript import: the organisation's own knowledge bases.
  const { data: kbsData } = useQuery<KBsResponse>({
    queryKey: ['app-knowledge-bases-for-gaps'],
    queryFn: async () => apiFetch<KBsResponse>('/api/app/knowledge-bases'),
    enabled: auth.isAuthenticated,
    retry: false,
  })
  const orgKbs = (kbsData?.knowledge_bases ?? []).filter((kb) => kb.owner_type === 'org')

  const clusters = data?.clusters ?? []

  const headerActions = (
    <div className="flex flex-wrap items-center gap-2">
      <TranscriptImportDialog orgKbs={orgKbs} />
      <Button variant="outline" size="sm" asChild>
        <Link to="/app/knowledge/gaps/support-cases">
          <FileText className="h-4 w-4 mr-2" />
          {m.support_cases_action_open()}
        </Link>
      </Button>
      <Button variant="outline" size="sm" asChild>
        <Link to="/app/knowledge">
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.knowledge_page_intro_heading()}
        </Link>
      </Button>
    </div>
  )

  return (
    <PageContainer width="6xl" gap="6">
      <PageHeader title={m.gaps_page_title()} description={m.gaps_clusters_description()} actions={headerActions} />

      <div className="flex flex-wrap items-end gap-4">
        <div className="space-y-1.5">
          <Label htmlFor="gaps-filter-view">{m.gaps_filter_show()}</Label>
          <Select
            id="gaps-filter-view"
            value={view}
            onChange={(e) => setView(e.target.value as GapView)}
            className="w-auto"
          >
            {VIEWS.map((option) => (
              <option key={option} value={option}>{viewLabel(option)}</option>
            ))}
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="gaps-filter-days">{m.gaps_filter_days()}</Label>
          <Select
            id="gaps-filter-days"
            value={String(days)}
            onChange={(e) => setDays(Number(e.target.value))}
            className="w-auto"
          >
            {PERIODS.map((period) => (
              <option key={period} value={String(period)}>{`${period}d`}</option>
            ))}
          </Select>
        </div>
        <Checkbox
          label={m.gaps_clusters_include_decided()}
          checked={includeDecided}
          onChange={(e) => setIncludeDecided(e.target.checked)}
        />
      </div>

      {isError ? (
        <QueryErrorState error={error} onRetry={() => void refetch()} />
      ) : isLoading ? (
        <ListLoadingState label={m.admin_shared_loading()} />
      ) : clusters.length === 0 ? (
        <ListEmptyState icon={AlertTriangle} title={m.gaps_clusters_empty()} />
      ) : (
        <div className="overflow-x-auto">
          <DataTable className="table-fixed min-w-5xl">
            <DataTableHeader>
              <DataTableRow>
                <DataTableHead className="w-48">{m.gaps_column_subject()}</DataTableHead>
                <DataTableHead className="w-44">{m.gaps_clusters_column_kind()}</DataTableHead>
                <DataTableHead align="right" className="w-32">{m.gaps_column_occurrences()}</DataTableHead>
                <DataTableHead>{m.gaps_clusters_column_question()}</DataTableHead>
                <DataTableHead className="w-44">{m.gaps_clusters_column_article()}</DataTableHead>
                <DataTableHead className="w-32">{m.gaps_clusters_column_last_seen()}</DataTableHead>
                <DataTableHead className="w-36">{m.gaps_column_status()}</DataTableHead>
              </DataTableRow>
            </DataTableHeader>
            <DataTableBody>
              {clusters.map((cluster) => (
                <ClusterTableRow
                  key={cluster.cluster_key}
                  cluster={cluster}
                  onOpen={() =>
                    void navigate({
                      to: '/app/knowledge/gaps/$clusterKey',
                      params: { clusterKey: cluster.cluster_key },
                    })
                  }
                />
              ))}
            </DataTableBody>
          </DataTable>
        </div>
      )}
    </PageContainer>
  )
}

function ClusterTableRow({ cluster, onOpen }: { cluster: ClusterRow; onOpen: () => void }) {
  return (
    <DataTableRow interactive onClick={onOpen}>
      <DataTableCell className="truncate" title={clusterTitle(cluster)}>
        <Link
          to="/app/knowledge/gaps/$clusterKey"
          params={{ clusterKey: cluster.cluster_key }}
          className="block truncate font-medium text-rl-dark"
          onClick={(e) => e.stopPropagation()}
        >
          {clusterTitle(cluster)}
        </Link>
      </DataTableCell>
      <DataTableCell>
        <Badge variant="info">{clusterKindLabel(cluster.kind)}</Badge>
      </DataTableCell>
      <DataTableCell align="right" className="font-medium tabular-nums">
        {cluster.count}
      </DataTableCell>
      <DataTableCell className="truncate" title={cluster.question ?? undefined}>
        {cluster.question != null ? (
          shorten(cluster.question)
        ) : (
          // Below telemetry level full a widget cluster has no text either, so only
          // a cluster without widget signals is an internal-chat one.
          <span className="text-gray-500">{cluster.widget_count === 0 ? m.gaps_clusters_internal_only() : '—'}</span>
        )}
      </DataTableCell>
      <DataTableCell className="truncate">
        {cluster.article && isWebUrl(cluster.article.url) ? (
          <a
            href={cluster.article.url}
            target="_blank"
            rel="noopener noreferrer"
            className="underline"
            onClick={(e) => e.stopPropagation()}
          >
            {articleLabel(cluster.article)}
          </a>
        ) : cluster.article && articleLabel(cluster.article) != null ? (
          articleLabel(cluster.article)
        ) : (
          <span className="text-gray-500">{m.gaps_clusters_no_article()}</span>
        )}
      </DataTableCell>
      <DataTableCell className="text-sm text-gray-600">{formatRelativeTime(cluster.last_seen)}</DataTableCell>
      <DataTableCell>
        <div className="flex flex-wrap gap-1">
          {cluster.decision === 'none' ? (
            <Badge variant="outline">{m.gaps_status_open()}</Badge>
          ) : (
            <Badge variant="secondary">{decisionLabel(cluster.decision)}</Badge>
          )}
          {cluster.has_proposal && <Badge variant="info">{m.gaps_clusters_has_proposal()}</Badge>}
        </div>
        {!cluster.decidable && (
          <p className="mt-1 text-xs text-gray-500">{m.gaps_clusters_not_decidable_marker()}</p>
        )}
      </DataTableCell>
    </DataTableRow>
  )
}
