import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery, useQueries } from '@tanstack/react-query'
import { useState, useEffect } from 'react'
import { Plus, CheckCircle2, AlertTriangle, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  DataTable,
  DataTableHeader,
  DataTableBody,
  DataTableRow,
  DataTableHead,
} from '@/components/ui/data-table'
import { ListLoadingState, ListEmptyState } from '@/components/ui/list-state'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import * as m from '@/paraglide/messages'
import { RoleGuard } from '@/components/layout/RoleGuard'
import { apiFetch } from '@/lib/apiFetch'
import type { ConnectorSummary, KnowledgeBase, MembersResponse } from './-kb-types'
import { kbQueryKeys } from '@/lib/kb-query-keys'
import { useConnectorDelete, useConnectorReconnect, useConnectorSync } from './-connectors-hooks'
import { ConnectorRow, type ConnectorLiveProgress } from './-connectors-row'
import { summarizeConnectorSyncError } from './-connectors-sync-errors'

export const Route = createFileRoute('/app/knowledge/$kbSlug/connectors')({
  validateSearch: (search: Record<string, unknown>) => ({
    oauth: typeof search.oauth === 'string' ? search.oauth : undefined,
  }),
  component: () => (
    <RoleGuard minRole="kb_manager">
      <ConnectorsTab />
    </RoleGuard>
  ),
})

