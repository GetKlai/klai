// TanStack Query hooks for admin widgets (SPEC-WIDGET-002)
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@/lib/auth'
import { apiFetch } from '@/lib/apiFetch'
import type {
  WidgetResponse,
  WidgetDetailResponse,
  CreateWidgetRequest,
  UpdateWidgetRequest,
  OrgKnowledgeBase,
  ConversationListItem,
  ConversationDetail,
  WidgetStats,
  StatsPeriod,
  HubSpotIntegrationStatus,
  WidgetPreviewSessionResponse,
} from './-types'

export function useWidgets() {
  const auth = useAuth()

  return useQuery({
    queryKey: ['admin-widgets'],
    queryFn: async () => apiFetch<WidgetResponse[]>('/api/admin/widgets'),
    enabled: auth.isAuthenticated,
  })
}

export function useWidget(id: string) {
  const auth = useAuth()

  return useQuery({
    queryKey: ['admin-widget', id],
    queryFn: async () => apiFetch<WidgetDetailResponse>(`/api/admin/widgets/${id}`),
    enabled: auth.isAuthenticated && !!id,
  })
}

// Preview session for the admin's live preview panel. The token is valid for
// an hour: fetch once, reuse across panel collapse/expand, and let the
// interval (mounted panel only) mint a fresh one well before expiry instead
// of letting a long-open preview run into a dead token. A save that changes
// the widget (useUpdateWidget) invalidates the session, because the minted
// JWT carries the kb_ids and must follow the saved knowledge-base access.
export function useWidgetPreviewSession(id: string) {
  const auth = useAuth()

  return useQuery({
    queryKey: ['admin-widget-preview-session', id],
    queryFn: async () =>
      apiFetch<WidgetPreviewSessionResponse>(`/api/admin/widgets/${id}/preview-session`),
    enabled: auth.isAuthenticated && !!id,
    retry: false,
    staleTime: 45 * 60 * 1000,
    refetchInterval: 45 * 60 * 1000,
  })
}

export function useCreateWidget() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (data: CreateWidgetRequest) =>
      apiFetch<WidgetDetailResponse>('/api/admin/widgets', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-widgets'] })
    },
  })
}

export function useUpdateWidget(id: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (data: UpdateWidgetRequest) =>
      apiFetch<WidgetResponse>(`/api/admin/widgets/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(data),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-widgets'] })
      void queryClient.invalidateQueries({ queryKey: ['admin-widget', id] })
      void queryClient.invalidateQueries({ queryKey: ['admin-widget-preview-session', id] })
    },
  })
}

export function useDeleteWidget() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (id: string) =>
      apiFetch<void>(`/api/admin/widgets/${id}`, {
        method: 'DELETE',
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-widgets'] })
    },
  })
}

// ──────────────────────────────────────────────────────────────
// Activity / audit-trail (SPEC-WIDGET-ACTIVITY-001)
// ──────────────────────────────────────────────────────────────

export function useWidgetConversations(widgetId: string) {
  const auth = useAuth()
  return useQuery({
    queryKey: ['admin-widget-conversations', widgetId],
    queryFn: async () =>
      apiFetch<ConversationListItem[]>(
        `/api/admin/widgets/${widgetId}/conversations?limit=50`,
      ),
    enabled: auth.isAuthenticated && !!widgetId,
  })
}

export function useWidgetConversation(
  widgetId: string,
  convId: string | number | null,
) {
  const auth = useAuth()
  return useQuery({
    queryKey: ['admin-widget-conversation', widgetId, convId],
    queryFn: async () =>
      apiFetch<ConversationDetail>(
        `/api/admin/widgets/${widgetId}/conversations/${convId}`,
      ),
    enabled: auth.isAuthenticated && !!widgetId && convId !== null,
  })
}

export function useWidgetStats(widgetId: string, period: StatsPeriod) {
  const auth = useAuth()
  return useQuery({
    queryKey: ['admin-widget-stats', widgetId, period],
    queryFn: async () =>
      apiFetch<WidgetStats>(
        `/api/admin/widgets/${widgetId}/stats?period=${period}`,
      ),
    enabled: auth.isAuthenticated && !!widgetId,
  })
}

export function useHubSpotIntegration(widgetId: string) {
  const auth = useAuth()
  return useQuery({
    queryKey: ['admin-widget-hubspot-integration', widgetId],
    queryFn: async () =>
      apiFetch<HubSpotIntegrationStatus>(
        `/api/admin/widgets/${widgetId}/integrations/hubspot`,
      ),
    enabled: auth.isAuthenticated && !!widgetId,
  })
}

export function useHubSpotIntegrationAction(
  widgetId: string,
  action: 'connect' | 'disconnect' | 'rebuild' | 'test-message',
) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () =>
      apiFetch<HubSpotIntegrationStatus>(
        `/api/admin/widgets/${widgetId}/integrations/hubspot/${action}`,
        { method: 'POST' },
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ['admin-widget-hubspot-integration', widgetId],
      })
      void queryClient.invalidateQueries({ queryKey: ['admin-widget', widgetId] })
    },
  })
}

export function useOrgKnowledgeBases() {
  const auth = useAuth()

  return useQuery({
    queryKey: ['app-knowledge-bases-for-widgets'],
    queryFn: async () =>
      apiFetch<{ knowledge_bases: OrgKnowledgeBase[] }>('/api/app/knowledge-bases?owner_type=org', ),
    enabled: auth.isAuthenticated,
  })
}
