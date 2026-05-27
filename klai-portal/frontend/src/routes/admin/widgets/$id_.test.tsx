import { createFileRoute } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { WidgetChatSurface } from '@/features/widgets/chat/WidgetChatSurface'
import { useHideGlobalWidget } from '@/features/widgets/chat/useHideGlobalWidget'
import { apiFetch } from '@/lib/apiFetch'
import * as m from '@/paraglide/messages'
import type { WidgetDetailResponse } from './-types'

// Admin preview route. Widget detail comes from admin auth; chat access uses
// a short-lived preview session token without the public Origin gate.

export const Route = createFileRoute('/admin/widgets/$id_/test')({
  component: WidgetTestPage,
})

interface PreviewSession {
  session_token: string
  chat_endpoint: string
  session_expires_at: string
}

function WidgetTestPage() {
  const { id } = Route.useParams()

  useHideGlobalWidget(
    'klai-admin-widget-preview-hide-help-widget',
    '[data-help-id="chat-help-bubble"], .klai-help-button',
  )

  const widgetQuery = useQuery<WidgetDetailResponse>({
    queryKey: ['admin-widget-detail', id],
    queryFn: () => apiFetch<WidgetDetailResponse>(`/api/admin/widgets/${id}`),
  })

  const sessionQuery = useQuery<PreviewSession>({
    queryKey: ['widget-preview-session', id],
    queryFn: () => apiFetch<PreviewSession>(`/api/admin/widgets/${id}/preview-session`),
    retry: false,
  })

  if (widgetQuery.isPending || sessionQuery.isPending) {
    return (
      <div className="fixed inset-0 z-[60] flex items-center justify-center bg-white">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-gray-300 border-t-gray-900" />
      </div>
    )
  }

  if (widgetQuery.error || !widgetQuery.data) {
    return (
      <div className="fixed inset-0 z-[60] flex items-center justify-center bg-white px-6">
        <p className="text-sm text-[var(--color-destructive)]">
          {widgetQuery.error instanceof Error ? widgetQuery.error.message : m.widget_chat_widget_load_error()}
        </p>
      </div>
    )
  }

  if (sessionQuery.error || !sessionQuery.data) {
    return (
      <div className="fixed inset-0 z-[60] flex items-center justify-center bg-white px-6">
        <div className="max-w-md text-center">
          <p className="text-sm font-medium text-gray-900">{m.widget_chat_preview_session_error()}</p>
          <p className="mt-1 text-xs text-gray-400">
            {sessionQuery.error instanceof Error ? sessionQuery.error.message : m.admin_shared_error_generic()}
          </p>
        </div>
      </div>
    )
  }

  const widget = widgetQuery.data
  const session = sessionQuery.data
  const config = widget.widget_config

  return (
    <WidgetChatSurface
      botName={config.title || widget.name}
      chatEndpoint={session.chat_endpoint}
      sessionToken={session.session_token}
      description={widget.description || ''}
      welcomeMessage={config.welcome_message}
      conversationStarters={config.conversation_starters}
      hideDisclaimer={config.hide_disclaimer}
      primaryColor={config.primary_color}
      theme={config.theme}
      showSources={config.show_sources}
      showMeta={config.show_meta}
      collectUserInfo={config.collect_user_info}
      pageContextEnabled={config.page_context_enabled}
      variant="admin-preview"
      onClose={() => window.close()}
    />
  )
}
