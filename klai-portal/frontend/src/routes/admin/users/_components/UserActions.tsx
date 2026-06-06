import { useNavigate } from '@tanstack/react-router'
import type { UseMutationResult } from '@tanstack/react-query'
import {
  Loader2,
  LogOut,
  Pause,
  Play,
  ShieldCheck,
  Trash2,
} from 'lucide-react'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import { BorderedRowActionIconButton, RowActionGroup } from '@/components/ui/row-action'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
} from '@/components/ui/dropdown-menu'
import { PROFILE_LADDER, type ProfileRole } from '@/lib/profiles'
import * as m from '@/paraglide/messages'
import { profileLabel, userDisplayName } from '../-users-helpers'
import type { AdminUser } from '../-users-types'

type UserMutation<TVariables> = Pick<
  UseMutationResult<void, Error, TVariables>,
  'mutate' | 'isPending' | 'variables'
>

interface UserActionsProps {
  user: AdminUser
  currentUserId?: string
  confirmingDeleteId: string | null
  resendInviteMutation: UserMutation<AdminUser>
  deleteMutation: UserMutation<AdminUser>
  changeProfileMutation: UserMutation<{ userId: string; role: ProfileRole }>
  suspendMutation: UserMutation<string>
  reactivateMutation: UserMutation<string>
  onConfirmDelete: (userId: string | null) => void
  onConfirmUserDelete: (userId: string) => void
  onConfirmLeave: () => void
}

export function UserActions({
  user,
  currentUserId,
  confirmingDeleteId,
  resendInviteMutation,
  deleteMutation,
  changeProfileMutation,
  suspendMutation,
  reactivateMutation,
  onConfirmDelete,
  onConfirmUserDelete,
  onConfirmLeave,
}: UserActionsProps) {
  const navigate = useNavigate()
  const isSelf = user.zitadel_user_id === currentUserId
  const isResending =
    resendInviteMutation.isPending &&
    resendInviteMutation.variables?.zitadel_user_id === user.zitadel_user_id
  const isConfirmingDelete = confirmingDeleteId === user.zitadel_user_id
  const isDeleting =
    deleteMutation.isPending &&
    deleteMutation.variables?.zitadel_user_id === user.zitadel_user_id
  const canResendInvite =
    user.invite_pending || user.status === 'active' || user.status === 'offboarded'
  const canDeleteUser = !user.invite_pending

  return (
    <InlineDeleteConfirm
      isConfirming={isConfirmingDelete}
      isPending={isDeleting}
      label={m.admin_users_delete_confirm({ name: userDisplayName(user) })}
      cancelLabel={m.admin_users_cancel()}
      onConfirm={() => {
        onConfirmDelete(null)
        deleteMutation.mutate(user)
      }}
      onCancel={() => onConfirmDelete(null)}
    >
      <RowActionGroup>
        {canResendInvite && (
          <BorderedRowActionIconButton
            label={m.admin_users_resend_invite()}
            action="send"
            tone="neutral"
            disabled={isResending}
            spinner={isResending ? <Loader2 className="animate-spin" /> : undefined}
            onClick={() => resendInviteMutation.mutate(user)}
          />
        )}
        <BorderedRowActionIconButton
          label={m.admin_users_edit()}
          action="edit"
          onClick={() =>
            navigate({
              to: '/admin/users/$userId/edit',
              params: { userId: user.zitadel_user_id },
            })
          }
        />
        {user.invite_pending ? (
          <BorderedRowActionIconButton
            label={m.admin_users_delete()}
            action="delete"
            onClick={() => onConfirmDelete(user.zitadel_user_id)}
          />
        ) : (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <BorderedRowActionIconButton
                label={m.admin_users_col_actions()}
                action="more"
                tooltip={false}
              />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {!isSelf && (
                <DropdownMenuSub>
                  <DropdownMenuSubTrigger>
                    <ShieldCheck className="mr-2 h-4 w-4" />
                    {m.admin_users_action_change_profile()}
                  </DropdownMenuSubTrigger>
                  <DropdownMenuSubContent>
                    {PROFILE_LADDER.map((targetRole) => (
                      <DropdownMenuItem
                        key={targetRole}
                        disabled={user.role === targetRole || changeProfileMutation.isPending}
                        onClick={() =>
                          changeProfileMutation.mutate({
                            userId: user.zitadel_user_id,
                            role: targetRole,
                          })
                        }
                      >
                        {profileLabel(targetRole)}
                        {user.role === targetRole && (
                          <span className="ml-2 text-xs text-gray-400">
                            ({m.admin_settings_saved()})
                          </span>
                        )}
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuSubContent>
                </DropdownMenuSub>
              )}
              {isSelf && (
                <DropdownMenuItem
                  onClick={onConfirmLeave}
                  className="text-[var(--color-destructive)]"
                >
                  <LogOut className="mr-2 h-4 w-4" />
                  {m.admin_users_action_leave_workspace()}
                </DropdownMenuItem>
              )}
              {!isSelf && <DropdownMenuSeparator />}
              {!isSelf && (
                <>
                  {user.status === 'active' && (
                    <DropdownMenuItem onClick={() => suspendMutation.mutate(user.zitadel_user_id)}>
                      <Pause className="mr-2 h-4 w-4" />
                      {m.admin_users_action_suspend()}
                    </DropdownMenuItem>
                  )}
                  {user.status === 'suspended' && (
                    <DropdownMenuItem onClick={() => reactivateMutation.mutate(user.zitadel_user_id)}>
                      <Play className="mr-2 h-4 w-4" />
                      {m.admin_users_action_reactivate()}
                    </DropdownMenuItem>
                  )}
                  {canDeleteUser && (
                    <>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        onClick={() => onConfirmUserDelete(user.zitadel_user_id)}
                        className="text-[var(--color-destructive)]"
                      >
                        <Trash2 className="mr-2 h-4 w-4" />
                        {m.admin_users_action_delete()}
                      </DropdownMenuItem>
                    </>
                  )}
                </>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </RowActionGroup>
    </InlineDeleteConfirm>
  )
}
