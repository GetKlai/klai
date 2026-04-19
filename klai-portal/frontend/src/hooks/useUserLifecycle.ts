import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { apiFetch } from '@/lib/apiFetch'
import * as m from '@/paraglide/messages'

function useLifecycleMutation(
  action: 'suspend' | 'reactivate' | 'offboard',
  successMessage: () => string,
) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (userId: string) => {
      await apiFetch(`/api/admin/users/${userId}/${action}`, {
        method: 'POST',
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      toast.success(successMessage())
    },
    onError: (error: Error) => {
      toast.error(error.message)
    },
  })
}

export function useSuspendUser() {
  return useLifecycleMutation('suspend', () => m.admin_users_toast_suspended())
}

export function useReactivateUser() {
  return useLifecycleMutation('reactivate', () => m.admin_users_toast_reactivated())
}

export function useOffboardUser() {
  return useLifecycleMutation('offboard', () => m.admin_users_toast_offboarded())
}
