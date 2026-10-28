// Weekly cluster list (knowledge-gap-to-article phase 1): the questions the chat
// could not answer well, clustered by the backend from the stored chat signals.
// The API's order is the order shown here; the page sorts nothing itself.
import { useState } from 'react'
import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, ArrowLeft } from 'lucide-react'
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
import { formatRelativeTime } from './-gap-types'
import {
  clusterKindLabel,
  clustersPath,
  clusterTitle,
  decisionLabel,
  isWebUrl,
  type ClusterRow,
  type ClustersResponse,
} from './-cluster-types'

const PERIODS = [7, 14, 30]
const QUESTION_MAX = 90

export const Route = createFileRoute('/app/knowledge/gaps/clusters/')({
  component: () => (
    <ProductGuard product="knowledge">
      <RoleGuard minRole="kb_manager">
        <GapsAccessGate width="6xl">
          <GapClustersPage />
        </GapsAccessGate>
      </RoleGuard>
    </ProductGuard>
  ),
})

const shorten = (text: string) => (text.length > QUESTION_MAX ? `${text.slice(0, QUESTION_MAX).trimEnd()}…` : text)

export function GapClustersPage() {
  const auth = useAuth()
  const navigate = useNavigate()
  const [days, setDays] = useState(7)
  const [includeDecided, setIncludeDecided] = useState(false)

  const { data, isLoading, isError, error, refetch } = useQuery<ClustersResponse>({
    queryKey: ['app-gap-clusters', 'list', days, includeDecided],
    queryFn: async () => {
      try {
        return await apiFetch<ClustersResponse>(clustersPath(days, includeDecided))
      } catch (err) {
        queryLogger.warn('Gap clusters fetch failed', { error: err })
        throw err
      }
    },
    enabled: auth.isAuthenticated,
    retry: false,
  })

  const clusters = data?.clusters ?? []

  const backButton = (
    <Button variant="outline" size="sm" asChild>
      <Link to="/app/knowledge/gaps">
        <ArrowLeft className="h-4 w-4 mr-2" />
        {m.support_cases_back_to_inbox()}
      </Link>
    </Button>
  )

  return (
    <PageContainer width="6xl" gap="6">
      <PageHeader title={m.gaps_clusters_title()} description={m.gaps_clusters_description()} actions={backButton} />

      <div className="flex flex-wrap items-end gap-4">
        <div className="space-y-1.5">
          <Label htmlFor="gap-clusters-days">{m.gaps_filter_days()}</Label>
          <Select
            id="gap-clusters-days"
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
                <DataTableHead className="w-40">{m.gaps_clusters_column_kind()}</DataTableHead>
                <DataTableHead align="right" className="w-28">{m.gaps_clusters_column_count()}</DataTableHead>
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
                      to: '/app/knowledge/gaps/clusters/$clusterKey',
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
  const split = m.gaps_clusters_split({ widget: cluster.widget_count, internal: cluster.internal_count })
  return (
    <DataTableRow interactive onClick={onOpen}>
      <DataTableCell className="truncate" title={clusterTitle(cluster)}>
        <Link
          to="/app/knowledge/gaps/clusters/$clusterKey"
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
      <DataTableCell align="right" className="tabular-nums">
        <span className="font-medium">{cluster.count}</span>
        <div className="text-xs text-gray-500">{split}</div>
      </DataTableCell>
      <DataTableCell className="truncate" title={cluster.question ?? undefined}>
        {cluster.question != null ? (
          shorten(cluster.question)
        ) : (
          <span className="text-gray-500">{m.gaps_clusters_internal_only()}</span>
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
            {cluster.article.title}
          </a>
        ) : cluster.article ? (
          cluster.article.title
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
          {!cluster.decidable && <span className="text-xs text-gray-500">{m.gaps_clusters_not_decidable_marker()}</span>}
        </div>
      </DataTableCell>
    </DataTableRow>
  )
}
