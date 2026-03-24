import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useAuth } from 'react-oidc-context'
import { toast } from 'sonner'
import { API_BASE } from '@/lib/api'
import * as m from '@/paraglide/messages'

function useLifecycleMutation(
  action: 'suspend' | 'reactivate' | 'offboard',
  successMessage: () => string,
) {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (userId: string) => {
      const res = await fetch(`${API_BASE}/api/admin/users/${userId}/${action}`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) {
        const body = await res.json().catch(() => null)
        const message = body?.detail ?? body?.message ?? m.admin_users_error_generic()
        if (res.status === 409) {
          throw new Error(message as string)
        }
        throw new Error(message as string)
      }
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
