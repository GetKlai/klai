import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  createColumnHelper,
} from '@tanstack/react-table'
import { useState } from 'react'
import { ArrowLeft, RefreshCw, Loader2 } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
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
import { getLocale } from '@/paraglide/runtime'
import { datetime } from '@/paraglide/registry'
import { CONNECTOR_API_BASE } from '@/lib/api'

export const Route = createFileRoute('/admin/connectors/$connectorId/')({
  component: ConnectorDetailPage,
})

interface Connector {
  id: string
  name: string
  connector_type: string
  is_enabled: boolean
  last_sync_at: string | null
  last_sync_status: string | null
  created_at: string
  schedule: string | null
  config: Record<string, unknown>
}

interface SyncRun {
  id: string
  connector_id: string
  status: string
  started_at: string
  completed_at: string | null
  documents_total: number
  documents_ok: number
  documents_failed: number
  bytes_processed: number
  error_details: Array<Record<string, unknown>> | null
}

function formatDate(isoString: string): string {
  return datetime(getLocale(), isoString, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatDuration(started: string, completed: string | null): string {
  if (!completed) return '—'
  const ms = new Date(completed).getTime() - new Date(started).getTime()
  if (ms < 60000) return `${Math.round(ms / 1000)}s`
  return `${Math.round(ms / 60000)}m`
}

function ConnectorTypeBadge({ type }: { type: string }) {
  const labels: Record<string, () => string> = {
    github: m.admin_connectors_type_github,
    google_drive: m.admin_connectors_type_google_drive,
    notion: m.admin_connectors_type_notion,
    ms_docs: m.admin_connectors_type_ms_docs,
  }
  const label = labels[type] ?? (() => type)
  return <Badge variant="secondary">{label()}</Badge>
}

function SyncStatusBadge({ status }: { status: string }) {
  switch (status) {
    case 'RUNNING':
      return <Badge variant="accent">{m.admin_connectors_status_running()}</Badge>
    case 'COMPLETED':
      return <Badge variant="success">{m.admin_connectors_status_completed()}</Badge>
    case 'FAILED':
      return <Badge variant="destructive">{m.admin_connectors_status_failed()}</Badge>
    case 'AUTH_ERROR':
      return <Badge variant="destructive">{m.admin_connectors_status_auth_error()}</Badge>
    default:
      return <Badge variant="outline">{status}</Badge>
  }
}

const columnHelper = createColumnHelper<SyncRun>()

function ConnectorDetailPage() {
  const { connectorId } = Route.useParams()
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [isSyncing, setIsSyncing] = useState(false)

  const { data: connector, isLoading, error } = useQuery({
    queryKey: ['admin-connector', connectorId, token],
    queryFn: async () => {
      const res = await fetch(`${CONNECTOR_API_BASE}/api/v1/connectors/${connectorId}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(m.admin_connectors_error_fetch({ status: String(res.status) }))
      return res.json() as Promise<Connector>
    },
    enabled: !!token,
  })

  const isRunning = connector?.last_sync_status === 'RUNNING'

  const { data: syncs, isLoading: syncsLoading, error: syncsError } = useQuery({
    queryKey: ['admin-connector-syncs', connectorId, token],
    queryFn: async () => {
      const res = await fetch(`${CONNECTOR_API_BASE}/api/v1/connectors/${connectorId}/syncs`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(m.admin_connectors_syncs_error({ status: String(res.status) }))
      return res.json() as Promise<SyncRun[]>
    },
    enabled: !!token,
    refetchInterval: isRunning ? 5000 : false,
  })

  const deleteMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch(`${CONNECTOR_API_BASE}/api/v1/connectors/${connectorId}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(m.admin_connectors_delete_error({ status: String(res.status) }))
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-connectors'] })
      void navigate({ to: '/admin/connectors' })
    },
  })

  async function handleSync() {
    setIsSyncing(true)
    try {
      await fetch(`${CONNECTOR_API_BASE}/api/v1/connectors/${connectorId}/sync`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      void queryClient.invalidateQueries({ queryKey: ['admin-connector', connectorId] })
      void queryClient.invalidateQueries({ queryKey: ['admin-connector-syncs', connectorId] })
    } finally {
      setIsSyncing(false)
    }
  }

  const syncColumns = [
    columnHelper.accessor('started_at', {
      header: () => m.admin_connectors_sync_col_started(),
      cell: (info) => (
        <span className="text-[var(--color-muted-foreground)]">{formatDate(info.getValue())}</span>
      ),
    }),
    columnHelper.accessor('status', {
      header: () => m.admin_connectors_sync_col_status(),
      cell: (info) => <SyncStatusBadge status={info.getValue()} />,
    }),
    columnHelper.accessor('documents_ok', {
      header: () => m.admin_connectors_sync_col_docs(),
      cell: (info) => <span>{info.getValue()}</span>,
    }),
    columnHelper.accessor('documents_failed', {
      header: () => m.admin_connectors_sync_col_failed(),
      cell: (info) => (
        <span className={info.getValue() > 0 ? 'text-[var(--color-destructive)]' : ''}>
          {info.getValue()}
        </span>
      ),
    }),
    columnHelper.display({
      id: 'duration',
      header: () => m.admin_connectors_sync_col_duration(),
      cell: ({ row }) => (
        <span className="text-[var(--color-muted-foreground)]">
          {formatDuration(row.original.started_at, row.original.completed_at)}
        </span>
      ),
    }),
  ]

  const syncTable = useReactTable({
    data: syncs ?? [],
    columns: syncColumns,
    getCoreRowModel: getCoreRowModel(),
  })

  if (isLoading) {
    return (
      <div className="p-8">
        <p className="text-sm text-[var(--color-muted-foreground)]">{m.admin_connectors_loading()}</p>
      </div>
    )
  }

  if (error || !connector) {
    return (
      <div className="p-8 space-y-4">
        <p className="text-sm text-[var(--color-destructive)]">
          {error instanceof Error ? error.message : m.admin_connectors_error_generic()}
        </p>
        <Button variant="ghost" size="sm" onClick={() => void navigate({ to: '/admin/connectors' })}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_connectors_back()}
        </Button>
      </div>
    )
  }

  return (
    <div className="p-8 space-y-6 max-w-3xl">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void navigate({ to: '/admin/connectors' })}
            className="-ml-2"
          >
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div className="flex items-center gap-2">
            <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
              {connector.name}
            </h1>
            <ConnectorTypeBadge type={connector.connector_type} />
          </div>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={isSyncing || isRunning}
            onClick={() => void handleSync()}
          >
            {isSyncing || isRunning
              ? <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              : <RefreshCw className="h-4 w-4 mr-2" />
            }
            {isSyncing || isRunning
              ? m.admin_connectors_syncing()
              : m.admin_connectors_action_sync()}
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="text-[var(--color-destructive)] border-[var(--color-destructive)]/30 hover:bg-[var(--color-destructive)]/5"
            onClick={() => setConfirmingDelete(true)}
          >
            {m.admin_connectors_action_delete()}
          </Button>
        </div>
      </div>

      {deleteMutation.error && (
        <p className="text-sm text-[var(--color-destructive)]">
          {deleteMutation.error instanceof Error
            ? deleteMutation.error.message
            : m.admin_connectors_error_generic()}
        </p>
      )}

      <Card>
        <CardContent className="pt-6">
          <h2 className="text-sm font-semibold text-[var(--color-purple-deep)] mb-4">
            {m.admin_connectors_info_title()}
          </h2>
          <dl className="grid grid-cols-2 gap-x-8 gap-y-3 text-sm">
            {connector.connector_type === 'github' && (
              <>
                <div>
                  <dt className="text-[var(--color-muted-foreground)]">
                    {m.admin_connectors_config_label_repo()}
                  </dt>
                  <dd className="font-medium text-[var(--color-purple-deep)]">
                    {String(connector.config.repo_owner)}/{String(connector.config.repo_name)}
                  </dd>
                </div>
                <div>
                  <dt className="text-[var(--color-muted-foreground)]">
                    {m.admin_connectors_config_label_branch()}
                  </dt>
                  <dd className="font-medium text-[var(--color-purple-deep)]">
                    {String(connector.config.branch)}
                  </dd>
                </div>
                <div>
                  <dt className="text-[var(--color-muted-foreground)]">
                    {m.admin_connectors_config_label_kb()}
                  </dt>
                  <dd className="font-medium text-[var(--color-purple-deep)]">
                    {String(connector.config.kb_slug)}
                  </dd>
                </div>
                <div>
                  <dt className="text-[var(--color-muted-foreground)]">
                    {m.admin_connectors_config_label_installation_id()}
                  </dt>
                  <dd className="font-medium text-[var(--color-purple-deep)]">
                    {String(connector.config.installation_id)}
                  </dd>
                </div>
              </>
            )}
            <div>
              <dt className="text-[var(--color-muted-foreground)]">
                {m.admin_connectors_schedule_label()}
              </dt>
              <dd className="font-medium text-[var(--color-purple-deep)]">
                {connector.schedule ?? m.admin_connectors_schedule_none()}
              </dd>
            </div>
            {connector.last_sync_at && (
              <div>
                <dt className="text-[var(--color-muted-foreground)]">
                  {m.admin_connectors_col_last_sync()}
                </dt>
                <dd className="font-medium text-[var(--color-purple-deep)]">
                  {formatDate(connector.last_sync_at)}
                </dd>
              </div>
            )}
          </dl>
        </CardContent>
      </Card>

      <div className="space-y-3">
        <h2 className="text-sm font-semibold text-[var(--color-purple-deep)]">
          {m.admin_connectors_syncs_title()}
        </h2>
        <Card>
          <CardContent className="pt-0 px-0 pb-0 overflow-hidden rounded-xl">
            {syncsLoading ? (
              <p className="px-6 py-8 text-sm text-[var(--color-muted-foreground)]">
                {m.admin_connectors_loading()}
              </p>
            ) : syncsError ? (
              <p className="px-6 py-8 text-sm text-[var(--color-destructive)]">
                {syncsError instanceof Error ? syncsError.message : m.admin_connectors_error_generic()}
              </p>
            ) : (syncs ?? []).length === 0 ? (
              <p className="px-6 py-8 text-sm text-[var(--color-muted-foreground)]">
                {m.admin_connectors_syncs_empty()}
              </p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  {syncTable.getHeaderGroups().map((headerGroup) => (
                    <tr key={headerGroup.id} className="border-b border-[var(--color-border)]">
                      {headerGroup.headers.map((header) => (
                        <th
                          key={header.id}
                          className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide"
                        >
                          {flexRender(header.column.columnDef.header, header.getContext())}
                        </th>
                      ))}
                    </tr>
                  ))}
                </thead>
                <tbody>
                  {syncTable.getRowModel().rows.map((row, i) => (
                    <tr
                      key={row.id}
                      className={i % 2 === 0 ? 'bg-[var(--color-card)]' : 'bg-[var(--color-secondary)]'}
                    >
                      {row.getVisibleCells().map((cell) => (
                        <td key={cell.id} className="px-6 py-3 text-[var(--color-purple-deep)]">
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>
      </div>

      <AlertDialog
        open={confirmingDelete}
        onOpenChange={(open) => { if (!open) setConfirmingDelete(false) }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{m.admin_connectors_delete_confirm_title()}</AlertDialogTitle>
            <AlertDialogDescription>
              {m.admin_connectors_delete_confirm_description()}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{m.admin_connectors_cancel()}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-[var(--color-destructive)] text-white hover:bg-[var(--color-destructive)]/90"
              onClick={() => {
                setConfirmingDelete(false)
                deleteMutation.mutate()
              }}
            >
              {m.admin_connectors_action_delete()}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
