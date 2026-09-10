import { createFileRoute } from '@tanstack/react-router'
import { useQueryClient } from '@tanstack/react-query'
import { useHideGlobalWidget } from '@/features/widgets/chat/useHideGlobalWidget'
import * as m from '@/paraglide/messages'
import { getLocale } from '@/paraglide/runtime'
import { fetchWidgetPreviewSession, useWidget, useWidgetPreviewSession, widgetPreviewSessionQueryKey } from './-hooks'
import { resolvePreview } from './-preview'
import { WidgetEmbedPreview } from './_components/WidgetEmbedPreview'
import { buildWidgetRendererConfig } from './_components/WidgetPreviewPanel'

// Admin preview route. Widget detail comes from admin auth; chat access uses
// a short-lived preview session token without the public Origin gate.

export const Route = createFileRoute('/admin/widgets/$id_/test')({
  component: WidgetTestPage,
})

function WidgetTestPage() {
  const { id } = Route.useParams()
  const queryClient = useQueryClient()

  useHideGlobalWidget(
    'klai-admin-widget-preview-hide-help-widget',
    '[data-help-id="chat-help-bubble"], .klai-help-button',
  )

  const widgetQuery = useWidget(id)
  const sessionQuery = useWidgetPreviewSession(id)

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
          <p className="mt-1 text-xs text-gray-600">
            {sessionQuery.error instanceof Error ? sessionQuery.error.message : m.admin_shared_error_generic()}
          </p>
        </div>
      </div>
    )
  }

  const widget = widgetQuery.data
  const session = sessionQuery.data
  const preview = resolvePreview(widget)
  const fetchConfig = async (sessionId?: string) => {
    const fresh = await fetchWidgetPreviewSession(id, sessionId)
    queryClient.setQueryData(widgetPreviewSessionQueryKey(id), fresh)
    return buildWidgetRendererConfig(widget, preview, fresh)
  }

  return (
    <div className="fixed inset-0 z-[60] bg-white">
      <WidgetEmbedPreview
        widgetId={widget.widget_id}
        locale={getLocale()}
        config={buildWidgetRendererConfig(widget, preview, session)}
        fetchConfig={fetchConfig}
        title={m.admin_widgets_preview_badge()}
      />
    </div>
  )
}
