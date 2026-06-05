import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import {
  RowActionGroup,
  RowActionIconButton,
  BorderedRowActionIconButton,
} from '@/components/ui/row-action'
import {
  DataTable,
  DataTableHeader,
  DataTableBody,
  DataTableRow,
  DataTableHead,
  DataTableCell,
} from '@/components/ui/data-table'
import { ListLoadingState, ListEmptyState } from '@/components/ui/list-state'
import { ArrowLeft, UserPlus } from 'lucide-react'
import { toast } from 'sonner'
import * as m from '@/paraglide/messages'
import { getLocale } from '@/paraglide/runtime'
import { datetime } from '@/paraglide/registry'
import { apiFetch } from '@/lib/apiFetch'

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
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { groupId } = Route.useParams()
  const [confirmRemoveId, setConfirmRemoveId] = useState<string | null>(null)

  // ---------------------------------------------------------------------------
  // Queries
  // ---------------------------------------------------------------------------

  const { data: groupData, isLoading: groupLoading } = useQuery({
    queryKey: ['admin-groups'],
    queryFn: async () => apiFetch<{ groups: Group[] }>(`/api/admin/groups`),
    enabled: auth.isAuthenticated,
    select: (data) => data.groups.find((g) => g.id === Number(groupId)),
  })

  const { data: membersData, isLoading: membersLoading } = useQuery({
    queryKey: ['admin-group-members', groupId],
    queryFn: async () => apiFetch<{ members: Member[] }>(`/api/admin/groups/${groupId}/members`),
    enabled: auth.isAuthenticated,
  })

  const { data: usersData } = useQuery({
    queryKey: ['admin-users'],
    queryFn: async () => apiFetch<{ users: OrgUser[] }>(`/api/admin/users`),
    enabled: auth.isAuthenticated,
  })

  const members = membersData?.members ?? []
  const orgUsers = usersData?.users ?? []
  const usersMap = new Map(orgUsers.map((u) => [u.zitadel_user_id, u]))

  // ---------------------------------------------------------------------------
  // Mutations
  // ---------------------------------------------------------------------------

  const removeMemberMutation = useMutation({
    mutationFn: async (userId: string) => {
      await apiFetch(`/api/admin/groups/${groupId}/members/${userId}`, { method: 'DELETE' })
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
      <div className="mx-auto max-w-2xl px-6 pt-4 pb-10">
        <ListLoadingState label={m.admin_shared_loading()} />
      </div>
    )
  }

  if (!groupData) {
    return (
      <div className="mx-auto max-w-2xl px-6 pt-4 pb-10">
        <ListEmptyState title={m.admin_groups_not_found()} />
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-2xl px-6 pt-4 pb-10 space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="space-y-2">
          <h1 className="page-title text-[26px] font-display-bold text-gray-900">
            {groupData.name}
          </h1>
          {groupData.description && (
            <p className="text-sm text-gray-400">
              {groupData.description}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          {!groupData.is_system && (
            <RowActionIconButton
              action="edit"
              label={m.admin_groups_edit()}
              onClick={() =>
                navigate({
                  to: '/admin/groups/$groupId/edit',
                  params: { groupId },
                })
              }
            />
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
            <h2 className="text-base font-medium">
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
            <ListLoadingState label={m.admin_shared_loading()} />
          ) : members.length === 0 ? (
            <ListEmptyState title={m.admin_groups_members_empty()} />
          ) : (
            <DataTable>
              <DataTableHeader>
                <DataTableRow>
                  <DataTableHead>{m.admin_groups_name()}</DataTableHead>
                  <DataTableHead>{m.admin_groups_members_email()}</DataTableHead>
                  <DataTableHead>{m.admin_groups_members_joined_at()}</DataTableHead>
                  <DataTableHead align="right" />
                </DataTableRow>
              </DataTableHeader>
              <DataTableBody>
                {members.map((member) => {
                  const user = usersMap.get(member.zitadel_user_id)
                  const isRemoving =
                    removeMemberMutation.isPending &&
                    removeMemberMutation.variables === member.zitadel_user_id
                  const isConfirming = confirmRemoveId === member.zitadel_user_id

                  return (
                    <DataTableRow key={member.zitadel_user_id} confirming={isConfirming}>
                      <DataTableCell>{displayName(user, member)}</DataTableCell>
                      <DataTableCell className="text-gray-400">
                        {displayEmail(user)}
                      </DataTableCell>
                      <DataTableCell className="whitespace-nowrap tabular-nums">
                        {formatDate(member.joined_at)}
                      </DataTableCell>
                      <DataTableCell align="right">
                        <InlineDeleteConfirm
                          isConfirming={isConfirming}
                          isPending={isRemoving}
                          label={m.admin_groups_members_remove_confirm({ name: displayName(user, member) })}
                          cancelLabel={m.admin_users_cancel()}
                          onConfirm={() => removeMemberMutation.mutate(member.zitadel_user_id)}
                          onCancel={() => setConfirmRemoveId(null)}
                        >
                          <RowActionGroup>
                            <BorderedRowActionIconButton
                              action="delete"
                              label={m.admin_groups_members_remove()}
                              onClick={() => setConfirmRemoveId(member.zitadel_user_id)}
                            />
                          </RowActionGroup>
                        </InlineDeleteConfirm>
                      </DataTableCell>
                    </DataTableRow>
                  )
                })}
              </DataTableBody>
            </DataTable>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