function ConnectorsTab() {
  const { kbSlug } = Route.useParams()
  const navigate = useNavigate({ from: Route.fullPath })
  const auth = useAuth()
  const { oauth } = Route.useSearch()
  const [showOAuthBanner, setShowOAuthBanner] = useState(oauth === 'connected')
  const [showOAuthFailedBanner, setShowOAuthFailedBanner] = useState(oauth === 'failed')
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null)

  // Clean up the ?oauth= param from the URL after mounting so a reload doesn't re-show the banner.
  useEffect(() => {
    if (oauth === 'connected' || oauth === 'failed') {
      window.history.replaceState({}, '', window.location.pathname)
    }
  }, [oauth])
  // SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-5 - InvestigateDialog state.
  const [investigatingConnector, setInvestigatingConnector] = useState<ConnectorSummary | null>(null)
  const deleteMutation = useConnectorDelete(kbSlug)
  const { syncingIds, sync } = useConnectorSync(kbSlug)
  const { reconnectingId, reconnectErrorId, reconnect } = useConnectorReconnect(kbSlug)

  const { data: kb } = useQuery<KnowledgeBase>({
    queryKey: kbQueryKeys.knowledgeBase(kbSlug),
    queryFn: async () => apiFetch<KnowledgeBase>(`/api/app/knowledge-bases/${kbSlug}`),
    enabled: auth.isAuthenticated,
  })
  const { data: members } = useQuery<MembersResponse>({
    queryKey: ['kb-members', kbSlug],
    queryFn: async () => apiFetch<MembersResponse>(`/api/app/knowledge-bases/${kbSlug}/members`),
    enabled: auth.isAuthenticated,
  })
  const myUserId = auth.user?.profile?.sub
  const isCreator = !!(myUserId && kb?.created_by === myUserId)
  const isOwner = isCreator || !!(myUserId && members?.users.some((u) => u.user_id === myUserId && u.role === 'owner'))

  const { data: connectors = [], isLoading } = useQuery<ConnectorSummary[]>({
    queryKey: kbQueryKeys.connectorsPortal(kbSlug),
    queryFn: async () => apiFetch<ConnectorSummary[]>(`/api/app/knowledge-bases/${kbSlug}/connectors/`),
    enabled: auth.isAuthenticated,
    refetchInterval: (query) => {
      const data = query.state.data
      if (Array.isArray(data) && data.some((c) => c.last_sync_status === 'RUNNING' || c.last_sync_status === 'running')) {
        return 5000
      }
      return false
    },
  })

  // SPEC-CRAWLER-006 REQ-08: for every running connector, fetch the latest
  // sync_run so the badge can render live progress (pages_done/pages_total
  // for crawler runs). The connector list endpoint does not carry these
  // fields - they live on connector.sync_runs and are surfaced by
  // SyncRunResolver. Backend caches the upstream call 30s per remote_job_id,
  // so a UI-side 5s poll only generates one upstream call every six ticks.
  const latestRunConnectors = connectors
    .filter((c) => ['RUNNING', 'FAILED', 'AUTH_ERROR'].includes(c.last_sync_status?.toUpperCase() ?? ''))
  const latestRunConnectorIds = latestRunConnectors.map((c) => c.id)
  const latestRunQueries = useQueries({
    queries: latestRunConnectors.map((connector) => {
      const connectorId = connector.id
      return {
        queryKey: ['connector-sync-latest', kbSlug, connectorId],
        queryFn: async () => {
          const runs = await apiFetch<Array<{
            id: string
            status: string
            documents_failed?: number | null
            error_details?: Array<Record<string, unknown>> | null
            pages_done?: number | null
            pages_total?: number | null
            live_resolution_failed?: boolean
          }>>(`/api/app/knowledge-bases/${kbSlug}/connectors/${connectorId}/syncs?limit=1`)
          return runs[0] ?? null
        },
        refetchInterval: connector.last_sync_status?.toUpperCase() === 'RUNNING' ? 5000 : false,
        enabled: auth.isAuthenticated,
      }
    }),
  })
  // Build an id → live-progress map for the JSX below. Empty for terminal rows.
  const liveProgressById: Record<string, ConnectorLiveProgress | undefined> = {}
  const syncErrorById: Record<string, string | undefined> = {}
  latestRunConnectorIds.forEach((connectorId, index) => {
    const run = latestRunQueries[index]?.data
    if (run?.status?.toUpperCase() === 'RUNNING') {
      liveProgressById[connectorId] = {
        pagesDone: run.pages_done ?? null,
        pagesTotal: run.pages_total ?? null,
        liveResolutionFailed: run.live_resolution_failed ?? false,
      }
    }
    const errorSummary = summarizeConnectorSyncError(run)
    if (errorSummary) syncErrorById[connectorId] = errorSummary
  })

  if (isLoading) {
    return <ListLoadingState label={m.admin_connectors_loading()} />
  }

  return (
    <div className="space-y-3">
      {showOAuthBanner && (
        <div className="flex gap-2 items-center rounded-lg border border-[var(--color-success)]/30 bg-[var(--color-success)]/5 p-3 text-xs text-[var(--color-success)]">
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
          <span className="flex-1">{m.admin_connectors_oauth_success()}</span>
          <button onClick={() => setShowOAuthBanner(false)} aria-label={m.admin_connectors_dismiss()} className="hover:opacity-70 transition-opacity">
            <X className="h-3 w-3" />
          </button>
        </div>
      )}
      {showOAuthFailedBanner && (
        <div className="flex gap-2 items-center rounded-lg border border-[var(--color-destructive)]/30 bg-[var(--color-destructive)]/5 p-3 text-xs text-[var(--color-destructive)]">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          <span className="flex-1">{m.admin_connectors_oauth_failed()}</span>
          <button onClick={() => setShowOAuthFailedBanner(false)} aria-label={m.admin_connectors_dismiss()} className="hover:opacity-70 transition-opacity">
            <X className="h-3 w-3" />
          </button>
        </div>
      )}
      {connectors.length > 0 && (
        <DataTable className="table-fixed">
          <DataTableHeader>
            <DataTableRow>
              <DataTableHead className="w-6 px-0 pr-2" />
              <DataTableHead>{m.admin_connectors_col_name()}</DataTableHead>
              <DataTableHead className="w-28">{m.admin_connectors_col_type()}</DataTableHead>
              <DataTableHead className="w-32">{m.admin_connectors_col_status()}</DataTableHead>
              {isOwner && <DataTableHead align="right" className="w-28" />}
            </DataTableRow>
          </DataTableHeader>
          <DataTableBody>
            {connectors.map((connector) => (
              <ConnectorRow
                key={connector.id}
                connector={connector}
                isOwner={isOwner}
                isSyncing={syncingIds.has(connector.id)}
                liveProgress={liveProgressById[connector.id]}
                syncError={syncErrorById[connector.id]}
                reconnecting={reconnectingId === connector.id}
                reconnectFailed={reconnectErrorId === connector.id}
                onSync={(connectorId) => void sync(connectorId)}
                onReconnect={(connectorType, connectorId) => void reconnect(connectorType, connectorId)}
                onEdit={(connectorId) =>
                  void navigate({ to: '/app/knowledge/$kbSlug/edit-connector/$connectorId', params: { kbSlug, connectorId } })}
                onDelete={setConfirmingDeleteId}
                onInvestigate={setInvestigatingConnector}
              />
            ))}
          </DataTableBody>
        </DataTable>
      )}

      {connectors.length === 0 && (
        <ListEmptyState title={m.knowledge_detail_connectors_empty()} />
      )}

      {isOwner && (
        <Button size="sm" variant="outline" onClick={() => void navigate({ to: '/app/knowledge/$kbSlug/add-source', params: { kbSlug } })}>
          <Plus className="h-4 w-4 mr-1" />
          {m.admin_connectors_add_button()}
        </Button>
      )}

      <AlertDialog open={confirmingDeleteId !== null} onOpenChange={(open) => { if (!open) setConfirmingDeleteId(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{m.admin_connectors_delete_confirm_title()}</AlertDialogTitle>
            <AlertDialogDescription>{m.admin_connectors_delete_confirm_description()}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{m.admin_connectors_cancel()}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-[var(--color-destructive)] text-white hover:bg-[var(--color-destructive)]/90"
              onClick={() => { if (confirmingDeleteId) deleteMutation.mutate(confirmingDeleteId); setConfirmingDeleteId(null) }}
            >
              {m.admin_connectors_action_delete()}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-5 - InvestigateDialog. */}
      <AlertDialog open={investigatingConnector !== null} onOpenChange={(open) => { if (!open) setInvestigatingConnector(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{m.admin_connectors_investigate_title()}</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2 text-left">
                <p>{m.admin_connectors_investigate_body()}</p>
                <p className="text-xs text-gray-400">
                  {m.admin_connectors_investigate_hint()}
                </p>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter className="!justify-start gap-2 flex-wrap">
            <AlertDialogCancel>{m.admin_connectors_investigate_close()}</AlertDialogCancel>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => {
                if (investigatingConnector) {
                  window.location.href = `/app/knowledge/${encodeURIComponent(kbSlug)}/edit-connector/${encodeURIComponent(investigatingConnector.id)}?step=auth`
                }
                setInvestigatingConnector(null)
              }}
            >
              {m.admin_connectors_investigate_edit_auth()}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => {
                if (investigatingConnector) {
                  window.location.href = `/app/knowledge/${encodeURIComponent(kbSlug)}/edit-connector/${encodeURIComponent(investigatingConnector.id)}?step=selector`
                }
                setInvestigatingConnector(null)
              }}
            >
              {m.admin_connectors_investigate_edit_selector()}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
