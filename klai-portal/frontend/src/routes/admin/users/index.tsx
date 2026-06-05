import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import { PageHeader, PageIntro } from '@/components/ui/page-header'
import { Pagination } from '@/components/ui/pagination'
import { SearchInput } from '@/components/ui/search-input'
import { useListControls } from '@/components/ui/use-list-controls'
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
import { userDisplayName } from './-users-helpers'
import { UserActions } from './_components/UserActions'
import { UsersTable } from './_components/UsersTable'

export const Route = createFileRoute('/admin/users/')({
  component: UsersPage,
})

function UsersPage() {
  const navigate = useNavigate()
  const auth = useAuth()
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null)
  const [confirmingOffboardId, setConfirmingOffboardId] = useState<string | null>(null)
  const [confirmingLeave, setConfirmingLeave] = useState(false)

  const usersQuery = useAdminUsers()
  const suspendMutation = useSuspendUser()
  const reactivateMutation = useReactivateUser()
  const resendInviteMutation = useResendInviteMutation()
  const deleteMutation = useDeleteUserMutation()
  const changeProfileMutation = useChangeProfileMutation()
  const leaveWorkspaceMutation = useLeaveWorkspaceMutation()

  const currentUserId = auth.user?.profile?.sub
  const users = useMemo(() => usersQuery.data?.users ?? [], [usersQuery.data])
  const controls = useListControls(users, {
    pageSize: 10,
    filter: (u, q) => {
      const s = q.trim().toLowerCase()
      return (
        `${u.first_name} ${u.last_name}`.toLowerCase().includes(s) ||
        u.email.toLowerCase().includes(s)
      )
    },
  })

  const mutationError = adminUsersMutationError({
    deleteError: deleteMutation.error,
    resendInviteError: resendInviteMutation.error,
    changeProfileError: changeProfileMutation.error,
    leaveWorkspaceError: leaveWorkspaceMutation.error,
  })

  const offboardTarget = confirmingOffboardId
    ? users.find((user) => user.zitadel_user_id === confirmingOffboardId)
    : undefined

  return (
    <div className="mx-auto max-w-4xl px-6 pt-4 pb-10 space-y-6">
      <PageHeader
        title={m.admin_users_heading()}
        count={!usersQuery.isLoading && !usersQuery.error ? users.length : undefined}
        description={m.admin_users_subtitle()}
        actions={
          <Button
            size="sm"
            data-help-id="admin-users-invite"
            onClick={() => navigate({ to: '/admin/users/invite' })}
          >
            {m.admin_users_invite_button()}
          </Button>
        }
      />

      <PageIntro>
        <p>{m.admin_users_intro_body()}</p>
        <p>{m.admin_users_intro_lifecycle()}</p>
      </PageIntro>

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

          {controls.showSearch && (
            <div className="max-w-sm">
              <SearchInput
                type="search"
                placeholder={m.admin_users_search_placeholder()}
                value={controls.query}
                onChange={(e) => controls.setQuery(e.target.value)}
                aria-label={m.admin_users_search_placeholder()}
              />
            </div>
          )}

          {usersQuery.isLoading ? (
            <p className="py-8 text-sm text-gray-400">
              {m.admin_users_loading()}
            </p>
          ) : users.length === 0 ? (
            <p className="py-8 text-sm text-gray-400">
              {m.admin_users_empty()}
            </p>
          ) : controls.filteredCount === 0 ? (
            <p className="py-8 text-sm text-gray-400">
              {m.list_no_results()}
            </p>
          ) : (
            <UsersTable
              users={controls.pageItems}
              onRowClick={(user) =>
                void navigate({
                  to: '/admin/users/$userId/edit',
                  params: { userId: user.zitadel_user_id },
                })
              }
              renderActions={(user) => (
                <UserActions
                  user={user}
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
              )}
            />
          )}

          {controls.showPagination && (
            <Pagination
              page={controls.page}
              pageCount={controls.pageCount}
              onPageChange={controls.setPage}
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
