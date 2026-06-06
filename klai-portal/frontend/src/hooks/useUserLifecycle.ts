import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { apiFetch } from '@/lib/apiFetch'
import * as m from '@/paraglide/messages'

function useLifecycleMutation(
  action: 'suspend' | 'reactivate',
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

/**
 * SPEC-PORTAL-KB-OWNERSHIP-001 Phase 3: offboard now requires a body
 * with kb_dispositions (one per KB returned by the offboard-preview).
 * The simple confirm-dialog wrapper is gone; callers pass the full
 * disposition array via the OffboardWizard component.
 */
export interface KbDisposition {
  kb_id: number
  action: 'transfer' | 'delete'
  transfer_to?: string
}

export interface OffboardArgs {
  userId: string
  kb_dispositions: KbDisposition[]
}

export function useOffboardUser() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ userId, kb_dispositions }: OffboardArgs) => {
      await apiFetch(`/api/admin/users/${userId}/offboard`, {
        method: 'POST',
        body: JSON.stringify({ kb_dispositions }),
        headers: { 'Content-Type': 'application/json' },
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      toast.success(m.admin_users_toast_offboarded())
    },
    onError: (error: Error) => {
      toast.error(error.message)
    },
  })
}

export function useDeleteUserWithDispositions() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ userId, kb_dispositions }: OffboardArgs) => {
      await apiFetch(`/api/admin/users/${userId}/delete`, {
        method: 'POST',
        body: JSON.stringify({ kb_dispositions }),
        headers: { 'Content-Type': 'application/json' },
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      toast.success(m.admin_users_toast_deleted())
    },
    onError: (error: Error) => {
      toast.error(error.message)
    },
  })
}
