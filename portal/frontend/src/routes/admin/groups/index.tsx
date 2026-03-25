import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  createColumnHelper,
} from '@tanstack/react-table'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Loader2, Eye, Lock, Plus } from 'lucide-react'
import { toast } from 'sonner'
import * as m from '@/paraglide/messages'
import { API_BASE } from '@/lib/api'

export const Route = createFileRoute('/admin/groups/')({
  component: AdminGroups,
})

interface Group {
  id: number
  name: string
  products: string[]
  is_system: boolean
}

interface OrgUser {
  zitadel_user_id: string
  email: string
  first_name: string
  last_name: string
}

function userInitials(user: OrgUser): string {
  if (user.first_name && user.last_name) {
    return `${user.first_name[0]}${user.last_name[0]}`.toUpperCase()
  }
  return user.email.slice(0, 2).toUpperCase()
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
    return <span className="text-xs text-[var(--color-muted-foreground)]">—</span>
  }
  return (
    <div className="flex items-center gap-1.5">
      {visible.map((uid) => {
        const user = usersMap.get(uid)
        const label = user ? userInitials(user) : '??'
        const title = user
          ? `${user.first_name} ${user.last_name}`.trim() || user.email
          : uid
        return (
          <div
            key={uid}
            title={title}
            className="h-7 w-7 rounded-full flex items-center justify-center text-xs font-medium bg-[var(--color-secondary)] text-[var(--color-purple-deep)]"
          >
            {label}
          </div>
        )
      })}
      {extra > 0 && (
        <div className="h-7 w-7 rounded-full flex items-center justify-center text-xs font-medium bg-[var(--color-muted)] text-[var(--color-muted-foreground)]">
          +{extra}
        </div>
      )}
    </div>
  )
}

const columnHelper = createColumnHelper<Group>()

function AdminGroups() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [showCreate, setShowCreate] = useState(false)
  const [newGroupName, setNewGroupName] = useState('')

  const { data, isLoading, error } = useQuery({
    queryKey: ['admin-groups'],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/admin/groups`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(`Failed to fetch groups (${res.status})`)
      return res.json() as Promise<{ groups: Group[] }>
    },
    enabled: !!token,
  })

  const { data: usersData } = useQuery({
    queryKey: ['admin-users'],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/admin/users`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(`Failed to fetch users (${res.status})`)
      return res.json() as Promise<{ users: OrgUser[] }>
    },
    enabled: !!token,
  })

  const { data: membershipsData } = useQuery({
    queryKey: ['admin-group-memberships'],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/admin/group-memberships`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(`Failed to fetch memberships (${res.status})`)
      return res.json() as Promise<{ memberships: Record<string, { id: number }[]> }>
    },
    enabled: !!token,
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

  const createMutation = useMutation({
    mutationFn: async (name: string) => {
      const res = await fetch(`${API_BASE}/api/admin/groups`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ name }),
      })
      if (!res.ok) throw new Error(`Failed to create group (${res.status})`)
      return res.json()
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-groups'] })
      setShowCreate(false)
      setNewGroupName('')
      toast.success(m.admin_groups_success_created())
    },
    onError: (err: Error) => {
      toast.error(err.message)
    },
  })

  const columns = [
    columnHelper.accessor('name', {
      header: () => m.admin_groups_name(),
      cell: (info) => (
        <span className="font-medium text-[var(--color-purple-deep)] flex items-center gap-2">
          {info.row.original.is_system && (
            <Lock className="h-3 w-3 text-[var(--color-muted-foreground)]" />
          )}
          {info.getValue()}
        </span>
      ),
    }),
    columnHelper.accessor('products', {
      header: () => 'Product',
      cell: (info) => (
        <div className="flex gap-1">
          {info.getValue().map((p) => (
            <Badge key={p} variant="secondary" className="text-xs capitalize">
              {p}
            </Badge>
          ))}
        </div>
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
              <span className="text-xs text-[var(--color-muted-foreground)]">
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
      cell: ({ row }) => (
        <Button
          variant="ghost"
          size="sm"
          onClick={() =>
            navigate({
              to: '/admin/groups/$groupId',
              params: { groupId: String(row.original.id) },
            })
          }
          aria-label={row.original.name}
        >
          <Eye className="h-4 w-4" />
        </Button>
      ),
    }),
  ]

  const table = useReactTable({
    data: groups,
    columns,
    getCoreRowModel: getCoreRowModel(),
  })

  return (
    <div className="p-8 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
          {m.admin_groups_title()}
        </h1>
        <Button size="sm" onClick={() => setShowCreate(true)}>
          <Plus className="h-4 w-4 mr-2" />
          {m.admin_groups_create()}
        </Button>
      </div>

      {showCreate && (
        <Card>
          <CardContent className="pt-6">
            <div className="flex gap-2 items-end">
              <div className="flex-1 space-y-1">
                <Label htmlFor="new-group-name">{m.admin_groups_name()}</Label>
                <Input
                  id="new-group-name"
                  value={newGroupName}
                  onChange={(e) => setNewGroupName(e.target.value)}
                  placeholder="Team naam..."
                />
              </div>
              <Button
                onClick={() => createMutation.mutate(newGroupName)}
                disabled={!newGroupName.trim() || createMutation.isPending}
              >
                {createMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  m.admin_groups_create()
                )}
              </Button>
              <Button
                variant="ghost"
                onClick={() => {
                  setShowCreate(false)
                  setNewGroupName('')
                }}
              >
                {m.admin_users_cancel()}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {error && (
        <p className="text-sm text-[var(--color-destructive)]">
          {error instanceof Error ? error.message : String(error)}
        </p>
      )}

      <Card>
        <CardContent className="pt-0 px-0 pb-0 overflow-hidden rounded-xl">
          {isLoading ? (
            <p className="px-6 py-8 text-sm text-[var(--color-muted-foreground)]">
              <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
              Loading...
            </p>
          ) : groups.length === 0 ? (
            <div className="px-6 py-12 text-center space-y-3">
              <p className="text-sm font-medium text-[var(--color-purple-deep)]">
                {m.admin_groups_empty()}
              </p>
              <p className="text-sm text-[var(--color-muted-foreground)]">
                {m.admin_groups_empty_description()}
              </p>
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                {table.getHeaderGroups().map((headerGroup) => (
                  <tr
                    key={headerGroup.id}
                    className="border-b border-[var(--color-border)]"
                  >
                    {headerGroup.headers.map((header) => (
                      <th
                        key={header.id}
                        className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide"
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
                {table.getRowModel().rows.map((row, i) => (
                  <tr
                    key={row.id}
                    className={
                      i % 2 === 0
                        ? 'bg-[var(--color-card)]'
                        : 'bg-[var(--color-secondary)]'
                    }
                  >
                    {row.getVisibleCells().map((cell) => (
                      <td
                        key={cell.id}
                        className="px-6 py-3 text-[var(--color-purple-deep)]"
                      >
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
  )
}
