import { createFileRoute } from '@tanstack/react-router'
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
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import { Loader2, Lock, Plus, Trash2, UserPlus, Users } from 'lucide-react'
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

interface Member {
  zitadel_user_id: string
  is_group_admin: boolean
  joined_at: string
}

function userDisplayName(user: OrgUser | undefined, fallback: string): string {
  if (!user) return fallback
  const full = `${user.first_name} ${user.last_name}`.trim()
  return full || user.email
}

function userInitials(user: OrgUser): string {
  if (user.first_name && user.last_name) {
    return `${user.first_name[0]}${user.last_name[0]}`.toUpperCase()
  }
  return user.email.slice(0, 2).toUpperCase()
}

const AVATAR_COLORS = [
  'bg-purple-100 text-purple-700',
  'bg-blue-100 text-blue-700',
  'bg-green-100 text-green-700',
  'bg-amber-100 text-amber-700',
  'bg-rose-100 text-rose-700',
]

function avatarColor(id: string): string {
  const hash = id.split('').reduce((acc, c) => acc + c.charCodeAt(0), 0)
  return AVATAR_COLORS[hash % AVATAR_COLORS.length]
}

// ---------------------------------------------------------------------------
// MemberAvatars
// ---------------------------------------------------------------------------

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
    <div className="flex items-center gap-1">
      {visible.map((uid) => {
        const user = usersMap.get(uid)
        return (
          <div
            key={uid}
            title={user ? userDisplayName(user, uid) : uid}
            className={`h-7 w-7 rounded-full flex items-center justify-center text-xs font-medium ${avatarColor(uid)}`}
          >
            {user ? userInitials(user) : '??'}
          </div>
        )
      })}
      {extra > 0 && (
        <div className="h-7 w-7 rounded-full flex items-center justify-center text-xs font-medium bg-gray-100 text-gray-600">
          +{extra}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// GroupSheet
// ---------------------------------------------------------------------------

function GroupSheet({
  group,
  usersMap,
  token,
  onClose,
}: {
  group: Group
  usersMap: Map<string, OrgUser>
  token: string
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [comboboxOpen, setComboboxOpen] = useState(false)
  const [selectedUserId, setSelectedUserId] = useState<string | null>(null)
  const groupId = String(group.id)

  const { data: membersData, isLoading: membersLoading } = useQuery({
    queryKey: ['admin-group-members', groupId],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/admin/groups/${groupId}/members`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(`Failed to fetch members (${res.status})`)
      return res.json() as Promise<{ members: Member[] }>
    },
  })

  const members = membersData?.members ?? []
  const memberIds = new Set(members.map((mb) => mb.zitadel_user_id))
  const allUsers = Array.from(usersMap.values())
  const availableUsers = allUsers.filter((u) => !memberIds.has(u.zitadel_user_id))
  const selectedUser = selectedUserId ? usersMap.get(selectedUserId) : null

  const removeMutation = useMutation({
    mutationFn: async (userId: string) => {
      const res = await fetch(
        `${API_BASE}/api/admin/groups/${groupId}/members/${userId}`,
        { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } },
      )
      if (!res.ok) throw new Error(`Failed to remove member (${res.status})`)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-group-members', groupId] })
      void queryClient.invalidateQueries({ queryKey: ['admin-group-memberships'] })
      toast.success(m.admin_groups_members_success_removed())
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const addMutation = useMutation({
    mutationFn: async (userId: string) => {
      const res = await fetch(`${API_BASE}/api/admin/groups/${groupId}/members`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ zitadel_user_id: userId }),
      })
      if (res.status === 409) throw new Error('already_member')
      if (!res.ok) throw new Error(`Failed to add member (${res.status})`)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-group-members', groupId] })
      void queryClient.invalidateQueries({ queryKey: ['admin-group-memberships'] })
      setSelectedUserId(null)
      setComboboxOpen(false)
      toast.success(m.admin_groups_members_success_added())
    },
    onError: (err: Error) => {
      if (err.message === 'already_member') {
        toast.error(m.admin_groups_members_error_already_member())
      } else {
        toast.error(err.message)
      }
    },
  })

  return (
    <Sheet open onOpenChange={(open) => { if (!open) onClose() }}>
      <SheetContent className="sm:max-w-lg overflow-y-auto flex flex-col gap-0">
        <SheetHeader className="mb-4">
          <SheetTitle className="font-serif text-xl flex items-center gap-2 text-[var(--color-purple-deep)]">
            {group.is_system && <Lock className="h-4 w-4 text-[var(--color-muted-foreground)]" />}
            {group.name}
          </SheetTitle>
          {group.products.length > 0 && (
            <div className="flex gap-2 flex-wrap mt-1">
              {group.products.map((p) => (
                <Badge key={p} variant="secondary" className="capitalize">{p}</Badge>
              ))}
            </div>
          )}
        </SheetHeader>

        {/* Members list */}
        <div className="space-y-2">
          <h3 className="text-xs font-semibold text-[var(--color-muted-foreground)] uppercase tracking-wide">
            {m.admin_groups_members_title()}
          </h3>

          {membersLoading ? (
            <p className="text-sm text-[var(--color-muted-foreground)] py-2">
              <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
              Loading...
            </p>
          ) : members.length === 0 ? (
            <p className="text-sm text-[var(--color-muted-foreground)] py-2">
              {m.admin_groups_members_empty()}
            </p>
          ) : (
            <div className="space-y-0.5">
              {members.map((member) => {
                const user = usersMap.get(member.zitadel_user_id)
                const isRemoving =
                  removeMutation.isPending &&
                  removeMutation.variables === member.zitadel_user_id
                return (
                  <div
                    key={member.zitadel_user_id}
                    className="flex items-center justify-between py-2 px-2 rounded-lg hover:bg-[var(--color-secondary)]"
                  >
                    <div className="flex items-center gap-3">
                      <div
                        className={`h-8 w-8 rounded-full flex items-center justify-center text-xs font-medium ${avatarColor(member.zitadel_user_id)}`}
                      >
                        {user ? userInitials(user) : '??'}
                      </div>
                      <div>
                        <div className="text-sm font-medium text-[var(--color-purple-deep)]">
                          {userDisplayName(user, member.zitadel_user_id)}
                        </div>
                        {user && (
                          <div className="text-xs text-[var(--color-muted-foreground)]">
                            {user.email}
                          </div>
                        )}
                      </div>
                    </div>
                    <AlertDialog>
                      <AlertDialogTrigger asChild>
                        <Button variant="ghost" size="sm" disabled={isRemoving}>
                          {isRemoving ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Trash2 className="h-4 w-4 text-[var(--color-destructive)]" />
                          )}
                        </Button>
                      </AlertDialogTrigger>
                      <AlertDialogContent>
                        <AlertDialogHeader>
                          <AlertDialogTitle>
                            {m.admin_groups_members_remove()}
                          </AlertDialogTitle>
                          <AlertDialogDescription>
                            {userDisplayName(user, member.zitadel_user_id)}
                          </AlertDialogDescription>
                        </AlertDialogHeader>
                        <AlertDialogFooter>
                          <AlertDialogCancel>{m.admin_users_cancel()}</AlertDialogCancel>
                          <AlertDialogAction
                            onClick={() => removeMutation.mutate(member.zitadel_user_id)}
                          >
                            {m.admin_groups_members_remove()}
                          </AlertDialogAction>
                        </AlertDialogFooter>
                      </AlertDialogContent>
                    </AlertDialog>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* Add member */}
        <div className="mt-6 border-t border-[var(--color-border)] pt-4 space-y-2">
          <h3 className="text-xs font-semibold text-[var(--color-muted-foreground)] uppercase tracking-wide">
            {m.admin_groups_members_add()}
          </h3>
          <div className="flex gap-2">
            <Popover open={comboboxOpen} onOpenChange={setComboboxOpen}>
              <PopoverTrigger asChild>
                <Button
                  variant="outline"
                  role="combobox"
                  aria-expanded={comboboxOpen}
                  className="flex-1 justify-between font-normal text-left"
                >
                  <span className="truncate">
                    {selectedUser
                      ? `${selectedUser.first_name} ${selectedUser.last_name}`.trim() ||
                        selectedUser.email
                      : m.admin_groups_members_search_placeholder()}
                  </span>
                  <span className="ml-2 opacity-50 shrink-0">&#x25BE;</span>
                </Button>
              </PopoverTrigger>
              <PopoverContent
                className="w-[var(--radix-popover-trigger-width)] p-0"
                align="start"
              >
                <Command>
                  <CommandInput
                    placeholder={m.admin_groups_members_search_placeholder()}
                  />
                  <CommandList>
                    <CommandEmpty>No users found</CommandEmpty>
                    <CommandGroup>
                      {availableUsers.map((u) => (
                        <CommandItem
                          key={u.zitadel_user_id}
                          value={`${u.first_name} ${u.last_name} ${u.email}`}
                          onSelect={() => {
                            setSelectedUserId(u.zitadel_user_id)
                            setComboboxOpen(false)
                          }}
                        >
                          <span>
                            {`${u.first_name} ${u.last_name}`.trim() || u.email}
                          </span>
                          <span className="ml-auto text-xs text-[var(--color-muted-foreground)]">
                            {u.email}
                          </span>
                        </CommandItem>
                      ))}
                    </CommandGroup>
                  </CommandList>
                </Command>
              </PopoverContent>
            </Popover>
            <Button
              disabled={!selectedUserId || addMutation.isPending}
              onClick={() => selectedUserId && addMutation.mutate(selectedUserId)}
            >
              {addMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <UserPlus className="h-4 w-4" />
              )}
            </Button>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const columnHelper = createColumnHelper<Group>()

function AdminGroups() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const [showCreate, setShowCreate] = useState(false)
  const [newGroupName, setNewGroupName] = useState('')
  const [openGroupId, setOpenGroupId] = useState<number | null>(null)

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

  const openGroup = openGroupId !== null
    ? groups.find((g) => g.id === openGroupId) ?? null
    : null

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
          onClick={(e) => {
            e.stopPropagation()
            setOpenGroupId(row.original.id)
          }}
          aria-label={row.original.name}
        >
          <Users className="h-4 w-4" />
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
                    className={`cursor-pointer transition-colors hover:bg-[var(--color-secondary)] ${
                      i % 2 === 0
                        ? 'bg-[var(--color-card)]'
                        : 'bg-[var(--color-secondary)]'
                    }`}
                    onClick={() => setOpenGroupId(row.original.id)}
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

      {openGroup && token && (
        <GroupSheet
          group={openGroup}
          usersMap={usersMap}
          token={token}
          onClose={() => setOpenGroupId(null)}
        />
      )}
    </div>
  )
}
