// Live preview panel for the widget admin screen (SPEC-WIDGET-PREVIEW-001).
//
// Renders the visitor-facing chat surface beside the settings tabs, driven by
// the un-saved form state published through WidgetPreviewProvider. Chat access
// uses a short-lived preview session from the backend, which flags these
// conversations so they stay out of stats and the gap dashboard.
import { useState } from 'react'
import { Loader2, PanelRightClose, RotateCcw } from 'lucide-react'
import { Alert } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { WidgetChatSurface } from '@/features/widgets/chat/WidgetChatSurface'
import * as m from '@/paraglide/messages'
import { useWidgetPreviewSession } from '../-hooks'
import { useWidgetPreview } from '../-preview'
import type { WidgetDetailResponse } from '../-types'

interface Props {
  widget: WidgetDetailResponse
  onCollapse: () => void
}

export function WidgetPreviewPanel({ widget, onCollapse }: Props) {
  const preview = useWidgetPreview()
  const session = useWidgetPreviewSession(widget.id)
  // Remounting the surface is the restart: it clears messages, input and
  // visitor info in one step.
  const [conversationKey, setConversationKey] = useState(0)

  return (
    <aside
      className="min-w-0 self-start xl:sticky xl:top-6"
      aria-label={m.admin_widgets_preview_badge()}
    >
      <div className="overflow-hidden rounded-xl border border-gray-200 bg-white">
        <header className="flex h-12 items-center justify-between gap-2 border-b border-gray-200 px-3">
          <div className="flex min-w-0 items-center gap-2">
            <Badge variant="secondary">{m.admin_widgets_preview_badge()}</Badge>
            <p className="hidden truncate text-xs text-gray-600 sm:block">
              {m.admin_widgets_preview_subtitle()}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 px-2 text-xs"
              disabled={!session.data}
              onClick={() => setConversationKey((key) => key + 1)}
            >
              <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
              {m.admin_widgets_preview_restart()}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="icon"
              className="h-7 w-7 text-gray-500"
              aria-label={m.admin_widgets_preview_close()}
              onClick={onCollapse}
            >
              <PanelRightClose className="h-4 w-4" />
            </Button>
          </div>
        </header>

        <div className="h-[24rem] sm:h-[28rem] xl:h-[32rem]">
          {session.isPending ? (
            <div
              role="status"
              className="flex h-full flex-col items-center justify-center gap-2"
            >
              <Loader2 className="h-5 w-5 animate-spin text-gray-500" />
              <p className="text-xs text-gray-600">{m.admin_widgets_preview_loading()}</p>
            </div>
          ) : !session.data ? (
            // A failed background refetch keeps `data` and is therefore not
            // rendered here - it must never tear down a working conversation.
            <div
              role="alert"
              className="flex h-full flex-col items-center justify-center px-4 text-center"
            >
              <p className="text-sm font-medium text-gray-900">
                {m.widget_chat_preview_session_error()}
              </p>
              <p className="mt-1 text-xs text-gray-600">
                {session.error instanceof Error
                  ? session.error.message
                  : m.admin_shared_error_generic()}
              </p>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="mt-3"
                onClick={() => void session.refetch()}
              >
                {m.admin_widgets_preview_retry()}
              </Button>
            </div>
          ) : (
            <WidgetChatSurface
              key={conversationKey}
              embedded
              variant="admin-preview"
              botName={preview.botName}
              chatEndpoint={session.data.chat_endpoint}
              sessionToken={session.data.session_token}
              description={preview.description}
              welcomeMessage={preview.welcomeMessage}
              conversationStarters={preview.conversationStarters}
              hideDisclaimer={preview.hideDisclaimer}
              primaryColor={preview.primaryColor}
              theme={preview.theme}
              showSources={preview.showSources}
              showMeta={preview.showMeta}
              collectUserInfo={preview.collectUserInfo}
              // Never on: the surface would scrape the admin page and feed
              // portal text to the model as if it were the visitor's page.
              // The saved setting governs real visitors; the toggle is listed
              // under the save-only note.
              pageContextEnabled={false}
              // The nerds booking integration is not part of the live
              // form state the other preview props come from; the saved
              // config decides what the visitor-facing footer shows.
              nerdsEnabled={widget.widget_config.integrations?.nerds?.enabled}
              nerdsBookingUrl={widget.widget_config.integrations?.nerds?.booking_url ?? undefined}
            />
          )}
        </div>

        <footer className="border-t border-gray-200 px-3 py-2">
          <p className="text-[0.6875rem] text-gray-600">{m.admin_widgets_preview_not_counted()}</p>
        </footer>
      </div>

      {preview.modelBehaviorDirty && (
        <Alert size="sm" variant="warning" className="mt-3">
          {m.admin_widgets_preview_model_note()}
        </Alert>
      )}
    </aside>
  )
}
