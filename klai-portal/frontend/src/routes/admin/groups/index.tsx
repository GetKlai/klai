import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  createColumnHelper,
} from '@tanstack/react-table'
import { Button } from '@/components/ui/button'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import { ListEmptyState, ListLoadingState } from '@/components/ui/list-state'
import { RowActionGroup, RowActionIconButton } from '@/components/ui/row-action'
import { Plus } from 'lucide-react'
import { toast } from 'sonner'
import * as m from '@/paraglide/messages'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { apiFetch } from '@/lib/apiFetch'
import { UserAvatar } from '@/routes/admin/_components/UserAvatar'

export const Route = createFileRoute('/admin/groups/')({
  component: AdminGroups,
})

interface Group {
  id: number
  name: string
  is_system: boolean
}

interface OrgUser {
  zitadel_user_id: string
  email: string
  first_name: string
  last_name: string
}

function MemberAvatars({
  userIds,
  usersMap,
}: {
  userIds: string[]
  usersMap: Map<string, OrgUser>
}) {
  const visible = userIds.slice(0, 4)
  const extra = userIds.length - visible.length
  if (userIds.length === 0) {
    return <span className="text-xs text-gray-400">-</span>
  }
  return (
    <div className="flex items-center gap-1.5">
      {visible.map((uid) => {
        const user = usersMap.get(uid)
        return (
          user ? (
            <UserAvatar
              key={uid}
              uid={uid}
              first_name={user.first_name}
              last_name={user.last_name}
              email={user.email}
              size="sm"
            />
          ) : (
            <div
              key={uid}
              title={uid}
              className="flex h-7 w-7 items-center justify-center rounded-full bg-[var(--color-muted)] text-xs font-medium text-gray-400"
            >
              ??
            </div>
          )
        )
      })}
      {extra > 0 && (
        <div className="h-7 w-7 rounded-full flex items-center justify-center text-xs font-medium bg-[var(--color-muted)] text-gray-400">
          +{extra}
        </div>
      )}
    </div>
  )
}

const columnHelper = createColumnHelper<Group>()

