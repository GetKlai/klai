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
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Tooltip } from '@/components/ui/tooltip'
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
import { RefreshCw, Eye, Trash2, Loader2 } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { getLocale } from '@/paraglide/runtime'
import { datetime } from '@/paraglide/registry'
import { CONNECTOR_API_BASE } from '@/lib/api'

export const Route = createFileRoute('/admin/connectors/')({
  component: ConnectorsPage,
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

function formatDate(isoString: string): string {
  return datetime(getLocale(), isoString, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function TypeBadge({ type }: { type: string }) {
  const labels: Record<string, () => string> = {
    github: m.admin_connectors_type_github,
    google_drive: m.admin_connectors_type_google_drive,
    notion: m.admin_connectors_type_notion,
    ms_docs: m.admin_connectors_type_ms_docs,
  }
  const label = labels[type] ?? (() => type)
  return <Badge variant="secondary">{label()}</Badge>
}

function StatusBadge({ connector }: { connector: Connector }) {
  if (!connector.is_enabled) {
    return <Badge variant="outline">{m.admin_connectors_status_disabled()}</Badge>
  }
  switch (connector.last_sync_status) {
    case 'RUNNING':
      return <Badge variant="accent">{m.admin_connectors_status_running()}</Badge>
    case 'COMPLETED':
      return <Badge variant="success">{m.admin_connectors_status_completed()}</Badge>
    case 'FAILED':
      return <Badge variant="destructive">{m.admin_connectors_status_failed()}</Badge>
    case 'AUTH_ERROR':
      return <Badge variant="destructive">{m.admin_connectors_status_auth_error()}</Badge>
    default:
      return <Badge variant="secondary">{m.admin_connectors_status_never()}</Badge>
  }
}

const columnHelper = createColumnHelper<Connector>()

function ConnectorsPage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null)
  const [syncingIds, setSyncingIds] = useState<Set<string>>(new Set())

  const { data, isLoading, error } = useQuery({
    queryKey: ['admin-connectors', token],
    queryFn: async () => {
      const res = await fetch(`${CONNECTOR_API_BASE}/api/v1/connectors`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(m.admin_connectors_error_fetch({ status: String(res.status) }))
      return res.json() as Promise<Connector[]>
    },
    enabled: !!token,
  })

  const connectors = data ?? []

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`${CONNECTOR_API_BASE}/api/v1/connectors/${id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(m.admin_connectors_delete_error({ status: String(res.status) }))
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-connectors'] })
    },
  })

  async function handleSync(id: string) {
    setSyncingIds((prev) => new Set([...prev, id]))
    try {
      await fetch(`${CONNECTOR_API_BASE}/api/v1/connectors/${id}/sync`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      void queryClient.invalidateQueries({ queryKey: ['admin-connectors'] })
    } finally {
      setSyncingIds((prev) => {
        const next = new Set(prev)
        next.delete(id)
        return next
      })
    }
  }

  const columns = [
    columnHelper.accessor('name', {
      header: () => m.admin_connectors_col_name(),
      cell: (info) => <span className="font-medium">{info.getValue()}</span>,
    }),
    columnHelper.accessor('connector_type', {
      header: () => m.admin_connectors_col_type(),
      cell: (info) => <TypeBadge type={info.getValue()} />,
    }),
    columnHelper.display({
      id: 'status',
      header: () => m.admin_connectors_col_status(),
      cell: ({ row }) => <StatusBadge connector={row.original} />,
    }),
    columnHelper.accessor('last_sync_at', {
      header: () => m.admin_connectors_col_last_sync(),
      cell: (info) => {
        const val = info.getValue()
        return val ? (
          <span className="text-[var(--color-muted-foreground)]">{formatDate(val)}</span>
        ) : (
          <span className="text-[var(--color-muted-foreground)]">—</span>
        )
      },
    }),
    columnHelper.display({
      id: 'actions',
      header: () => m.admin_connectors_col_actions(),
      cell: ({ row }) => {
        const connector = row.original
        const isSyncing = syncingIds.has(connector.id)
        const isRunning = connector.last_sync_status === 'RUNNING'
        return (
          <div className="flex items-center gap-1">
            <Tooltip label={m.admin_connectors_action_sync()}>
              <button
                disabled={isSyncing || isRunning}
                onClick={() => void handleSync(connector.id)}
                aria-label={m.admin_connectors_action_sync()}
                className="flex h-7 w-7 items-center justify-center text-[var(--color-accent)] transition-opacity hover:opacity-70 disabled:opacity-40"
              >
                {isSyncing || isRunning
                  ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  : <RefreshCw className="h-3.5 w-3.5" />
                }
              </button>
            </Tooltip>
            <Tooltip label={m.admin_connectors_action_view()}>
              <button
                onClick={() => void navigate({ to: '/admin/connectors/$connectorId', params: { connectorId: connector.id } })}
                aria-label={m.admin_connectors_action_view()}
                className="flex h-7 w-7 items-center justify-center text-[var(--color-muted-foreground)] transition-opacity hover:opacity-70"
              >
                <Eye className="h-3.5 w-3.5" />
              </button>
            </Tooltip>
            <Tooltip label={m.admin_connectors_action_delete()}>
              <button
                onClick={() => setConfirmingDeleteId(connector.id)}
                aria-label={m.admin_connectors_action_delete()}
                className="flex h-7 w-7 items-center justify-center text-[var(--color-destructive)] transition-opacity hover:opacity-70"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </Tooltip>
          </div>
        )
      },
    }),
  ]

  const table = useReactTable({
    data: connectors,
    columns,
    getCoreRowModel: getCoreRowModel(),
  })

  const pageError =
    (error instanceof Error ? error.message : error ? m.admin_connectors_error_generic() : null) ??
    (deleteMutation.error instanceof Error ? deleteMutation.error.message : deleteMutation.error ? m.admin_connectors_error_generic() : null)

  return (
    <div className="p-8 space-y-6">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
            {m.admin_connectors_heading()}
          </h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {m.admin_connectors_subtitle()}
          </p>
        </div>
        <Button onClick={() => void navigate({ to: '/admin/connectors/new' })}>
          {m.admin_connectors_add_button()}
        </Button>
      </div>

      {pageError && (
        <p className="text-sm text-[var(--color-destructive)]">{pageError}</p>
      )}

      <Card>
        <CardContent className="pt-0 px-0 pb-0 overflow-hidden rounded-xl">
          {isLoading ? (
            <p className="px-6 py-8 text-sm text-[var(--color-muted-foreground)]">
              {m.admin_connectors_loading()}
            </p>
          ) : connectors.length === 0 ? (
            <div className="px-6 py-12 text-center space-y-3">
              <p className="text-sm font-medium text-[var(--color-purple-deep)]">
                {m.admin_connectors_empty()}
              </p>
              <p className="text-sm text-[var(--color-muted-foreground)]">
                {m.admin_connectors_empty_description()}
              </p>
              <Button onClick={() => void navigate({ to: '/admin/connectors/new' })} variant="outline">
                {m.admin_connectors_add_button()}
              </Button>
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                {table.getHeaderGroups().map((headerGroup) => (
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
                {table.getRowModel().rows.map((row, i) => (
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

      <AlertDialog
        open={confirmingDeleteId !== null}
        onOpenChange={(open) => { if (!open) setConfirmingDeleteId(null) }}
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
                if (confirmingDeleteId) deleteMutation.mutate(confirmingDeleteId)
                setConfirmingDeleteId(null)
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
