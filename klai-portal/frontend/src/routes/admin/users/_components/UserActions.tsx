import { useNavigate } from '@tanstack/react-router'
import type { UseMutationResult } from '@tanstack/react-query'
import {
  Loader2,
  LogOut,
  MoreHorizontal,
  Pause,
  Pencil,
  Play,
  Send,
  ShieldCheck,
  Trash2,
  UserX,
} from 'lucide-react'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import { Tooltip } from '@/components/ui/tooltip'
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
  onConfirmOffboard: (userId: string) => void
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
  onConfirmOffboard,
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
      <div className="flex items-start justify-end gap-2 mt-px">
        {user.invite_pending && (
          <Tooltip label={m.admin_users_resend_invite()}>
            <button
              disabled={isResending}
              onClick={() => resendInviteMutation.mutate(user)}
              aria-label={m.admin_users_resend_invite()}
              className="inline-flex items-center justify-center text-[var(--color-accent)] transition-opacity hover:opacity-70 disabled:opacity-40"
            >
              {isResending
                ? <Loader2 className="h-4 w-4 animate-spin" />
                : <Send className="h-4 w-4" />
              }
            </button>
          </Tooltip>
        )}
        <Tooltip label={m.admin_users_edit()}>
          <button
            onClick={() =>
              navigate({
                to: '/admin/users/$userId/edit',
                params: { userId: user.zitadel_user_id },
              })
            }
            aria-label={m.admin_users_edit()}
            className="inline-flex items-center justify-center text-[var(--color-warning)] transition-opacity hover:opacity-70"
          >
            <Pencil className="h-4 w-4" />
          </button>
        </Tooltip>
        {user.invite_pending ? (
          <Tooltip label={m.admin_users_delete()}>
            <button
              onClick={() => onConfirmDelete(user.zitadel_user_id)}
              aria-label={m.admin_users_delete()}
              className="inline-flex items-center justify-center text-[var(--color-destructive)] transition-opacity hover:opacity-70"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </Tooltip>
        ) : (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                aria-label={m.admin_users_col_actions()}
                className="inline-flex items-center justify-center rounded text-gray-400 transition-colors hover:bg-[var(--color-hover)]"
              >
                <MoreHorizontal className="h-4 w-4" />
              </button>
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
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    onClick={() => onConfirmOffboard(user.zitadel_user_id)}
                    className="text-[var(--color-destructive)]"
                  >
                    <UserX className="mr-2 h-4 w-4" />
                    {m.admin_users_action_offboard()}
                  </DropdownMenuItem>
                </>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>
    </InlineDeleteConfirm>
  )
}