function AdminGroups() {
  const auth = useAuth()
  const navigate = useNavigate({ from: '/admin/groups/' })
  const queryClient = useQueryClient()
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['admin-groups'],
    queryFn: async () => apiFetch<{ groups: Group[] }>('/api/admin/groups'),
    enabled: auth.isAuthenticated,
  })

  const { data: usersData } = useQuery({
    queryKey: ['admin-users'],
    queryFn: async () => apiFetch<{ users: OrgUser[] }>('/api/admin/users'),
    enabled: auth.isAuthenticated,
  })

  const { data: membershipsData } = useQuery({
    queryKey: ['admin-group-memberships'],
    queryFn: async () => apiFetch<{ memberships: Record<string, { id: number }[]> }>('/api/admin/group-memberships'),
    enabled: auth.isAuthenticated,
  })

  const groups = data?.groups ?? []
  const usersMap = new Map(
    (usersData?.users ?? []).map((u) => [u.zitadel_user_id, u]),
  )

  // Invert memberships: userId -> [group] to groupId -> [userId]
  const groupMembersMap = new Map<number, string[]>()
  if (membershipsData?.memberships) {
    for (const [userId, groupList] of Object.entries(membershipsData.memberships)) {
      for (const g of groupList) {
        if (!groupMembersMap.has(g.id)) groupMembersMap.set(g.id, [])
        groupMembersMap.get(g.id)!.push(userId)
      }
    }
  }

  const deleteMutation = useMutation({
    mutationFn: async (groupId: number) => {
      return apiFetch(`/api/admin/groups/${groupId}`, {
        method: 'DELETE',
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-groups'] })
      void queryClient.invalidateQueries({ queryKey: ['admin-group-memberships'] })
      toast.success(m.admin_groups_success_deleted())
    },
    onError: (err: Error) => {
      toast.error(err.message)
    },
  })

  const columns = [
    columnHelper.accessor('name', {
      header: () => m.admin_groups_name(),
      cell: (info) => (
        <span className="font-medium text-gray-900">
          {info.getValue()}
        </span>
      ),
    }),
    columnHelper.display({
      id: 'members',
      header: () => m.admin_groups_members_title(),
      cell: ({ row }) => {
        const memberIds = groupMembersMap.get(row.original.id) ?? []
        return (
          <div className="flex items-center gap-2">
            <MemberAvatars userIds={memberIds} usersMap={usersMap} />
            {memberIds.length > 0 && (
              <span className="text-xs text-gray-400">
                {memberIds.length}
              </span>
            )}
          </div>
        )
      },
    }),
    columnHelper.display({
      id: 'actions',
      header: () => '',
      cell: ({ row }) => {
        const isConfirming = confirmDeleteId === row.original.id
        return (
          <InlineDeleteConfirm
            isConfirming={isConfirming}
            isPending={deleteMutation.isPending}
            label={m.admin_groups_delete_confirm({ name: row.original.name })}
            cancelLabel={m.admin_users_cancel()}
            onConfirm={() => { deleteMutation.mutate(row.original.id); setConfirmDeleteId(null) }}
            onCancel={() => setConfirmDeleteId(null)}
          >
            <RowActionGroup className="mt-px items-start">
              <RowActionIconButton
                label={m.admin_groups_delete()}
                action="delete"
                onClick={() => setConfirmDeleteId(row.original.id)}
              />
              <RowActionIconButton
                label={m.admin_groups_edit()}
                action="edit"
                onClick={() =>
                  navigate({
                    to: '/admin/groups/$groupId/edit',
                    params: { groupId: String(row.original.id) },
                  })
                }
              />
              <RowActionIconButton
                label={row.original.name}
                action="view"
                onClick={() =>
                  navigate({
                    to: '/admin/groups/$groupId',
                    params: { groupId: String(row.original.id) },
                  })
                }
              />
            </RowActionGroup>
          </InlineDeleteConfirm>
        )
      },
    }),
  ]

  // eslint-disable-next-line react-hooks/incompatible-library -- useReactTable returns functions that React Compiler cannot memoize safely; this is expected TanStack Table behaviour
  const table = useReactTable({
    data: groups,
    columns,
    getCoreRowModel: getCoreRowModel(),
  })

  return (
    <div className="mx-auto max-w-3xl px-6 pt-6 pb-10 space-y-6">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h1 className="page-title text-[26px] font-display-bold text-gray-900">
            {m.admin_groups_title()}
          </h1>
          <p className="text-sm text-gray-400">
            {m.admin_groups_subtitle()}
          </p>
        </div>
        <Button size="sm" onClick={() => void navigate({ to: '/admin/groups/new' })}>
          <Plus className="h-4 w-4 mr-2" />
          {m.admin_groups_create()}
        </Button>
      </div>

      {error ? (
        <QueryErrorState error={error instanceof Error ? error : new Error(String(error))} onRetry={() => void refetch()} />
      ) : isLoading ? (
        <ListLoadingState label={m.admin_shared_loading()} />
      ) : groups.length === 0 ? (
        <ListEmptyState title={m.admin_groups_empty()} />
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
                onClick={() =>
                  void navigate({
                    to: '/admin/groups/$groupId',
                    params: { groupId: String(row.original.id) },
                  })
                }
                className={`border-b border-gray-200 last:border-b-0 cursor-pointer klai-hover ${
                  confirmDeleteId === row.original.id ? 'bg-[var(--color-hover)]' : ''
                }`}
              >
                {row.getVisibleCells().map((cell) => {
                  const isActionCell = cell.column.id === 'actions'
                  return (
                    <td
                      key={cell.id}
                      className="py-4 pr-4 align-top text-gray-900"
                      onClick={
                        isActionCell ? (e) => e.stopPropagation() : undefined
                      }
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
