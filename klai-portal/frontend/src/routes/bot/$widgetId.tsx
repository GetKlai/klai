import { createFileRoute } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { WidgetChatSurface } from '@/features/widgets/chat/WidgetChatSurface'
import { useHideGlobalWidget } from '@/features/widgets/chat/useHideGlobalWidget'
import * as m from '@/paraglide/messages'

// Public bot share-page at /bot/<widget_id>. No admin auth required:
// the security boundary is the signed session token returned by the public
// config endpoint.

export const Route = createFileRoute('/bot/$widgetId')({
  component: PublicBotPage,
})

interface PublicConfig {
  title: string
  welcome_message: string
  chat_endpoint: string
  session_token: string
  session_expires_at: string
  css_variables?: Record<string, string>
  conversation_starters?: string[]
  hide_disclaimer?: boolean
  primary_color?: string
  name?: string
  description?: string
}

function PublicBotPage() {
  const { widgetId } = Route.useParams()

  useHideGlobalWidget(
    'klai-public-bot-hide-help-widget',
    '.klai-bubble, #klai-widget-root',
  )

  const configQuery = useQuery<PublicConfig>({
    queryKey: ['public-bot-config', widgetId],
    queryFn: async () => {
      const res = await fetch(`/partner/v1/public-bot-config?id=${encodeURIComponent(widgetId)}`)
      if (!res.ok) throw new Error(`config ${res.status}`)
      return (await res.json()) as PublicConfig
    },
    retry: false,
  })

  if (configQuery.isPending) {
    return (
      <div className="fixed inset-0 z-[60] flex items-center justify-center bg-white">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-gray-300 border-t-gray-900" />
      </div>
    )
  }

  if (configQuery.error || !configQuery.data) {
    return (
      <div className="fixed inset-0 z-[60] flex items-center justify-center bg-white px-6">
        <div className="max-w-md text-center">
          <p className="text-sm font-medium text-gray-900">{m.widget_chat_unavailable()}</p>
          <p className="mt-1 text-xs text-gray-400">
            {configQuery.error instanceof Error ? configQuery.error.message : m.admin_shared_error_generic()}
          </p>
        </div>
      </div>
    )
  }

  const cfg = configQuery.data

  return (
    <WidgetChatSurface
      botName={cfg.title || cfg.name || m.widget_chat_default_bot_name()}
      chatEndpoint={cfg.chat_endpoint}
      sessionToken={cfg.session_token}
      description={cfg.description || ''}
      welcomeMessage={cfg.welcome_message}
      conversationStarters={cfg.conversation_starters}
      hideDisclaimer={cfg.hide_disclaimer}
      primaryColor={cfg.primary_color}
      shareUrl={window.location.href}
    />
  )
}
