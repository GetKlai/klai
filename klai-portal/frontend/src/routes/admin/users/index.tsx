import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  createColumnHelper,
} from '@tanstack/react-table'
import { useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
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
import { Trash2, Send, Loader2, Pencil, MoreHorizontal, Pause, Play, UserX, LogOut, ShieldCheck } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { getLocale } from '@/paraglide/runtime'
import { datetime, plural } from '@/paraglide/registry'
import { apiFetch } from '@/lib/apiFetch'
import { adminLogger } from '@/lib/logger'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { useSuspendUser, useReactivateUser, useOffboardUser } from '@/hooks/useUserLifecycle'
import { PROFILE_LADDER, type ProfileRole } from '@/lib/profiles'

export const Route = createFileRoute('/admin/users/')({
  component: UsersPage,
})

type UserStatus = 'active' | 'suspended' | 'offboarded'

interface User {
  zitadel_user_id: string
  email: string
  first_name: string
  last_name: string
  role: ProfileRole
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

function profileLabel(role: ProfileRole): string {
  const msgs = m as unknown as Record<string, (() => string) | undefined>
  const labelFn = msgs[`profile_${role}_label`]
  return labelFn ? labelFn() : role
}

function ProfileBadge({ role, pending }: { role: ProfileRole; pending?: boolean }) {
  const variant = role === 'admin' ? 'accent' : 'secondary'
  if (pending) {
    return <Badge variant="warning">{profileLabel(role)}</Badge>
  }
  return <Badge variant={variant}>{profileLabel(role)}</Badge>
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

const columnHelper = createColumnHelper<User>()

function UsersPage() {
  const auth = useAuth()
  const currentUserId = auth.user?.profile?.sub
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null)
  const [confirmingOffboardId, setConfirmingOffboardId] = useState<string | null>(null)
  // R6: confirm dialog for "Leave workspace" (self-removal)
  const [confirmingLeave, setConfirmingLeave] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')

  const suspendMutation = useSuspendUser()
  const reactivateMutation = useReactivateUser()
  const offboardMutation = useOffboardUser()

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['admin-users'],
    queryFn: async () => apiFetch<{ users: User[] }>(`/api/admin/users`),
    enabled: auth.isAuthenticated,
  })

  const users = useMemo(() => data?.users ?? [], [data])

  const filteredUsers = useMemo(() => {
    const query = searchQuery.trim().toLowerCase()
    if (!query) return users
    return users.filter((user) => {
      const fullName = `${user.first_name} ${user.last_name}`.toLowerCase()
      return fullName.includes(query) || user.email.toLowerCase().includes(query)
    })
  }, [users, searchQuery])

  const resendInviteMutation = useMutation({
    mutationFn: async (user: User) => {
      await apiFetch(`/api/admin/users/${user.zitadel_user_id}/resend-invite`, { method: 'POST' })
    },
    onSuccess: (_data, user) => {
      adminLogger.info('Invite resent', { userId: user.zitadel_user_id, email: user.email })
      void queryClient.invalidateQueries({ queryKey: ['admin-users'] })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: async (user: User) => {
      await apiFetch(`/api/admin/users/${user.zitadel_user_id}`, { method: 'DELETE' })
    },
    onSuccess: (_data, user) => {
      adminLogger.info('User deleted', { userId: user.zitadel_user_id, email: user.email })
      void queryClient.invalidateQueries({ queryKey: ['admin-users'] })
    },
  })

  // SPEC-PORTAL-ADMIN-UI-001 REQ-2: unified change-profile via PATCH /role.
  // Replaces legacy promote-admin / demote-admin endpoints in the UI.
  const changeProfileMutation = useMutation({
    mutationFn: async ({ userId, role }: { userId: string; role: ProfileRole }) => {
      await apiFetch(`/api/admin/users/${userId}/role`, {
        method: 'PATCH',
        body: JSON.stringify({ role }),
      })
    },
    onSuccess: (_data, vars) => {
      adminLogger.info('Profile changed', { userId: vars.userId, role: vars.role })
      void queryClient.invalidateQueries({ queryKey: ['admin-users'] })
    },
  })

  // R6: leave workspace (self-removal)
  const leaveWorkspaceMutation = useMutation({
    mutationFn: async () => {
      await apiFetch(`/api/admin/users/me`, { method: 'DELETE' })
    },
    onSuccess: () => {
      adminLogger.info('Left workspace')
      window.location.href = '/'
    },
  })

  const mutationError =
    (deleteMutation.error instanceof Error ? deleteMutation.error.message : deleteMutation.error ? m.admin_users_error_delete_generic() : null) ??
    (resendInviteMutation.error instanceof Error ? resendInviteMutation.error.message : resendInviteMutation.error ? m.admin_users_error_resend_invite_generic() : null) ??
    (changeProfileMutation.error instanceof Error ? changeProfileMutation.error.message : changeProfileMutation.error ? m.admin_profiles_error_change() : null) ??
    (leaveWorkspaceMutation.error instanceof Error ? leaveWorkspaceMutation.error.message : leaveWorkspaceMutation.error ? m.admin_users_error_leave_workspace() : null)

  // SPEC-PORTAL-ADMIN-UI-001 REQ-1: columns Name | Email | Profile | Status | Last active | Actions.
  // "Last active" rendered from created_at (Invited date) — backend has no
  // last_active_at field; rename is a future SPEC. Label uses col_invited so
  // the data we display matches the data we have.
  const columns = [
    columnHelper.accessor((row) => `${row.first_name} ${row.last_name}`, {
      id: 'name',
      header: () => m.admin_users_col_name(),
      cell: (info) => info.getValue(),
    }),
    columnHelper.accessor('email', {
      header: () => m.admin_users_col_email(),
      cell: (info) => info.getValue(),
    }),
    columnHelper.accessor('role', {
      header: () => m.admin_users_field_profile(),
      cell: (info) => (
        <ProfileBadge role={info.getValue()} pending={info.row.original.invite_pending} />
      ),
    }),
    columnHelper.accessor('status', {
      header: () => m.admin_users_col_status(),
      cell: (info) => <StatusBadge status={info.getValue()} />,
    }),
    columnHelper.accessor('created_at', {
      header: () => m.admin_users_col_invited(),
      cell: (info) => formatDate(info.getValue()),
    }),
    columnHelper.display({
      id: 'actions',
      header: () => m.admin_users_col_actions(),
      cell: ({ row }) => {
        const user = row.original
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
            label={m.admin_users_delete_confirm({ name: `${user.first_name} ${user.last_name}`.trim() || user.email })}
            cancelLabel={m.admin_users_cancel()}
            onConfirm={() => { setConfirmingDeleteId(null); deleteMutation.mutate(user) }}
            onCancel={() => setConfirmingDeleteId(null)}
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
                  onClick={() => navigate({ to: '/admin/users/$userId/edit', params: { userId: user.zitadel_user_id } })}
                  aria-label={m.admin_users_edit()}
                  className="inline-flex items-center justify-center text-[var(--color-warning)] transition-opacity hover:opacity-70"
                >
                  <Pencil className="h-4 w-4" />
                </button>
              </Tooltip>
              {user.invite_pending && (
                <Tooltip label={m.admin_users_delete()}>
                  <button
                    onClick={() => setConfirmingDeleteId(user.zitadel_user_id)}
                    aria-label={m.admin_users_delete()}
                    className="inline-flex items-center justify-center text-[var(--color-destructive)] transition-opacity hover:opacity-70"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </Tooltip>
              )}
              {!user.invite_pending && (
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <button
                      aria-label={m.admin_users_col_actions()}
                      className="inline-flex items-center justify-center rounded text-[var(--color-muted-foreground)] transition-colors hover:bg-[var(--color-secondary)]"
                    >
                      <MoreHorizontal className="h-4 w-4" />
                    </button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    {/* SPEC-PORTAL-ADMIN-UI-001 REQ-2: change profile submenu */}
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
                                <span className="ml-2 text-xs text-[var(--color-muted-foreground)]">
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
                        onClick={() => setConfirmingLeave(true)}
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
                          <DropdownMenuItem
                            onClick={() => suspendMutation.mutate(user.zitadel_user_id)}
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
      },
    }),
  ]

  // eslint-disable-next-line react-hooks/incompatible-library -- useReactTable returns functions that React Compiler cannot memoize safely; this is expected TanStack Table behaviour
  const table = useReactTable({
    data: filteredUsers,
    columns,
    getCoreRowModel: getCoreRowModel(),
  })

  return (
    <div className="mx-auto max-w-6xl px-6 py-10 space-y-6">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h1 className="page-title text-[26px] font-display-bold text-gray-900">
            {m.admin_users_heading()}
          </h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {!isLoading && !error && (
              plural(getLocale(), users.length) === 'one'
                ? m.admin_users_count_one()
                : m.admin_users_count_other({ count: String(users.length) })
            )}
          </p>
        </div>
        <Button size="sm" data-help-id="admin-users-invite" onClick={() => navigate({ to: '/admin/users/invite' })}>
          {m.admin_users_invite_button()}
        </Button>
      </div>

      {error ? (
        <QueryErrorState error={error instanceof Error ? error : new Error(String(error))} onRetry={() => void refetch()} />
      ) : <>
      {mutationError && (
        <p className="text-sm text-[var(--color-destructive)]">{mutationError}</p>
      )}

      {/* SPEC-PORTAL-ADMIN-UI-001 Sparring decision #5: search-input above the
          table, client-side filter on name + email. (Replaces filter chips
          from the v0.1.0 draft of REQ-3 — see PR description.) */}
      {users.length > 0 && (
        <div className="max-w-sm">
          <Input
            type="search"
            placeholder={m.admin_users_search_placeholder()}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            aria-label={m.admin_users_search_placeholder()}
          />
        </div>
      )}

      {isLoading ? (
        <p className="py-8 text-sm text-[var(--color-muted-foreground)]">
          {m.admin_users_loading()}
        </p>
      ) : users.length === 0 ? (
        <p className="py-8 text-sm text-[var(--color-muted-foreground)]">
          {m.admin_users_empty()}
        </p>
      ) : filteredUsers.length === 0 ? (
        <p className="py-8 text-sm text-[var(--color-muted-foreground)]">
          {m.admin_users_empty()}
        </p>
      ) : (
        <table data-help-id="admin-users-table" className="w-full text-sm border-t border-b border-[var(--color-border)]">
          <thead>
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id} className="border-b border-[var(--color-border)]">
                {headerGroup.headers.map((header) => (
                  <th
                    key={header.id}
                    className="py-3 pr-4 text-left text-xs font-medium text-gray-400 tracking-wide"
                  >
                    {flexRender(header.column.columnDef.header, header.getContext())}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => (
              <tr
                key={row.id}
                className="border-b border-[var(--color-border)] last:border-b-0"
              >
                {row.getVisibleCells().map((cell) => (
                  <td
                    key={cell.id}
                    className="py-4 pr-4 align-top text-[var(--color-foreground)]"
                  >
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <AlertDialog
        open={confirmingOffboardId !== null}
        onOpenChange={(open) => { if (!open) setConfirmingOffboardId(null) }}
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

      {/* R6: confirm leave workspace dialog (C6.6) */}
      <AlertDialog
        open={confirmingLeave}
        onOpenChange={(open) => { if (!open) setConfirmingLeave(false) }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{m.admin_users_confirm_leave_title()}</AlertDialogTitle>
            <AlertDialogDescription>
              {m.admin_users_confirm_leave_description()}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{m.admin_users_cancel()}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-[var(--color-destructive)] text-white hover:bg-[var(--color-destructive)]/90"
              onClick={() => {
                setConfirmingLeave(false)
                leaveWorkspaceMutation.mutate()
              }}
            >
              {m.admin_users_action_leave_workspace()}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      </>}
    </div>
  )
}
