import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Switch } from '@/components/ui/switch'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from '@/components/ui/dialog'
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
import { ArrowLeft, Loader2, Trash2, UserPlus } from 'lucide-react'
import { toast } from 'sonner'
import * as m from '@/paraglide/messages'
import { getLocale } from '@/paraglide/runtime'
import { datetime } from '@/paraglide/registry'
import { API_BASE } from '@/lib/api'

export const Route = createFileRoute('/admin/groups/$groupId/')({
  component: AdminGroupDetail,
})

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Group {
  id: number
  name: string
  description: string | null
  created_at: string
  created_by: string
}

interface Member {
  zitadel_user_id: string
  is_group_admin: boolean
  joined_at: string
}

interface OrgUser {
  zitadel_user_id: string
  email: string
  first_name: string
  last_name: string
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDate(isoString: string): string {
  return datetime(getLocale(), isoString, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

function displayName(user: OrgUser | undefined, member: Member): string {
  if (user) {
    const full = `${user.first_name} ${user.last_name}`.trim()
    return full || user.email
  }
  return member.zitadel_user_id
}

function displayEmail(user: OrgUser | undefined): string {
  return user?.email ?? ''
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

function AdminGroupDetail() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { groupId } = Route.useParams()

  // --- Edit group state ---
  const [editOpen, setEditOpen] = useState(false)
  const [editName, setEditName] = useState('')
  const [editDescription, setEditDescription] = useState('')
  const [editDuplicateError, setEditDuplicateError] = useState(false)

  // --- Add member state ---
  const [addMemberOpen, setAddMemberOpen] = useState(false)
  const [comboboxOpen, setComboboxOpen] = useState(false)
  const [selectedUserId, setSelectedUserId] = useState<string | null>(null)

  // ---------------------------------------------------------------------------
  // Queries
  // ---------------------------------------------------------------------------

  const { data: groupData, isLoading: groupLoading } = useQuery({
    queryKey: ['admin-groups'],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/admin/groups`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(`Failed to fetch groups (${res.status})`)
      return res.json() as Promise<{ groups: Group[] }>
    },
    enabled: !!token,
    select: (data) => data.groups.find((g) => g.id === Number(groupId)),
  })

  const { data: membersData, isLoading: membersLoading } = useQuery({
    queryKey: ['admin-group-members', groupId],
    queryFn: async () => {
      const res = await fetch(
        `${API_BASE}/api/admin/groups/${groupId}/members`,
        { headers: { Authorization: `Bearer ${token}` } },
      )
      if (!res.ok) throw new Error(`Failed to fetch members (${res.status})`)
      return res.json() as Promise<{ members: Member[] }>
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

  const members = membersData?.members ?? []
  const orgUsers = usersData?.users ?? []

  // Build lookup map for user display
  const usersMap = new Map(orgUsers.map((u) => [u.zitadel_user_id, u]))

  // Users not yet members (for the add-member combobox)
  const memberIds = new Set(members.map((mb) => mb.zitadel_user_id))
  const availableUsers = orgUsers.filter((u) => !memberIds.has(u.zitadel_user_id))

  // ---------------------------------------------------------------------------
  // Mutations
  // ---------------------------------------------------------------------------

  const updateMutation = useMutation({
    mutationFn: async (body: { name: string; description: string }) => {
      const res = await fetch(`${API_BASE}/api/admin/groups/${groupId}`, {
        method: 'PATCH',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(body),
      })
      if (res.status === 409) throw new Error('duplicate')
      if (!res.ok) throw new Error(`Failed to update group (${res.status})`)
      return res.json() as Promise<Group>
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-group', groupId] })
      void queryClient.invalidateQueries({ queryKey: ['admin-groups'] })
      toast.success(m.admin_groups_success_updated())
      closeEditDialog()
    },
    onError: (err: Error) => {
      if (err.message === 'duplicate') {
        setEditDuplicateError(true)
      } else {
        toast.error(err.message)
      }
    },
  })

  const deleteMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch(`${API_BASE}/api/admin/groups/${groupId}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(`Failed to delete group (${res.status})`)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-groups'] })
      toast.success(m.admin_groups_success_deleted())
      void navigate({ to: '/admin/groups' })
    },
    onError: (err: Error) => {
      toast.error(err.message)
    },
  })

  const addMemberMutation = useMutation({
    mutationFn: async (zitadel_user_id: string) => {
      const res = await fetch(
        `${API_BASE}/api/admin/groups/${groupId}/members`,
        {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ zitadel_user_id }),
        },
      )
      if (res.status === 409) throw new Error('already_member')
      if (!res.ok) throw new Error(`Failed to add member (${res.status})`)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ['admin-group-members', groupId],
      })
      toast.success(m.admin_groups_members_success_added())
      closeAddMemberDialog()
    },
    onError: (err: Error) => {
      if (err.message === 'already_member') {
        toast.error(m.admin_groups_members_error_already_member())
      } else {
        toast.error(err.message)
      }
    },
  })

  const removeMemberMutation = useMutation({
    mutationFn: async (userId: string) => {
      const res = await fetch(
        `${API_BASE}/api/admin/groups/${groupId}/members/${userId}`,
        {
          method: 'DELETE',
          headers: { Authorization: `Bearer ${token}` },
        },
      )
      if (!res.ok) throw new Error(`Failed to remove member (${res.status})`)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ['admin-group-members', groupId],
      })
      toast.success(m.admin_groups_members_success_removed())
    },
    onError: (err: Error) => {
      toast.error(err.message)
    },
  })

  const toggleAdminMutation = useMutation({
    mutationFn: async ({
      userId,
      is_group_admin,
    }: {
      userId: string
      is_group_admin: boolean
    }) => {
      const res = await fetch(
        `${API_BASE}/api/admin/groups/${groupId}/members/${userId}`,
        {
          method: 'PATCH',
          headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ is_group_admin }),
        },
      )
      if (!res.ok)
        throw new Error(`Failed to toggle admin status (${res.status})`)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ['admin-group-members', groupId],
      })
      toast.success(m.admin_groups_members_admin_toggled())
    },
    onError: (err: Error) => {
      toast.error(err.message)
    },
  })

  // ---------------------------------------------------------------------------
  // Dialog helpers
  // ---------------------------------------------------------------------------

  function openEditDialog() {
    if (groupData) {
      setEditName(groupData.name)
      setEditDescription(groupData.description ?? '')
    }
    setEditDuplicateError(false)
    setEditOpen(true)
  }

  function closeEditDialog() {
    setEditOpen(false)
    setEditName('')
    setEditDescription('')
    setEditDuplicateError(false)
  }

  function handleEditSubmit(e: React.FormEvent) {
    e.preventDefault()
    setEditDuplicateError(false)
    updateMutation.mutate({
      name: editName.trim(),
      description: editDescription.trim(),
    })
  }

  function closeAddMemberDialog() {
    setAddMemberOpen(false)
    setSelectedUserId(null)
    setComboboxOpen(false)
  }

  function handleAddMemberSubmit() {
    if (selectedUserId) {
      addMemberMutation.mutate(selectedUserId)
    }
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  if (groupLoading) {
    return (
      <div className="p-8">
        <p className="text-sm text-[var(--color-muted-foreground)]">
          <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
          Loading...
        </p>
      </div>
    )
  }

  if (!groupData) {
    return (
      <div className="p-8">
        <p className="text-sm text-[var(--color-destructive)]">
          Group not found
        </p>
      </div>
    )
  }

  const selectedUser = selectedUserId
    ? orgUsers.find((u) => u.zitadel_user_id === selectedUserId)
    : null

  return (
    <div className="p-8 space-y-6 max-w-4xl">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
            {groupData.name}
          </h1>
          {groupData.description && (
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {groupData.description}
            </p>
          )}
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigate({ to: '/admin/groups' })}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_groups_title()}
        </Button>
      </div>

      {/* Group info card */}
      <Card>
        <CardContent className="pt-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-semibold text-lg">
              {m.admin_groups_edit()}
            </h2>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={openEditDialog}>
                {m.admin_groups_edit()}
              </Button>

              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button
                    variant="destructive"
                    size="sm"
                    disabled={deleteMutation.isPending}
                  >
                    {deleteMutation.isPending && (
                      <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    )}
                    {m.admin_groups_delete()}
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>
                      {m.admin_groups_confirm_delete_title()}
                    </AlertDialogTitle>
                    <AlertDialogDescription>
                      {m.admin_groups_confirm_delete_description()}
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>
                      {m.admin_users_cancel()}
                    </AlertDialogCancel>
                    <AlertDialogAction
                      onClick={() => deleteMutation.mutate()}
                    >
                      {m.admin_groups_delete()}
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            </div>
          </div>

          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
            <div>
              <dt className="text-[var(--color-muted-foreground)]">
                {m.admin_groups_name()}
              </dt>
              <dd className="text-[var(--color-purple-deep)] font-medium">
                {groupData.name}
              </dd>
            </div>
            <div>
              <dt className="text-[var(--color-muted-foreground)]">
                {m.admin_groups_description()}
              </dt>
              <dd className="text-[var(--color-purple-deep)]">
                {groupData.description || '-'}
              </dd>
            </div>
            <div>
              <dt className="text-[var(--color-muted-foreground)]">
                {m.admin_groups_created_at()}
              </dt>
              <dd className="text-[var(--color-purple-deep)]">
                {formatDate(groupData.created_at)}
              </dd>
            </div>
            <div>
              <dt className="text-[var(--color-muted-foreground)]">
                {m.admin_groups_created_by()}
              </dt>
              <dd className="text-[var(--color-purple-deep)]">
                {groupData.created_by}
              </dd>
            </div>
          </dl>
        </CardContent>
      </Card>

      {/* Members section */}
      <Card>
        <CardContent className="pt-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-semibold text-lg">
              {m.admin_groups_members_title()}
            </h2>
            <Button
              size="sm"
              onClick={() => setAddMemberOpen(true)}
            >
              <UserPlus className="h-4 w-4 mr-2" />
              {m.admin_groups_members_add()}
            </Button>
          </div>

          {membersLoading ? (
            <p className="text-sm text-[var(--color-muted-foreground)]">
              <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
              Loading...
            </p>
          ) : members.length === 0 ? (
            <p className="text-sm text-[var(--color-muted-foreground)] py-4 text-center">
              {m.admin_groups_members_empty()}
            </p>
          ) : (
            <div className="overflow-hidden rounded-xl border border-[var(--color-border)]">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[var(--color-border)]">
                    <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                      {m.admin_groups_name()}
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                      Email
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                      {m.admin_groups_members_joined_at()}
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                      {m.admin_groups_members_admin()}
                    </th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                      {/* Actions */}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {members.map((member, i) => {
                    const user = usersMap.get(member.zitadel_user_id)
                    const isTogglingAdmin =
                      toggleAdminMutation.isPending &&
                      toggleAdminMutation.variables?.userId ===
                        member.zitadel_user_id
                    const isRemoving =
                      removeMemberMutation.isPending &&
                      removeMemberMutation.variables === member.zitadel_user_id

                    return (
                      <tr
                        key={member.zitadel_user_id}
                        className={
                          i % 2 === 0
                            ? 'bg-[var(--color-card)]'
                            : 'bg-[var(--color-secondary)]'
                        }
                      >
                        <td className="px-6 py-3 text-[var(--color-purple-deep)]">
                          {displayName(user, member)}
                        </td>
                        <td className="px-6 py-3 text-[var(--color-muted-foreground)]">
                          {displayEmail(user)}
                        </td>
                        <td className="px-6 py-3 text-[var(--color-purple-deep)]">
                          {formatDate(member.joined_at)}
                        </td>
                        <td className="px-6 py-3">
                          <div className="flex items-center gap-2">
                            <Switch
                              checked={member.is_group_admin}
                              disabled={isTogglingAdmin}
                              onCheckedChange={(checked) =>
                                toggleAdminMutation.mutate({
                                  userId: member.zitadel_user_id,
                                  is_group_admin: checked,
                                })
                              }
                            />
                            {member.is_group_admin && (
                              <Badge variant="secondary">
                                {m.admin_groups_members_admin()}
                              </Badge>
                            )}
                          </div>
                        </td>
                        <td className="px-6 py-3 text-right">
                          <AlertDialog>
                            <AlertDialogTrigger asChild>
                              <Button
                                variant="ghost"
                                size="sm"
                                disabled={isRemoving}
                              >
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
                                  {displayName(user, member)}
                                </AlertDialogDescription>
                              </AlertDialogHeader>
                              <AlertDialogFooter>
                                <AlertDialogCancel>
                                  {m.admin_users_cancel()}
                                </AlertDialogCancel>
                                <AlertDialogAction
                                  onClick={() =>
                                    removeMemberMutation.mutate(
                                      member.zitadel_user_id,
                                    )
                                  }
                                >
                                  {m.admin_groups_members_remove()}
                                </AlertDialogAction>
                              </AlertDialogFooter>
                            </AlertDialogContent>
                          </AlertDialog>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Edit group dialog */}
      <Dialog
        open={editOpen}
        onOpenChange={(open) => {
          if (!open) closeEditDialog()
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{m.admin_groups_edit()}</DialogTitle>
            <DialogDescription className="sr-only">
              {m.admin_groups_edit()}
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleEditSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="edit-group-name">{m.admin_groups_name()}</Label>
              <Input
                id="edit-group-name"
                value={editName}
                onChange={(e) => {
                  setEditName(e.target.value)
                  setEditDuplicateError(false)
                }}
                placeholder={m.admin_groups_name_placeholder()}
                required
                autoFocus
              />
              {editDuplicateError && (
                <p className="text-sm text-[var(--color-destructive)]">
                  {m.admin_groups_error_duplicate()}
                </p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="edit-group-description">
                {m.admin_groups_description()}
              </Label>
              <textarea
                id="edit-group-description"
                value={editDescription}
                onChange={(e) => setEditDescription(e.target.value)}
                placeholder={m.admin_groups_description_placeholder()}
                rows={3}
                className="w-full rounded-md border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm text-[var(--color-purple-deep)] outline-none transition-colors placeholder:text-[var(--color-muted-foreground)] focus:ring-2 focus:ring-[var(--color-ring)] disabled:cursor-not-allowed disabled:opacity-50"
              />
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={closeEditDialog}
                disabled={updateMutation.isPending}
              >
                {m.admin_users_cancel()}
              </Button>
              <Button
                type="submit"
                disabled={updateMutation.isPending || !editName.trim()}
              >
                {updateMutation.isPending && (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                )}
                {m.admin_groups_edit()}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Add member dialog */}
      <Dialog
        open={addMemberOpen}
        onOpenChange={(open) => {
          if (!open) closeAddMemberDialog()
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{m.admin_groups_members_add()}</DialogTitle>
            <DialogDescription className="sr-only">
              {m.admin_groups_members_add()}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <Popover open={comboboxOpen} onOpenChange={setComboboxOpen}>
              <PopoverTrigger asChild>
                <Button
                  variant="outline"
                  role="combobox"
                  aria-expanded={comboboxOpen}
                  className="w-full justify-between font-normal"
                >
                  {selectedUser
                    ? `${selectedUser.first_name} ${selectedUser.last_name}`.trim() ||
                      selectedUser.email
                    : m.admin_groups_members_search_placeholder()}
                  <span className="ml-2 opacity-50">&#x25BE;</span>
                </Button>
              </PopoverTrigger>
              <PopoverContent className="w-[var(--radix-popover-trigger-width)] p-0" align="start">
                <Command>
                  <CommandInput
                    placeholder={m.admin_groups_members_search_placeholder()}
                  />
                  <CommandList>
                    <CommandEmpty>No users found</CommandEmpty>
                    <CommandGroup>
                      {availableUsers.map((u) => {
                        const label =
                          `${u.first_name} ${u.last_name}`.trim() || u.email
                        return (
                          <CommandItem
                            key={u.zitadel_user_id}
                            value={`${u.first_name} ${u.last_name} ${u.email}`}
                            onSelect={() => {
                              setSelectedUserId(u.zitadel_user_id)
                              setComboboxOpen(false)
                            }}
                          >
                            <span>{label}</span>
                            <span className="ml-auto text-xs text-[var(--color-muted-foreground)]">
                              {u.email}
                            </span>
                          </CommandItem>
                        )
                      })}
                    </CommandGroup>
                  </CommandList>
                </Command>
              </PopoverContent>
            </Popover>

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={closeAddMemberDialog}
                disabled={addMemberMutation.isPending}
              >
                {m.admin_users_cancel()}
              </Button>
              <Button
                type="button"
                onClick={handleAddMemberSubmit}
                disabled={addMemberMutation.isPending || !selectedUserId}
              >
                {addMemberMutation.isPending && (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                )}
                {m.admin_groups_members_add()}
              </Button>
            </DialogFooter>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
