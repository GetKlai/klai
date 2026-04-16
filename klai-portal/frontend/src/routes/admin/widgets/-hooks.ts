// TanStack Query hooks for admin widgets (SPEC-WIDGET-002)
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useAuth } from 'react-oidc-context'
import { apiFetch } from '@/lib/apiFetch'
import type {
  WidgetResponse,
  WidgetDetailResponse,
  CreateWidgetRequest,
  UpdateWidgetRequest,
  OrgKnowledgeBase,
} from './-types'

export function useWidgets() {
  const auth = useAuth()
  const token = auth.user?.access_token

  return useQuery({
    queryKey: ['admin-widgets'],
    queryFn: async () => apiFetch<WidgetResponse[]>('/api/widgets', token),
    enabled: !!token,
  })
}

export function useWidget(id: string) {
  const auth = useAuth()
  const token = auth.user?.access_token

  return useQuery({
    queryKey: ['admin-widget', id],
    queryFn: async () => apiFetch<WidgetDetailResponse>(`/api/widgets/${id}`, token),
    enabled: !!token && !!id,
  })
}

export function useCreateWidget() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (data: CreateWidgetRequest) =>
      apiFetch<WidgetDetailResponse>('/api/widgets', token, {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-widgets'] })
    },
  })
}

export function useUpdateWidget(id: string) {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (data: UpdateWidgetRequest) =>
      apiFetch<WidgetResponse>(`/api/widgets/${id}`, token, {
        method: 'PATCH',
        body: JSON.stringify(data),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-widgets'] })
      void queryClient.invalidateQueries({ queryKey: ['admin-widget', id] })
    },
  })
}

export function useDeleteWidget() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (id: string) =>
      apiFetch<void>(`/api/widgets/${id}`, token, {
        method: 'DELETE',
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-widgets'] })
    },
  })
}

export function useOrgKnowledgeBases() {
  const auth = useAuth()
  const token = auth.user?.access_token

  return useQuery({
    queryKey: ['app-knowledge-bases-for-widgets'],
    queryFn: async () =>
      apiFetch<{ knowledge_bases: OrgKnowledgeBase[] }>(
        '/api/app/knowledge-bases?owner_type=org',
        token,
      ),
    enabled: !!token,
  })
}
