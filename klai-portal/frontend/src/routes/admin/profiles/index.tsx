import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery } from '@tanstack/react-query'
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  createColumnHelper,
} from '@tanstack/react-table'
import * as m from '@/paraglide/messages'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { BorderedRowActionIconButton, RowActionGroup } from '@/components/ui/row-action'
import {
  DataTable,
  DataTableBody,
  DataTableCell,
  DataTableHead,
  DataTableHeader,
  DataTableRow,
} from '@/components/ui/data-table'
import { ListLoadingState } from '@/components/ui/list-state'
import { apiFetch } from '@/lib/apiFetch'
import { PROFILE_LADDER, type ProfileRole } from '@/lib/profiles'

export const Route = createFileRoute('/admin/profiles/')({
  component: AdminProfiles,
})

interface AdminUser {
  zitadel_user_id: string
  role: ProfileRole
}

interface ProfileRow {
  role: ProfileRole
  label: string
  description: string
  count: number
}

const columnHelper = createColumnHelper<ProfileRow>()

function AdminProfiles() {
  const auth = useAuth()
  const navigate = useNavigate({ from: '/admin/profiles/' })

  const { data: usersData, isLoading, error, refetch } = useQuery({
    queryKey: ['admin-users'],
    queryFn: async () => apiFetch<{ users: AdminUser[] }>('/api/admin/users'),
    enabled: auth.isAuthenticated,
  })

  const msgs = m as unknown as Record<string, (() => string) | undefined>
  const users = usersData?.users ?? []

  const rows: ProfileRow[] = PROFILE_LADDER.map((role) => {
    const labelFn = msgs[`profile_${role}_label`]
    const descFn = msgs[`profile_${role}_description`]
    return {
      role,
      label: labelFn ? labelFn() : role,
      description: descFn ? descFn() : '',
      count: users.filter((u) => u.role === role).length,
    }
  })

  const columns = [
    columnHelper.accessor('label', {
      header: () => m.admin_groups_name(),
      cell: (info) => (
        <div className="space-y-0.5">
          <div className="font-medium text-gray-900">
            {info.getValue()}
          </div>
          <div className="text-xs text-gray-400">
            {info.row.original.description}
          </div>
        </div>
      ),
    }),
    columnHelper.accessor('count', {
      header: () => m.admin_groups_members_title(),
      cell: (info) => (
        <span className="text-gray-900 tabular-nums">
          {info.getValue()}
        </span>
      ),
    }),
    columnHelper.display({
      id: 'actions',
      header: () => m.admin_users_col_actions(),
      cell: ({ row }) => (
        <RowActionGroup>
          <BorderedRowActionIconButton
            label={m.admin_profiles_view_members()}
            action="view"
            onClick={() =>
              navigate({
                to: '/admin/profiles/$profile',
                params: { profile: row.original.role },
              })
            }
          />
        </RowActionGroup>
      ),
    }),
  ]

  // eslint-disable-next-line react-hooks/incompatible-library -- useReactTable returns functions that React Compiler cannot memoize safely; this is expected TanStack Table behaviour
  const table = useReactTable({
    data: rows,
    columns,
    getCoreRowModel: getCoreRowModel(),
  })

  return (
    <div className="mx-auto max-w-3xl px-6 pt-4 pb-10 space-y-6">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h1 className="page-title text-[26px] font-display-bold text-gray-900">
            {m.admin_profiles_title()}
          </h1>
          <p className="text-sm text-gray-400">
            {m.admin_profiles_subtitle()}
          </p>
        </div>
      </div>

      {error ? (
        <QueryErrorState
          error={error instanceof Error ? error : new Error(String(error))}
          onRetry={() => void refetch()}
        />
      ) : isLoading ? (
        <ListLoadingState label={m.admin_profiles_loading()} />
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
                    {flexRender(header.column.columnDef.header, header.getContext())}
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
                onClick={() =>
                  void navigate({
                    to: '/admin/profiles/$profile',
                    params: { profile: row.original.role },
                  })
                }
              >
                {row.getVisibleCells().map((cell) => {
                  const isActionCell = cell.column.id === 'actions'
                  return (
                    <DataTableCell
                      key={cell.id}
                      align={isActionCell ? 'right' : 'left'}
                      onClick={isActionCell ? (e) => e.stopPropagation() : undefined}
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
