// TanStack Query hooks for admin integrations
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useAuth } from 'react-oidc-context'
import { apiFetch } from '@/lib/apiFetch'
import type {
  IntegrationResponse,
  IntegrationDetailResponse,
  CreateIntegrationRequest,
  CreateIntegrationResponse,
  UpdateIntegrationRequest,
  OrgKnowledgeBase,
} from './-types'

export function useIntegrations() {
  const auth = useAuth()
  const token = auth.user?.access_token

  return useQuery({
    queryKey: ['admin-integrations'],
    queryFn: async () =>
      apiFetch<IntegrationResponse[]>('/api/integrations', token),
    enabled: !!token,
  })
}

export function useIntegration(id: string) {
  const auth = useAuth()
  const token = auth.user?.access_token

  return useQuery({
    queryKey: ['admin-integration', id],
    queryFn: async () =>
      apiFetch<IntegrationDetailResponse>(`/api/integrations/${id}`, token),
    enabled: !!token && !!id,
  })
}

export function useCreateIntegration() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (data: CreateIntegrationRequest) =>
      apiFetch<CreateIntegrationResponse>('/api/integrations', token, {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-integrations'] })
    },
  })
}

export function useUpdateIntegration(id: string) {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (data: UpdateIntegrationRequest) =>
      apiFetch<IntegrationDetailResponse>(`/api/integrations/${id}`, token, {
        method: 'PATCH',
        body: JSON.stringify(data),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-integrations'] })
      void queryClient.invalidateQueries({ queryKey: ['admin-integration', id] })
    },
  })
}

export function useRevokeIntegration(id: string) {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async () =>
      apiFetch<IntegrationDetailResponse>(`/api/integrations/${id}/revoke`, token, {
        method: 'POST',
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-integrations'] })
      void queryClient.invalidateQueries({ queryKey: ['admin-integration', id] })
    },
  })
}

export function useDeleteIntegration() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (id: string) =>
      apiFetch<void>(`/api/integrations/${id}`, token, {
        method: 'DELETE',
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-integrations'] })
    },
  })
}

export function useOrgKnowledgeBases() {
  const auth = useAuth()
  const token = auth.user?.access_token

  return useQuery({
    queryKey: ['app-knowledge-bases-for-integrations'],
    queryFn: async () =>
      apiFetch<{ knowledge_bases: OrgKnowledgeBase[] }>(
        '/api/app/knowledge-bases?owner_type=org',
        token,
      ),
    enabled: !!token,
  })
}
