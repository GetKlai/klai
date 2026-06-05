import { createFileRoute, useNavigate } from '@tanstack/react-router'
import {
  useReactTable,
  getCoreRowModel,
  createColumnHelper,
} from '@tanstack/react-table'
import { useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import { SearchInput } from '@/components/ui/search-input'
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
import * as m from '@/paraglide/messages'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { useAuth } from '@/lib/auth'
import { useSuspendUser, useReactivateUser } from '@/hooks/useUserLifecycle'
import { OffboardWizard } from '@/components/admin/offboard-wizard'
import {
  adminUsersMutationError,
  useAdminUsers,
  useChangeProfileMutation,
  useDeleteUserMutation,
  useLeaveWorkspaceMutation,
  useResendInviteMutation,
} from './-users-hooks'
import {
  filterUsers,
  formatDate,
  userCountLabel,
  userDisplayName,
} from './-users-helpers'
import type { AdminUser } from './-users-types'
import { AccountTypeBadge, ProfileBadge, StatusBadge } from './_components/UserBadges'
import { UserActions } from './_components/UserActions'
import { UsersTable } from './_components/UsersTable'

export const Route = createFileRoute('/admin/users/')({
  component: UsersPage,
})

const columnHelper = createColumnHelper<AdminUser>()

function UsersPage() {
  const navigate = useNavigate()
  const auth = useAuth()
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null)
  const [confirmingOffboardId, setConfirmingOffboardId] = useState<string | null>(null)
  const [confirmingLeave, setConfirmingLeave] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')

  const usersQuery = useAdminUsers()
  const suspendMutation = useSuspendUser()
  const reactivateMutation = useReactivateUser()
  const resendInviteMutation = useResendInviteMutation()
  const deleteMutation = useDeleteUserMutation()
  const changeProfileMutation = useChangeProfileMutation()
  const leaveWorkspaceMutation = useLeaveWorkspaceMutation()

  const currentUserId = auth.user?.profile?.sub
  const users = useMemo(() => usersQuery.data?.users ?? [], [usersQuery.data])
  const filteredUsers = useMemo(() => filterUsers(users, searchQuery), [users, searchQuery])

  const mutationError = adminUsersMutationError({
    deleteError: deleteMutation.error,
    resendInviteError: resendInviteMutation.error,
    changeProfileError: changeProfileMutation.error,
    leaveWorkspaceError: leaveWorkspaceMutation.error,
  })

  // SPEC-PORTAL-ADMIN-UI-001 REQ-1: columns Name | Email | Profile | Status | Last active | Actions.
  // "Last active" still renders from created_at (Invited date); backend has no last_active_at field.
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
    columnHelper.accessor('seat_type', {
      header: () => m.admin_users_col_account_type(),
      cell: (info) => <AccountTypeBadge seat={info.getValue()} />,
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
      cell: ({ row }) => (
        <UserActions
          user={row.original}
          currentUserId={currentUserId}
          confirmingDeleteId={confirmingDeleteId}
          resendInviteMutation={resendInviteMutation}
          deleteMutation={deleteMutation}
          changeProfileMutation={changeProfileMutation}
          suspendMutation={suspendMutation}
          reactivateMutation={reactivateMutation}
          onConfirmDelete={setConfirmingDeleteId}
          onConfirmOffboard={setConfirmingOffboardId}
          onConfirmLeave={() => setConfirmingLeave(true)}
        />
      ),
    }),
  ]

  // eslint-disable-next-line react-hooks/incompatible-library -- useReactTable returns functions that React Compiler cannot memoize safely; this is expected TanStack Table behaviour
  const table = useReactTable({
    data: filteredUsers,
    columns,
    getCoreRowModel: getCoreRowModel(),
  })

  const offboardTarget = confirmingOffboardId
    ? users.find((user) => user.zitadel_user_id === confirmingOffboardId)
    : undefined

  return (
    <div className="mx-auto max-w-3xl px-6 pt-4 pb-10 space-y-6">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h1 className="page-title text-[26px] font-display-bold text-gray-900">
            {m.admin_users_heading()}
          </h1>
          <p className="text-sm text-gray-400">
            {!usersQuery.isLoading && !usersQuery.error && userCountLabel(users.length)}
          </p>
        </div>
        <Button
          size="sm"
          data-help-id="admin-users-invite"
          onClick={() => navigate({ to: '/admin/users/invite' })}
        >
          {m.admin_users_invite_button()}
        </Button>
      </div>

      {usersQuery.error ? (
        <QueryErrorState
          error={usersQuery.error instanceof Error ? usersQuery.error : new Error(String(usersQuery.error))}
          onRetry={() => void usersQuery.refetch()}
        />
      ) : (
        <>
          {mutationError && (
            <p className="text-sm text-[var(--color-destructive)]">{mutationError}</p>
          )}

          {users.length > 0 && (
            <div className="max-w-sm">
              <SearchInput
                type="search"
                placeholder={m.admin_users_search_placeholder()}
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                aria-label={m.admin_users_search_placeholder()}
              />
            </div>
          )}

          {usersQuery.isLoading ? (
            <p className="py-8 text-sm text-gray-400">
              {m.admin_users_loading()}
            </p>
          ) : users.length === 0 || filteredUsers.length === 0 ? (
            <p className="py-8 text-sm text-gray-400">
              {m.admin_users_empty()}
            </p>
          ) : (
            <UsersTable
              table={table}
              onRowClick={(user) =>
                void navigate({
                  to: '/admin/users/$userId/edit',
                  params: { userId: user.zitadel_user_id },
                })
              }
            />
          )}

          {offboardTarget && currentUserId && (
            <OffboardWizard
              userId={offboardTarget.zitadel_user_id}
              userLabel={userDisplayName(offboardTarget)}
              currentAdminId={currentUserId}
              open={confirmingOffboardId !== null}
              onOpenChange={(open) => {
                if (!open) setConfirmingOffboardId(null)
              }}
            />
          )}

          <AlertDialog
            open={confirmingLeave}
            onOpenChange={(open) => {
              if (!open) setConfirmingLeave(false)
            }}
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
                  variant="destructive"
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
        </>
      )}
    </div>
  )
}
