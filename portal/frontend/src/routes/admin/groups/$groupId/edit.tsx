import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useEffect } from 'react'
import { ArrowLeft, Loader2, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
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
import * as m from '@/paraglide/messages'
import { getLocale } from '@/paraglide/runtime'
import { datetime } from '@/paraglide/registry'
import { API_BASE } from '@/lib/api'

export const Route = createFileRoute('/admin/groups/$groupId/edit')({
  component: EditGroupPage,
})

interface Group {
  id: number
  name: string
  description: string | null
}

interface Member {
  zitadel_user_id: string
  joined_at: string
}

interface OrgUser {
  zitadel_user_id: string
  email: string
  first_name: string
  last_name: string
}

function displayName(user: OrgUser | undefined, fallback: string): string {
  if (!user) return fallback
  return `${user.first_name} ${user.last_name}`.trim() || user.email
}

function formatDate(isoString: string): string {
  return datetime(getLocale(), isoString, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

function EditGroupPage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { groupId } = Route.useParams()

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [duplicateError, setDuplicateError] = useState(false)
  const [initialized, setInitialized] = useState(false)
  const [confirmRemoveId, setConfirmRemoveId] = useState<string | null>(null)
  const [comboboxOpen, setComboboxOpen] = useState(false)
  const [selectedUserId, setSelectedUserId] = useState<string | null>(null)

  // ---------------------------------------------------------------------------
  // Queries
  // ---------------------------------------------------------------------------

  const { data: group, isLoading: groupLoading } = useQuery({
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
      const res = await fetch(`${API_BASE}/api/admin/groups/${groupId}/members`, {
        headers: { Authorization: `Bearer ${token}` },
      })
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

  useEffect(() => {
    if (group && !initialized) {
      setName(group.name)
      setDescription(group.description ?? '')
      setInitialized(true)
    }
  }, [group, initialized])

  const members = membersData?.members ?? []
  const orgUsers = usersData?.users ?? []
  const usersMap = new Map(orgUsers.map((u) => [u.zitadel_user_id, u]))
  const memberIds = new Set(members.map((mb) => mb.zitadel_user_id))
  const availableUsers = orgUsers.filter((u) => !memberIds.has(u.zitadel_user_id))
  const selectedUser = selectedUserId ? usersMap.get(selectedUserId) : undefined

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
      return res.json()
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-groups'] })
      toast.success(m.admin_groups_success_updated())
    },
    onError: (err: Error) => {
      if (err.message === 'duplicate') setDuplicateError(true)
      else toast.error(err.message)
    },
  })

  const removeMemberMutation = useMutation({
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
      setConfirmRemoveId(null)
      toast.success(m.admin_groups_members_success_removed())
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const addMemberMutation = useMutation({
    mutationFn: async (userId: string) => {
      const res = await fetch(`${API_BASE}/api/admin/groups/${groupId}/members`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
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
      if (err.message === 'already_member') toast.error(m.admin_groups_members_error_already_member())
      else toast.error(err.message)
    },
  })

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

  if (!group) {
    return (
      <div className="p-8">
        <p className="text-sm text-[var(--color-destructive)]">Group not found</p>
      </div>
    )
  }

  return (
    <div className="p-8 max-w-2xl space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
          {m.admin_groups_edit()}
        </h1>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => navigate({ to: '/admin/groups' })}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_users_cancel()}
        </Button>
      </div>

      {/* Name + description */}
      <Card>
        <CardContent className="pt-6">
          <form
            onSubmit={(e) => {
              e.preventDefault()
              setDuplicateError(false)
              updateMutation.mutate({ name: name.trim(), description: description.trim() })
            }}
            className="space-y-4"
          >
            <div className="space-y-1.5">
              <Label htmlFor="group-name">{m.admin_groups_name()}</Label>
              <Input
                id="group-name"
                value={name}
                onChange={(e) => { setName(e.target.value); setDuplicateError(false) }}
                placeholder={m.admin_groups_name_placeholder()}
                required
              />
              {duplicateError && (
                <p className="text-sm text-[var(--color-destructive)]">
                  {m.admin_groups_error_duplicate()}
                </p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="group-description">{m.admin_groups_description()}</Label>
              <textarea
                id="group-description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder={m.admin_groups_description_placeholder()}
                rows={3}
                className="w-full rounded-md border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm text-[var(--color-purple-deep)] outline-none transition-colors placeholder:text-[var(--color-muted-foreground)] focus:ring-2 focus:ring-[var(--color-ring)] disabled:cursor-not-allowed disabled:opacity-50"
              />
            </div>
            <div className="pt-2">
              <Button type="submit" disabled={updateMutation.isPending || !name.trim()}>
                {updateMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                {m.admin_groups_success_updated()}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {/* Members */}
      <Card>
        <CardContent className="pt-6 space-y-4">
          <h2 className="font-semibold text-[var(--color-purple-deep)]">
            {m.admin_groups_members_title()}
          </h2>

          {/* Add member */}
          <div className="flex gap-2">
            <Popover open={comboboxOpen} onOpenChange={setComboboxOpen}>
              <PopoverTrigger asChild>
                <Button
                  type="button"
                  variant="outline"
                  role="combobox"
                  aria-expanded={comboboxOpen}
                  className="flex-1 justify-between font-normal"
                >
                  <span className="truncate text-left">
                    {selectedUser
                      ? displayName(selectedUser, selectedUser.zitadel_user_id)
                      : m.admin_groups_members_search_placeholder()}
                  </span>
                  <span className="ml-2 opacity-50 shrink-0">&#x25BE;</span>
                </Button>
              </PopoverTrigger>
              <PopoverContent className="w-[var(--radix-popover-trigger-width)] p-0" align="start">
                <Command>
                  <CommandInput placeholder={m.admin_groups_members_search_placeholder()} />
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
                          <span>{displayName(u, u.zitadel_user_id)}</span>
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
              type="button"
              disabled={!selectedUserId || addMemberMutation.isPending}
              onClick={() => selectedUserId && addMemberMutation.mutate(selectedUserId)}
            >
              {addMemberMutation.isPending
                ? <Loader2 className="h-4 w-4 animate-spin" />
                : m.admin_groups_members_add()}
            </Button>
          </div>

          {/* Members list */}
          {membersLoading ? (
            <p className="text-sm text-[var(--color-muted-foreground)]">
              <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
              Loading...
            </p>
          ) : members.length === 0 ? (
            <p className="text-sm text-[var(--color-muted-foreground)] py-2 text-center">
              {m.admin_groups_members_empty()}
            </p>
          ) : (
            <div className="overflow-hidden rounded-xl border border-[var(--color-border)]">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[var(--color-border)]">
                    <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                      {m.admin_groups_name()}
                    </th>
                    <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                      Email
                    </th>
                    <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                      {m.admin_groups_members_joined_at()}
                    </th>
                    <th className="px-4 py-2.5" />
                  </tr>
                </thead>
                <tbody>
                  {members.map((member, i) => {
                    const user = usersMap.get(member.zitadel_user_id)
                    const isConfirming = confirmRemoveId === member.zitadel_user_id
                    const isRemoving =
                      removeMemberMutation.isPending &&
                      removeMemberMutation.variables === member.zitadel_user_id

                    return (
                      <tr
                        key={member.zitadel_user_id}
                        className={i % 2 === 0 ? 'bg-[var(--color-card)]' : 'bg-[var(--color-secondary)]'}
                      >
                        <td className="px-4 py-2.5 text-[var(--color-purple-deep)]">
                          {displayName(user, member.zitadel_user_id)}
                        </td>
                        <td className="px-4 py-2.5 text-[var(--color-muted-foreground)]">
                          {user?.email ?? ''}
                        </td>
                        <td className="px-4 py-2.5 text-[var(--color-muted-foreground)]">
                          {formatDate(member.joined_at)}
                        </td>
                        <td className="px-4 py-2.5 text-right">
                          <div className="flex items-center justify-end gap-1">
                            {isConfirming ? (
                              <>
                                <Button
                                  size="sm"
                                  className="bg-[var(--color-destructive)] text-white hover:opacity-90"
                                  disabled={isRemoving}
                                  onClick={() => removeMemberMutation.mutate(member.zitadel_user_id)}
                                >
                                  {isRemoving
                                    ? <Loader2 className="h-3 w-3 animate-spin" />
                                    : m.admin_groups_members_remove()}
                                </Button>
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  onClick={() => setConfirmRemoveId(null)}
                                >
                                  {m.admin_users_cancel()}
                                </Button>
                              </>
                            ) : (
                              <button
                                onClick={() => setConfirmRemoveId(member.zitadel_user_id)}
                                className="flex h-7 w-7 items-center justify-center text-[var(--color-destructive)] transition-opacity hover:opacity-70"
                                aria-label={m.admin_groups_members_remove()}
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            )}
                          </div>
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
    </div>
  )
}
