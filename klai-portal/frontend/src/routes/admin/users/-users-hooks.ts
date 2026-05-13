import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@/lib/auth'
import { apiFetch } from '@/lib/apiFetch'
import { adminLogger } from '@/lib/logger'
import type { ProfileRole } from '@/lib/profiles'
import * as m from '@/paraglide/messages'
import { cleanErrorMessage } from '../_components/errors'
import type { AdminUser, AdminUsersResponse } from './-users-types'

export const adminUsersQueryKey = ['admin-users'] as const

export function useAdminUsers() {
  const auth = useAuth()

  return useQuery({
    queryKey: adminUsersQueryKey,
    queryFn: async () => apiFetch<AdminUsersResponse>(`/api/admin/users`),
    enabled: auth.isAuthenticated,
  })
}

export function useResendInviteMutation() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (user: AdminUser) => {
      await apiFetch(`/api/admin/users/${user.zitadel_user_id}/resend-invite`, {
        method: 'POST',
      })
    },
    onSuccess: (_data, user) => {
      adminLogger.info('Invite resent', { userId: user.zitadel_user_id, email: user.email })
      void queryClient.invalidateQueries({ queryKey: adminUsersQueryKey })
    },
  })
}

export function useDeleteUserMutation() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (user: AdminUser) => {
      await apiFetch(`/api/admin/users/${user.zitadel_user_id}`, { method: 'DELETE' })
    },
    onSuccess: (_data, user) => {
      adminLogger.info('User deleted', { userId: user.zitadel_user_id, email: user.email })
      void queryClient.invalidateQueries({ queryKey: adminUsersQueryKey })
    },
  })
}

export function useChangeProfileMutation() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async ({ userId, role }: { userId: string; role: ProfileRole }) => {
      await apiFetch(`/api/admin/users/${userId}/role`, {
        method: 'PATCH',
        body: JSON.stringify({ role }),
      })
    },
    onSuccess: (_data, vars) => {
      adminLogger.info('Profile changed', { userId: vars.userId, role: vars.role })
      void queryClient.invalidateQueries({ queryKey: adminUsersQueryKey })
    },
  })
}

export function useLeaveWorkspaceMutation() {
  return useMutation({
    mutationFn: async () => {
      await apiFetch(`/api/admin/users/me`, { method: 'DELETE' })
    },
    onSuccess: () => {
      adminLogger.info('Left workspace')
      window.location.href = '/'
    },
  })
}

interface AdminUsersMutationErrors {
  deleteError: unknown
  resendInviteError: unknown
  changeProfileError: unknown
  leaveWorkspaceError: unknown
}

export function adminUsersMutationError({
  deleteError,
  resendInviteError,
  changeProfileError,
  leaveWorkspaceError,
}: AdminUsersMutationErrors): string | null {
  return (
    (deleteError ? cleanErrorMessage(deleteError, m.admin_users_error_delete_generic()) : null) ??
    (resendInviteError
      ? cleanErrorMessage(resendInviteError, m.admin_users_error_resend_invite_generic())
      : null) ??
    (changeProfileError ? cleanErrorMessage(changeProfileError, m.admin_profiles_error_change()) : null) ??
    (leaveWorkspaceError
      ? cleanErrorMessage(leaveWorkspaceError, m.admin_users_error_leave_workspace())
      : null)
  )
}
