import { useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '@/lib/apiFetch'
import { kbQueryKeys } from '@/lib/kb-query-keys'
import type { KnowledgeBase } from './-kb-types'

export const kbMembersQueryKey = (kbSlug: string) => ['kb-members', kbSlug] as const

interface UpdateKnowledgeBaseBody {
  visibility?: string
  default_org_role?: string
}

export function useKnowledgeBaseUpdate(kbSlug: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (body: UpdateKnowledgeBaseBody) =>
      apiFetch<KnowledgeBase>(`/api/app/knowledge-bases/${kbSlug}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: kbQueryKeys.knowledgeBase(kbSlug) })
    },
  })
}

export function useInviteUser(kbSlug: string, onInvited?: () => void) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async ({ email, role }: { email: string; role: string }) => {
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/members/users`, {
        method: 'POST',
        body: JSON.stringify({ email, role }),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: kbMembersQueryKey(kbSlug) })
      onInvited?.()
    },
  })
}

export function useInviteGroup(kbSlug: string, onInvited?: () => void) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async ({ groupId, role }: { groupId: number; role: string }) => {
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/members/groups`, {
        method: 'POST',
        body: JSON.stringify({ group_id: groupId, role }),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: kbMembersQueryKey(kbSlug) })
      onInvited?.()
    },
  })
}

export function useRemoveUser(kbSlug: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (id: number) => {
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/members/users/${id}`, {
        method: 'DELETE',
      })
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: kbMembersQueryKey(kbSlug) }),
  })
}

export function useRemoveGroup(kbSlug: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (id: number) => {
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/members/groups/${id}`, {
        method: 'DELETE',
      })
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: kbMembersQueryKey(kbSlug) }),
  })
}
