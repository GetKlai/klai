import { createFileRoute, useNavigate } from '@tanstack/react-router'
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  createColumnHelper,
} from '@tanstack/react-table'
import { useState } from 'react'
import { Plus, Loader2, Eye, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import { QueryErrorState } from '@/components/ui/query-error-state'
import * as m from '@/paraglide/messages'
import { getLocale } from '@/paraglide/runtime'
import { datetime } from '@/paraglide/registry'
import { useIntegrations, useDeleteIntegration } from './-hooks'
import type { IntegrationResponse } from './-types'

export const Route = createFileRoute('/admin/integrations/')({
  component: IntegrationsPage,
})

function formatRelativeTime(isoString: string | null): string {
  if (!isoString) return '\u2014'
  return datetime(getLocale(), isoString, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function StatusBadge({ active }: { active: boolean }) {
  return active ? (
    <Badge variant="success">{m.admin_integrations_status_active()}</Badge>
  ) : (
    <Badge variant="destructive">{m.admin_integrations_status_revoked()}</Badge>
  )
}

const columnHelper = createColumnHelper<IntegrationResponse>()

function IntegrationsPage() {
  const navigate = useNavigate()
  const { data, isLoading, error, refetch } = useIntegrations()
  const deleteMutation = useDeleteIntegration()
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)

  const integrations = Array.isArray(data) ? data : []

  const columns = [
    columnHelper.accessor('name', {
      header: () => m.admin_integrations_col_name(),
      cell: (info) => (
        <button
          onClick={() =>
            navigate({
              to: '/admin/integrations/$id',
              params: { id: String(info.row.original.id) },
            })
          }
          className="font-medium text-[var(--color-foreground)] hover:text-[var(--color-accent)] transition-colors text-left"
        >
          {info.getValue()}
        </button>
      ),
    }),
    columnHelper.accessor('key_prefix', {
      header: () => m.admin_integrations_col_key_prefix(),
      cell: (info) => (
        <code className="text-xs font-mono text-[var(--color-muted-foreground)]">
          {info.getValue()}...
        </code>
      ),
    }),
    columnHelper.accessor('active', {
      header: () => m.admin_integrations_col_status(),
      cell: (info) => <StatusBadge active={info.getValue()} />,
    }),
    columnHelper.accessor('kb_access_count', {
      header: () => m.admin_integrations_col_kb_access(),
      cell: (info) => (
        <span className="text-sm tabular-nums">{info.getValue()}</span>
      ),
    }),
    columnHelper.accessor('last_used_at', {
      header: () => m.admin_integrations_col_last_used(),
      cell: (info) => (
        <span className="text-sm text-[var(--color-muted-foreground)] whitespace-nowrap tabular-nums">
          {formatRelativeTime(info.getValue())}
        </span>
      ),
    }),
    columnHelper.display({
      id: 'actions',
      header: () => '',
      cell: ({ row }) => {
        const isConfirming = confirmDeleteId === String(row.original.id)
        return (
          <InlineDeleteConfirm
            isConfirming={isConfirming}
            isPending={deleteMutation.isPending}
            label={m.admin_integrations_delete_confirm({ name: row.original.name })}
            cancelLabel={m.admin_users_cancel()}
            onConfirm={() => { deleteMutation.mutate(String(row.original.id)); setConfirmDeleteId(null) }}
            onCancel={() => setConfirmDeleteId(null)}
          >
            <div className="flex items-start justify-end gap-2 mt-px">
              <button
                onClick={() => setConfirmDeleteId(String(row.original.id))}
                aria-label={`Delete ${row.original.name}`}
                className="inline-flex items-center justify-center text-[var(--color-destructive)] transition-opacity hover:opacity-70"
              >
                <Trash2 className="h-4 w-4" />
              </button>
              <button
                onClick={() =>
                  navigate({
                    to: '/admin/integrations/$id',
                    params: { id: String(row.original.id) },
                  })
                }
                aria-label={row.original.name}
                className="inline-flex items-center justify-center text-[var(--color-accent)] transition-opacity hover:opacity-70"
              >
                <Eye className="h-4 w-4" />
              </button>
            </div>
          </InlineDeleteConfirm>
        )
      },
    }),
  ]

  // eslint-disable-next-line react-hooks/incompatible-library -- useReactTable returns functions that React Compiler cannot memoize safely; this is expected TanStack Table behaviour
  const table = useReactTable({
    data: integrations,
    columns,
    getCoreRowModel: getCoreRowModel(),
  })

  return (
    <div className="p-6 space-y-6 max-w-5xl">
      <div className="flex items-start justify-between">
        <h1 className="page-title text-xl/none font-semibold text-[var(--color-foreground)]">
          {m.admin_integrations_title()}
        </h1>
        <Button
          size="sm"
          onClick={() => void navigate({ to: '/admin/integrations/new' })}
        >
          <Plus className="h-4 w-4 mr-2" />
          {m.admin_integrations_create()}
        </Button>
      </div>

      {error ? (
        <QueryErrorState
          error={error instanceof Error ? error : new Error(String(error))}
          onRetry={() => void refetch()}
        />
      ) : isLoading ? (
        <p className="py-8 text-sm text-[var(--color-muted-foreground)]">
          <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
          {m.admin_integrations_loading()}
        </p>
      ) : integrations.length === 0 ? (
        <div className="py-12 text-center space-y-3">
          <p className="text-sm font-medium text-[var(--color-foreground)]">
            {m.admin_integrations_empty()}
          </p>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {m.admin_integrations_empty_description()}
          </p>
        </div>
      ) : (
        <table className="w-full text-sm border-t border-b border-[var(--color-border)]">
          <thead>
            {table.getHeaderGroups().map((headerGroup) => (
              <tr
                key={headerGroup.id}
                className="border-b border-[var(--color-border)]"
              >
                {headerGroup.headers.map((header) => (
                  <th
                    key={header.id}
                    className="py-3 pr-4 text-left text-xs font-medium text-[var(--color-rl-dark-30)] uppercase tracking-[0.04em]"
                  >
                    {flexRender(
                      header.column.columnDef.header,
                      header.getContext(),
                    )}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => (
              <tr
                key={row.id}
                className="border-b border-[var(--color-border)] last:border-b-0"
              >
                {row.getVisibleCells().map((cell) => (
                  <td
                    key={cell.id}
                    className="py-4 pr-4 align-top text-[var(--color-foreground)]"
                  >
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
