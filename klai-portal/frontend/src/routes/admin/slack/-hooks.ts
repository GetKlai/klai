// TanStack Query hooks for the admin Slack page (SPEC-SLACK-001 §7.1)
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@/lib/auth'
import { apiFetch, ApiError } from '@/lib/apiFetch'
import type {
  CreateSlackKeyRequest,
  CreateSlackKeyResponse,
  DisconnectSlackResponse,
  SlackStatusResponse,
  UpdateSlackKbsRequest,
} from './-types'

const SLACK_KEY = ['admin-slack'] as const
const POLL_MS = 10_000

/** 403 on GET means the tenant has no `slack` unlock; the page shows the locked state. */
export function isSlackLockedError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403
}

export function useSlackStatus() {
  const auth = useAuth()

  return useQuery({
    queryKey: SLACK_KEY,
    queryFn: async () => apiFetch<SlackStatusResponse>('/api/admin/slack'),
    enabled: auth.isAuthenticated,
    retry: (failureCount, error) => !isSlackLockedError(error) && failureCount < 2,
    // Poll only while the admin has to paste the key into Slack.
    refetchInterval: (query) =>
      query.state.data?.status === 'awaiting_link' ? POLL_MS : false,
  })
}

export function useCreateSlackKey() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (data: CreateSlackKeyRequest) =>
      apiFetch<CreateSlackKeyResponse>('/api/admin/slack/key', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: SLACK_KEY })
    },
  })
}

export function useUpdateSlackKbs() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (data: UpdateSlackKbsRequest) =>
      apiFetch<SlackStatusResponse>('/api/admin/slack', {
        method: 'PATCH',
        body: JSON.stringify(data),
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(SLACK_KEY, data)
    },
  })
}

export function useDisconnectSlack() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async () =>
      apiFetch<DisconnectSlackResponse>('/api/admin/slack', { method: 'DELETE' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: SLACK_KEY })
    },
  })
}
