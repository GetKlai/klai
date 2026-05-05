import { createFileRoute, Link, redirect, useNavigate } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import { Tooltip } from '@/components/ui/tooltip'
import { ArrowLeft, Loader2, Trash2, UserPlus } from 'lucide-react'
import { toast } from 'sonner'
import * as m from '@/paraglide/messages'
import { getLocale } from '@/paraglide/runtime'
import { datetime } from '@/paraglide/registry'
import { apiFetch } from '@/lib/apiFetch'
import { PROFILE_LADDER, type ProfileRole } from '@/lib/profiles'
import { UserAvatar, displayName } from '../../_components/UserAvatar'
import { cleanErrorMessage } from '../../_components/errors'
import { useCurrentUser } from '@/hooks/useCurrentUser'

export const Route = createFileRoute('/admin/profiles/$profile/')({
  component: AdminProfileDetail,
  beforeLoad: ({ params }) => {
    if (!PROFILE_LADDER.includes(params.profile as ProfileRole)) {
      throw redirect({ to: '/admin/profiles' })
    }
  },
})

interface OrgUser {
  zitadel_user_id: string
  email: string
  first_name: string
  last_name: string
  role: ProfileRole
  created_at: string
}

function formatDate(isoString: string): string {
  return datetime(getLocale(), isoString, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

function AdminProfileDetail() {
  const auth = useAuth()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { profile } = Route.useParams()
  const { user: currentUser } = useCurrentUser()
  const profileRole = profile as ProfileRole
  const [confirmDemoteId, setConfirmDemoteId] = useState<string | null>(null)

  const msgs = m as unknown as Record<string, (() => string) | undefined>
  const labelFn = msgs[`profile_${profileRole}_label`]
  const descFn = msgs[`profile_${profileRole}_description`]
  const profileLabel = labelFn ? labelFn() : profileRole
  const profileDescription = descFn ? descFn() : ''

  const { data: usersData, isLoading } = useQuery({
    queryKey: ['admin-users'],
    queryFn: async () => apiFetch<{ users: OrgUser[] }>(`/api/admin/users`),
    enabled: auth.isAuthenticated,
  })

  const members = (usersData?.users ?? []).filter((u) => u.role === profileRole)

  const removeMemberMutation = useMutation({
    mutationFn: async (userId: string) => {
      await apiFetch(`/api/admin/users/${userId}/role`, {
        method: 'PATCH',
        body: JSON.stringify({ role: 'personal' }),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      setConfirmDemoteId(null)
      toast.success(m.admin_profiles_demote_success())
    },
    onError: (err: Error) => {
      toast.error(cleanErrorMessage(err, m.admin_profiles_error_change()))
    },
  })

  return (
    <div className="mx-auto max-w-2xl px-6 py-10 space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="space-y-2">
          <h1 className="page-title text-[26px] font-display-bold text-gray-900">
            {profileLabel}
          </h1>
          {profileDescription && (
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {profileDescription}
            </p>
          )}
        </div>
        <Button asChild variant="ghost" size="sm">
          <Link to="/admin/profiles">
            <ArrowLeft className="h-4 w-4 mr-2" />
            {m.admin_profiles_back()}
          </Link>
        </Button>
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
                  to: '/admin/profiles/$profile/add-member',
                  params: { profile: profileRole },
                })
              }
            >
              <UserPlus className="h-4 w-4 mr-2" />
              {m.admin_groups_members_add()}
            </Button>
          </div>

          {isLoading ? (
            <p className="text-sm text-[var(--color-muted-foreground)]">
              <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
              {m.admin_profiles_loading()}
            </p>
          ) : members.length === 0 ? (
            <p className="text-sm text-[var(--color-muted-foreground)] py-4 text-center">
              {m.admin_profiles_drill_in_empty()}
            </p>
          ) : (
            <table className="w-full text-sm table-fixed border-t border-b border-[var(--color-border)]">
              <thead>
                <tr className="border-b border-[var(--color-border)]">
                  <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 tracking-wide w-12">
                    {/* avatar */}
                  </th>
                  <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 tracking-wide">
                    {m.admin_users_col_name()}
                  </th>
                  <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 tracking-wide">
                    {m.admin_users_col_email()}
                  </th>
                  <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 tracking-wide w-28">
                    {m.admin_users_col_invited()}
                  </th>
                  <th className="py-3 text-right text-xs font-medium text-gray-400 tracking-wide w-16">
                    {/* Actions */}
                  </th>
                </tr>
              </thead>
              <tbody>
                {members.map((user) => {
                  const isDemoting =
                    removeMemberMutation.isPending &&
                    removeMemberMutation.variables === user.zitadel_user_id
                  const isConfirming = confirmDemoteId === user.zitadel_user_id
                  const isSelf = user.zitadel_user_id === currentUser?.user_id
                  // Demoting to "personal" is a no-op when already "personal".
                  const alreadyPersonal = profileRole === 'personal'
                  const demoteDisabled = isSelf || alreadyPersonal
                  const demoteTooltip = isSelf
                    ? m.admin_profiles_demote_self_blocked()
                    : m.admin_profiles_demote_action()

                  return (
                    <tr
                      key={user.zitadel_user_id}
                      className="border-b border-[var(--color-border)] last:border-b-0"
                    >
                      <td className="py-4 pr-4 align-top w-12">
                        <UserAvatar
                          uid={user.zitadel_user_id}
                          first_name={user.first_name}
                          last_name={user.last_name}
                          email={user.email}
                          size="sm"
                        />
                      </td>
                      <td className="py-4 pr-4 align-top text-[var(--color-foreground)]">
                        {displayName(user)}
                      </td>
                      <td className="py-4 pr-4 align-top text-[var(--color-muted-foreground)]">
                        {user.email}
                      </td>
                      <td className="py-4 pr-4 align-top text-[var(--color-foreground)] whitespace-nowrap tabular-nums w-28">
                        {formatDate(user.created_at)}
                      </td>
                      <td className="py-4 align-top text-right w-16">
                        {alreadyPersonal ? (
                          // No demote target on the lowest rung.
                          <span className="text-xs text-[var(--color-muted-foreground)]">—</span>
                        ) : (
                          <InlineDeleteConfirm
                            isConfirming={isConfirming}
                            isPending={isDemoting}
                            label={m.admin_profiles_demote_confirm({ name: displayName(user) })}
                            cancelLabel={m.admin_users_cancel()}
                            onConfirm={() => removeMemberMutation.mutate(user.zitadel_user_id)}
                            onCancel={() => setConfirmDemoteId(null)}
                          >
                            <div className="flex items-start justify-end gap-2 mt-px">
                              <Tooltip label={demoteTooltip}>
                                <button
                                  onClick={() => {
                                    if (!demoteDisabled) setConfirmDemoteId(user.zitadel_user_id)
                                  }}
                                  disabled={demoteDisabled}
                                  aria-label={demoteTooltip}
                                  className="inline-flex items-center justify-center text-[var(--color-destructive)] transition-opacity hover:opacity-70 disabled:opacity-30 disabled:cursor-not-allowed"
                                >
                                  <Trash2 className="h-4 w-4" />
                                </button>
                              </Tooltip>
                            </div>
                          </InlineDeleteConfirm>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
