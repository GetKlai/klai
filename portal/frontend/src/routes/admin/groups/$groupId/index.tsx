import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ArrowLeft, Loader2, Pencil, Trash2, UserPlus } from 'lucide-react'
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
  products: string[]
  is_system: boolean
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
  const [confirmRemoveId, setConfirmRemoveId] = useState<string | null>(null)

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
  const usersMap = new Map(orgUsers.map((u) => [u.zitadel_user_id, u]))

  // ---------------------------------------------------------------------------
  // Mutations
  // ---------------------------------------------------------------------------

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
      void queryClient.invalidateQueries({ queryKey: ['admin-group-members', groupId] })
      void queryClient.invalidateQueries({ queryKey: ['admin-group-memberships'] })
      void queryClient.invalidateQueries({ queryKey: ['admin-user-groups'] })
      setConfirmRemoveId(null)
      toast.success(m.admin_groups_members_success_removed())
    },
    onError: (err: Error) => {
      toast.error(err.message)
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

  if (!groupData) {
    return (
      <div className="p-8">
        <p className="text-sm text-[var(--color-destructive)]">Group not found</p>
      </div>
    )
  }

  return (
    <div className="p-8 space-y-6 max-w-2xl">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="space-y-2">
          <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
            {groupData.name}
          </h1>
          {groupData.description && (
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {groupData.description}
            </p>
          )}
          {groupData.products.length > 0 && (
            <div className="flex gap-2">
              {groupData.products.map((p) => (
                <Badge key={p} className="capitalize">{p}</Badge>
              ))}
            </div>
          )}
        </div>
        <div className="flex items-center gap-2">
          {!groupData.is_system && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() =>
                navigate({
                  to: '/admin/groups/$groupId/edit',
                  params: { groupId },
                })
              }
            >
              <Pencil className="h-4 w-4 mr-2" />
              {m.admin_groups_edit()}
            </Button>
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => navigate({ to: '/admin/groups' })}
          >
            <ArrowLeft className="h-4 w-4 mr-2" />
            {m.admin_groups_title()}
          </Button>
        </div>
      </div>

      {/* Members section */}
      <Card>
        <CardContent className="pt-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-semibold text-lg">
              {m.admin_groups_members_title()}
            </h2>
            <Button
              size="sm"
              onClick={() =>
                navigate({
                  to: '/admin/groups/$groupId/add-member',
                  params: { groupId },
                })
              }
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
                    <th className="px-6 py-3 text-right text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                      {/* Actions */}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {members.map((member, i) => {
                    const user = usersMap.get(member.zitadel_user_id)
                    const isRemoving =
                      removeMemberMutation.isPending &&
                      removeMemberMutation.variables === member.zitadel_user_id
                    const isConfirming = confirmRemoveId === member.zitadel_user_id

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
                        <td className="px-6 py-3 text-right">
                          <div className="flex items-center justify-end gap-1">
                            {isConfirming ? (
                              <>
                                <Button
                                  size="sm"
                                  className="bg-[var(--color-destructive)] text-white hover:opacity-90"
                                  disabled={isRemoving}
                                  onClick={() =>
                                    removeMemberMutation.mutate(member.zitadel_user_id)
                                  }
                                >
                                  {isRemoving ? (
                                    <Loader2 className="h-3 w-3 animate-spin" />
                                  ) : (
                                    m.admin_groups_members_remove()
                                  )}
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
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() =>
                                  setConfirmRemoveId(member.zitadel_user_id)
                                }
                              >
                                <Trash2 className="h-4 w-4 text-[var(--color-destructive)]" />
                              </Button>
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
