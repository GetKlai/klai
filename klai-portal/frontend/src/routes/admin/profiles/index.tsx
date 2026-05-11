import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery } from '@tanstack/react-query'
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  createColumnHelper,
} from '@tanstack/react-table'
import { Eye } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { QueryErrorState } from '@/components/ui/query-error-state'
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
      header: () => '',
      cell: ({ row }) => (
        <div className="flex items-start justify-end gap-2 mt-px">
          <button
            onClick={() =>
              navigate({
                to: '/admin/profiles/$profile',
                params: { profile: row.original.role },
              })
            }
            aria-label={row.original.label}
            className="inline-flex items-center justify-center text-[var(--color-accent)] transition-opacity hover:opacity-70"
          >
            <Eye className="h-4 w-4" />
          </button>
        </div>
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
    <div className="mx-auto max-w-4xl px-6 py-10 space-y-6">
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
        <p className="py-8 text-sm text-gray-400">
          {m.admin_profiles_loading()}
        </p>
      ) : (
        <table className="w-full text-sm border-t border-b border-gray-200">
          <thead>
            {table.getHeaderGroups().map((headerGroup) => (
              <tr
                key={headerGroup.id}
                className="border-b border-gray-200"
              >
                {headerGroup.headers.map((header) => (
                  <th
                    key={header.id}
                    className="py-3 pr-4 text-left text-xs font-medium text-gray-400 tracking-wide"
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
                className="border-b border-gray-200 last:border-b-0"
              >
                {row.getVisibleCells().map((cell) => (
                  <td
                    key={cell.id}
                    className="py-4 pr-4 align-top text-gray-900"
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
