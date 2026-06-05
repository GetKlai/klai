import { createFileRoute, useNavigate } from '@tanstack/react-router'
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  createColumnHelper,
} from '@tanstack/react-table'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
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
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import {
  BorderedRowActionIconButton,
  RowActionGroup,
} from '@/components/ui/row-action'
import { QueryErrorState } from '@/components/ui/query-error-state'
import * as m from '@/paraglide/messages'
import { getLocale } from '@/paraglide/runtime'
import { datetime, plural } from '@/paraglide/registry'
import { useApiKeys, useDeleteApiKey } from './-hooks'
import type { ApiKeyResponse } from './-types'

export const Route = createFileRoute('/admin/api-keys/')({
  component: ApiKeysPage,
})

function formatRelativeTime(isoString: string | null): string {
  if (!isoString) return '—'
  return datetime(getLocale(), isoString, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function apiKeyCountLabel(count: number): string {
  return plural(getLocale(), count) === 'one'
    ? m.admin_api_keys_count_one()
    : m.admin_api_keys_count_other({ count: String(count) })
}

const columnHelper = createColumnHelper<ApiKeyResponse>()

function ApiKeysPage() {
  const navigate = useNavigate()
  const { data, isLoading, error, refetch } = useApiKeys()
  const deleteMutation = useDeleteApiKey()
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)

  const apiKeys = Array.isArray(data) ? data : []

  const openKey = (id: ApiKeyResponse['id']) =>
    void navigate({ to: '/admin/api-keys/$id', params: { id: String(id) } })

  const columns = [
    columnHelper.accessor('name', {
      header: () => m.admin_api_keys_col_name(),
      cell: (info) => (
        <span className="font-medium text-gray-900">{info.getValue()}</span>
      ),
    }),
    columnHelper.accessor('key_prefix', {
      header: () => m.admin_api_keys_col_key_prefix(),
      cell: (info) => (
        <code className="font-mono text-xs text-gray-400">
          {info.getValue()}...
        </code>
      ),
    }),
    columnHelper.accessor('kb_access_count', {
      header: () => m.admin_api_keys_col_kb_access(),
      cell: (info) => (
        <span className="tabular-nums text-gray-900">{info.getValue()}</span>
      ),
    }),
    columnHelper.accessor('last_used_at', {
      header: () => m.admin_api_keys_col_last_used(),
      cell: (info) => (
        <span className="whitespace-nowrap tabular-nums text-gray-400">
          {formatRelativeTime(info.getValue())}
        </span>
      ),
    }),
    columnHelper.display({
      id: 'actions',
      header: () => '',
      cell: ({ row }) => {
        const id = String(row.original.id)
        return (
          <InlineDeleteConfirm
            isConfirming={confirmDeleteId === id}
            isPending={deleteMutation.isPending}
            label={m.admin_api_keys_delete_confirm({ name: row.original.name })}
            cancelLabel={m.admin_users_cancel()}
            onConfirm={() => {
              deleteMutation.mutate(id)
              setConfirmDeleteId(null)
            }}
            onCancel={() => setConfirmDeleteId(null)}
          >
            <RowActionGroup>
              <BorderedRowActionIconButton
                label={m.admin_api_keys_view()}
                action="view"
                onClick={() => openKey(row.original.id)}
              />
              <BorderedRowActionIconButton
                label={m.admin_api_keys_delete()}
                action="delete"
                onClick={() => setConfirmDeleteId(id)}
              />
            </RowActionGroup>
          </InlineDeleteConfirm>
        )
      },
    }),
  ]

  // eslint-disable-next-line react-hooks/incompatible-library -- useReactTable returns functions that React Compiler cannot memoize safely; this is expected TanStack Table behaviour
  const table = useReactTable({
    data: apiKeys,
    columns,
    getCoreRowModel: getCoreRowModel(),
  })

  return (
    <div className="mx-auto max-w-3xl px-6 pt-4 pb-10 space-y-6">
      <PageHeader
        title={m.admin_api_keys_title()}
        description={
          !isLoading && !error ? apiKeyCountLabel(apiKeys.length) : undefined
        }
        actions={
          <Button
            size="sm"
            onClick={() => void navigate({ to: '/admin/api-keys/new' })}
          >
            {m.admin_api_keys_create()}
          </Button>
        }
      />

      {error ? (
        <QueryErrorState
          error={error instanceof Error ? error : new Error(String(error))}
          onRetry={() => void refetch()}
        />
      ) : isLoading ? (
        <ListLoadingState label={m.admin_api_keys_loading()} />
      ) : apiKeys.length === 0 ? (
        <ListEmptyState
          title={m.admin_api_keys_empty()}
          description={m.admin_api_keys_empty_description()}
        />
      ) : (
        <DataTable>
          <DataTableHeader>
            {table.getHeaderGroups().map((headerGroup) => (
              <DataTableRow key={headerGroup.id}>
                {headerGroup.headers.map((header) => (
                  <DataTableHead
                    key={header.id}
                    align={header.column.id === 'actions' ? 'right' : 'left'}
                  >
                    {flexRender(
                      header.column.columnDef.header,
                      header.getContext(),
                    )}
                  </DataTableHead>
                ))}
              </DataTableRow>
            ))}
          </DataTableHeader>
          <DataTableBody>
            {table.getRowModel().rows.map((row) => (
              <DataTableRow
                key={row.id}
                interactive
                confirming={confirmDeleteId === String(row.original.id)}
                onClick={() => openKey(row.original.id)}
              >
                {row.getVisibleCells().map((cell) => {
                  const isActionCell = cell.column.id === 'actions'
                  return (
                    <DataTableCell
                      key={cell.id}
                      align={isActionCell ? 'right' : 'left'}
                      onClick={
                        isActionCell ? (e) => e.stopPropagation() : undefined
                      }
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </DataTableCell>
                  )
                })}
              </DataTableRow>
            ))}
          </DataTableBody>
        </DataTable>
      )}
    </div>
  )
}
