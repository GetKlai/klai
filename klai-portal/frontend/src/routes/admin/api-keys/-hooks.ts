// TanStack Query hooks for admin API keys (SPEC-WIDGET-002)
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@/lib/auth'
import { apiFetch } from '@/lib/apiFetch'
import type {
  ApiKeyResponse,
  ApiKeyDetailResponse,
  CreateApiKeyRequest,
  CreateApiKeyResponse,
  UpdateApiKeyRequest,
  OrgKnowledgeBase,
} from './-types'

export function useApiKeys() {
  const auth = useAuth()
  const token = auth.user?.access_token

  return useQuery({
    queryKey: ['admin-api-keys'],
    queryFn: async () => apiFetch<ApiKeyResponse[]>('/api/api-keys', token),
    enabled: !!token,
  })
}

export function useApiKey(id: string) {
  const auth = useAuth()
  const token = auth.user?.access_token

  return useQuery({
    queryKey: ['admin-api-key', id],
    queryFn: async () => apiFetch<ApiKeyDetailResponse>(`/api/api-keys/${id}`, token),
    enabled: !!token && !!id,
  })
}

export function useCreateApiKey() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (data: CreateApiKeyRequest) =>
      apiFetch<CreateApiKeyResponse>('/api/api-keys', token, {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-api-keys'] })
    },
  })
}

export function useUpdateApiKey(id: string) {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (data: UpdateApiKeyRequest) =>
      apiFetch<ApiKeyDetailResponse>(`/api/api-keys/${id}`, token, {
        method: 'PATCH',
        body: JSON.stringify(data),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-api-keys'] })
      void queryClient.invalidateQueries({ queryKey: ['admin-api-key', id] })
    },
  })
}

export function useDeleteApiKey() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (id: string) =>
      apiFetch<void>(`/api/api-keys/${id}`, token, {
        method: 'DELETE',
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-api-keys'] })
    },
  })
}

export function useOrgKnowledgeBases() {
  const auth = useAuth()
  const token = auth.user?.access_token

  return useQuery({
    queryKey: ['app-knowledge-bases-for-api-keys'],
    queryFn: async () =>
      apiFetch<{ knowledge_bases: OrgKnowledgeBase[] }>(
        '/api/app/knowledge-bases?owner_type=org',
        token,
      ),
    enabled: !!token,
  })
}
