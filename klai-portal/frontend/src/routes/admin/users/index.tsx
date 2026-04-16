import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import { Tooltip } from '@/components/ui/tooltip'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import {
  Trash2,
  Send,
  Loader2,
  Pencil,
  MoreHorizontal,
  Pause,
  Play,
  UserX,
  Plus,
  User as UserIcon,
  Users as UsersIcon,
} from 'lucide-react'
import * as m from '@/paraglide/messages'
import { getLocale } from '@/paraglide/runtime'
import { datetime, plural } from '@/paraglide/registry'
import { apiFetch } from '@/lib/apiFetch'
import { adminLogger } from '@/lib/logger'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { useSuspendUser, useReactivateUser, useOffboardUser } from '@/hooks/useUserLifecycle'

export const Route = createFileRoute('/admin/users/')({
  component: UsersPage,
})

type Role = 'admin' | 'member'
type UserStatus = 'active' | 'suspended' | 'offboarded'

interface User {
  zitadel_user_id: string
  email: string
  first_name: string
  last_name: string
  role: Role
  status: UserStatus
  preferred_language: 'nl' | 'en'
  created_at: string
  invite_pending: boolean
}

function formatDate(isoString: string): string {
  return datetime(getLocale(), isoString, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

function RoleBadge({ role, pending }: { role: Role; pending?: boolean }) {
  if (role === 'admin') return <Badge variant="accent">{m.admin_users_role_admin()}</Badge>
  if (pending) return <Badge variant="warning">{m.admin_users_role_member_pending()}</Badge>
  return <Badge variant="secondary">{m.admin_users_role_member()}</Badge>
}

function StatusBadge({ status }: { status: UserStatus }) {
  switch (status) {
    case 'suspended':
      return <Badge variant="warning">{m.admin_users_status_suspended()}</Badge>
    case 'offboarded':
      return <Badge variant="destructive">{m.admin_users_status_offboarded()}</Badge>
    default:
      return <Badge variant="success">{m.admin_users_status_active()}</Badge>
  }
}

function UsersPage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const currentUserId = auth.user?.profile?.sub
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null)
  const [confirmingOffboardId, setConfirmingOffboardId] = useState<string | null>(null)

  const suspendMutation = useSuspendUser()
  const reactivateMutation = useReactivateUser()
  const offboardMutation = useOffboardUser()

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['admin-users'],
    queryFn: async () => apiFetch<{ users: User[] }>(`/api/admin/users`, token),
    enabled: !!token,
  })

  const users = data?.users ?? []

  const { data: membershipsData } = useQuery({
    queryKey: ['admin-group-memberships'],
    queryFn: async () =>
      apiFetch<{ memberships: Record<string, { id: number; name: string; products: string[] }[]> }>(
        `/api/admin/group-memberships`,
        token,
      ),
    enabled: !!token,
  })

  const membershipsByUser = membershipsData?.memberships ?? {}

  const resendInviteMutation = useMutation({
    mutationFn: async (user: User) => {
      await apiFetch(`/api/admin/users/${user.zitadel_user_id}/resend-invite`, token, {
        method: 'POST',
      })
    },
    onSuccess: (_data, user) => {
      adminLogger.info('Invite resent', { userId: user.zitadel_user_id, email: user.email })
      void queryClient.invalidateQueries({ queryKey: ['admin-users'] })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: async (user: User) => {
      await apiFetch(`/api/admin/users/${user.zitadel_user_id}`, token, { method: 'DELETE' })
    },
    onSuccess: (_data, user) => {
      adminLogger.info('User deleted', { userId: user.zitadel_user_id, email: user.email })
      void queryClient.invalidateQueries({ queryKey: ['admin-users'] })
    },
  })

  const mutationError =
    (deleteMutation.error instanceof Error
      ? deleteMutation.error.message
      : deleteMutation.error
        ? m.admin_users_error_delete_generic()
        : null) ??
    (resendInviteMutation.error instanceof Error
      ? resendInviteMutation.error.message
      : resendInviteMutation.error
        ? m.admin_users_error_resend_invite_generic()
        : null)

  const subtitle =
    isLoading || error
      ? ''
      : plural(getLocale(), users.length) === 'one'
        ? m.admin_users_count_one()
        : m.admin_users_count_other({ count: String(users.length) })

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <h1 className="text-[26px] font-display-bold text-gray-900">{m.admin_users_heading()}</h1>
        <button
          type="button"
          data-help-id="admin-users-invite"
          onClick={() => navigate({ to: '/admin/users/invite' })}
          className="flex items-center gap-1.5 rounded-lg bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800 transition-colors"
        >
          <Plus className="h-4 w-4" />
          {m.admin_users_invite_button()}
        </button>
      </div>
      {subtitle && <p className="text-sm text-gray-400 mb-6">{subtitle}</p>}

      {mutationError && (
        <p className="mb-4 text-sm text-[var(--color-destructive)]">{mutationError}</p>
      )}

      {error ? (
        <QueryErrorState
          error={error instanceof Error ? error : new Error(String(error))}
          onRetry={() => void refetch()}
        />
      ) : isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-14 rounded-lg bg-gray-50 animate-pulse" />
          ))}
        </div>
      ) : users.length === 0 ? (
        <div className="rounded-lg border border-dashed border-gray-200 py-16 text-center">
          <UsersIcon className="h-10 w-10 text-gray-300 mx-auto mb-3" />
          <p className="text-sm text-gray-400">{m.admin_users_empty()}</p>
        </div>
      ) : (
        <div className="divide-y divide-gray-200 border-t border-b border-gray-200" data-help-id="admin-users-table">
          {users.map((user) => {
            const isSelf = user.zitadel_user_id === currentUserId
            const groups = membershipsByUser[user.zitadel_user_id] ?? []
            const fullName = `${user.first_name} ${user.last_name}`.trim() || user.email
            const isResending =
              resendInviteMutation.isPending &&
              resendInviteMutation.variables?.zitadel_user_id === user.zitadel_user_id
            const isConfirmingDelete = confirmingDeleteId === user.zitadel_user_id
            const isDeleting =
              deleteMutation.isPending &&
              deleteMutation.variables?.zitadel_user_id === user.zitadel_user_id

            return (
              <div
                key={user.zitadel_user_id}
                className="flex items-center gap-3 px-2 py-3.5 hover:bg-gray-50 transition-colors"
              >
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gray-50">
                  <UserIcon size={16} strokeWidth={1.75} className="text-gray-400" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-gray-900 truncate">{fullName}</span>
                    <RoleBadge role={user.role} pending={user.invite_pending} />
                    {user.status !== 'active' && <StatusBadge status={user.status} />}
                  </div>
                  <div className="flex items-center gap-2 text-xs text-gray-400 truncate">
                    <span className="truncate">{user.email}</span>
                    {groups.length > 0 && (
                      <>
                        <span>·</span>
                        <span className="truncate">{groups.map((g) => g.name).join(', ')}</span>
                      </>
                    )}
                    <span>·</span>
                    <span className="whitespace-nowrap">{formatDate(user.created_at)}</span>
                  </div>
                </div>
                <InlineDeleteConfirm
                  isConfirming={isConfirmingDelete}
                  isPending={isDeleting}
                  label={m.admin_users_delete_confirm({ name: fullName })}
                  cancelLabel={m.admin_users_cancel()}
                  onConfirm={() => {
                    setConfirmingDeleteId(null)
                    deleteMutation.mutate(user)
                  }}
                  onCancel={() => setConfirmingDeleteId(null)}
                >
                  <div className="flex items-center gap-1 shrink-0">
                    {user.invite_pending && (
                      <Tooltip label={m.admin_users_resend_invite()}>
                        <button
                          type="button"
                          disabled={isResending}
                          onClick={() => resendInviteMutation.mutate(user)}
                          aria-label={m.admin_users_resend_invite()}
                          className="rounded-lg p-1.5 text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-colors disabled:opacity-40"
                        >
                          {isResending ? (
                            <Loader2 size={14} className="animate-spin" />
                          ) : (
                            <Send size={14} />
                          )}
                        </button>
                      </Tooltip>
                    )}
                    <Tooltip label={m.admin_users_edit()}>
                      <button
                        type="button"
                        onClick={() =>
                          navigate({
                            to: '/admin/users/$userId/edit',
                            params: { userId: user.zitadel_user_id },
                          })
                        }
                        aria-label={m.admin_users_edit()}
                        className="rounded-lg p-1.5 text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-colors"
                      >
                        <Pencil size={14} />
                      </button>
                    </Tooltip>
                    {user.invite_pending ? (
                      <Tooltip label={m.admin_users_delete()}>
                        <button
                          type="button"
                          onClick={() => setConfirmingDeleteId(user.zitadel_user_id)}
                          aria-label={m.admin_users_delete()}
                          className="rounded-lg p-1.5 text-gray-400 hover:text-[var(--color-destructive)] hover:bg-gray-100 transition-colors"
                        >
                          <Trash2 size={14} />
                        </button>
                      </Tooltip>
                    ) : (
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <button
                            type="button"
                            aria-label={m.admin_users_col_actions()}
                            className="rounded-lg p-1.5 text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-colors"
                          >
                            <MoreHorizontal size={14} />
                          </button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          {user.status === 'active' && (
                            <DropdownMenuItem
                              onClick={() => suspendMutation.mutate(user.zitadel_user_id)}
                              disabled={isSelf}
                            >
                              <Pause className="mr-2 h-4 w-4" />
                              {m.admin_users_action_suspend()}
                            </DropdownMenuItem>
                          )}
                          {user.status === 'suspended' && (
                            <DropdownMenuItem
                              onClick={() => reactivateMutation.mutate(user.zitadel_user_id)}
                            >
                              <Play className="mr-2 h-4 w-4" />
                              {m.admin_users_action_reactivate()}
                            </DropdownMenuItem>
                          )}
                          <DropdownMenuSeparator />
                          <DropdownMenuItem
                            onClick={() => setConfirmingOffboardId(user.zitadel_user_id)}
                            disabled={isSelf}
                            className="text-[var(--color-destructive)]"
                          >
                            <UserX className="mr-2 h-4 w-4" />
                            {m.admin_users_action_offboard()}
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    )}
                  </div>
                </InlineDeleteConfirm>
              </div>
            )
          })}
        </div>
      )}

      <AlertDialog
        open={confirmingOffboardId !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmingOffboardId(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{m.admin_users_confirm_offboard_title()}</AlertDialogTitle>
            <AlertDialogDescription>
              {m.admin_users_confirm_offboard_description()}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{m.admin_users_cancel()}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-[var(--color-destructive)] text-white hover:bg-[var(--color-destructive)]/90"
              onClick={() => {
                if (confirmingOffboardId) {
                  offboardMutation.mutate(confirmingOffboardId)
                }
                setConfirmingOffboardId(null)
              }}
            >
              {m.admin_users_action_offboard()}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
