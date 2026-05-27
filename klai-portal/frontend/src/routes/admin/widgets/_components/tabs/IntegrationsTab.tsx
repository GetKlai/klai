import { ExternalLink, Loader2, MessageSquareText, PlugZap, RotateCcw, Unplug } from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'
import {
  useHubSpotIntegration,
  useHubSpotIntegrationAction,
} from '../../-hooks'
import type { WidgetDetailResponse } from '../../-types'

interface Props {
  widget: WidgetDetailResponse
}

export function IntegrationsTab({ widget }: Props) {
  const widgetId = String(widget.id)
  const statusQuery = useHubSpotIntegration(widgetId)
  const connectMutation = useHubSpotIntegrationAction(widgetId, 'connect')
  const disconnectMutation = useHubSpotIntegrationAction(widgetId, 'disconnect')
  const rebuildMutation = useHubSpotIntegrationAction(widgetId, 'rebuild')
  const testMutation = useHubSpotIntegrationAction(widgetId, 'test-message')

  const status = statusQuery.data
  const isBusy =
    connectMutation.isPending ||
    disconnectMutation.isPending ||
    rebuildMutation.isPending ||
    testMutation.isPending
  const isConnected = status?.status === 'connected'
  const canUseActions = Boolean(status?.configured) && !isBusy
  const error =
    statusQuery.error ||
    connectMutation.error ||
    disconnectMutation.error ||
    rebuildMutation.error ||
    testMutation.error

  function runAction(
    mutation: typeof connectMutation,
    successMessage: string,
  ) {
    mutation.mutate(undefined, {
      onSuccess: () => toast.success(successMessage),
      onError: (err) => {
        toast.error(err instanceof Error ? err.message : m.admin_shared_error_generic())
      },
    })
  }

  return (
    <section className="space-y-6">
      <div className="space-y-1.5">
        <h2 className="text-lg font-display-bold text-gray-900">
          {m.admin_widgets_integrations_title()}
        </h2>
        <p className="max-w-2xl text-sm text-gray-400">
          {m.admin_widgets_integrations_intro({ name: widget.name })}
        </p>
      </div>

      <div className="grid gap-3">
        <article className="rounded-lg border border-gray-200 bg-white p-4">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
            <div className="flex min-w-0 gap-3">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-[#ff7a59]/10 text-[#ff7a59]">
                <MessageSquareText className="h-5 w-5" />
              </div>
              <div className="min-w-0 space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-sm font-semibold text-gray-900">HubSpot</h3>
                  <StatusBadge status={status?.status} loading={statusQuery.isLoading} />
                </div>
                <p className="max-w-2xl text-sm text-gray-500">
                  {m.admin_widgets_integrations_hubspot_description()}
                </p>
                <dl className="grid gap-2 pt-1 text-xs text-gray-400 sm:grid-cols-3">
                  <div>
                    <dt className="font-medium text-gray-500">
                      {m.admin_widgets_integrations_target_label()}
                    </dt>
                    <dd>{status?.inbox_id ? 'HubSpot Help Desk' : '-'}</dd>
                  </div>
                  <div>
                    <dt className="font-medium text-gray-500">
                      {m.admin_widgets_integrations_channel_label()}
                    </dt>
                    <dd>{status?.channel_id ? 'Klai Webchat Support' : '-'}</dd>
                  </div>
                  <div>
                    <dt className="font-medium text-gray-500">
                      {m.admin_widgets_integrations_mode_label()}
                    </dt>
                    <dd>{m.admin_widgets_integrations_mode_realtime()}</dd>
                  </div>
                </dl>
                {status?.channel_account_id && (
                  <p className="text-xs text-gray-400">
                    {m.admin_widgets_integrations_channel_account_label()}: {status.channel_account_id}
                  </p>
                )}
                {status?.last_test_thread_id && (
                  <p className="text-xs text-gray-400">
                    {m.admin_widgets_integrations_last_test_thread_label()}: {status.last_test_thread_id}
                  </p>
                )}
                {status?.last_error && (
                  <p className="text-xs text-[var(--color-destructive)]">
                    {status.last_error}
                  </p>
                )}
              </div>
            </div>
            <div className="flex shrink-0 flex-wrap gap-2 sm:justify-end">
              <Button
                type="button"
                variant={isConnected ? 'secondary' : 'default'}
                size="sm"
                disabled={!canUseActions || isConnected}
                onClick={() => runAction(connectMutation, m.admin_widgets_integrations_connect_success())}
              >
                {connectMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <PlugZap className="h-4 w-4" />}
                {m.admin_widgets_integrations_connect()}
              </Button>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                disabled={!canUseActions}
                onClick={() => runAction(rebuildMutation, m.admin_widgets_integrations_rebuild_success())}
              >
                {rebuildMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}
                {m.admin_widgets_integrations_rebuild()}
              </Button>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                disabled={!canUseActions || !isConnected}
                onClick={() => runAction(testMutation, m.admin_widgets_integrations_test_success())}
              >
                {testMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                {m.admin_widgets_integrations_test_message()}
              </Button>
              <Button
                type="button"
                variant="destructive"
                size="sm"
                disabled={!canUseActions || !isConnected}
                onClick={() => runAction(disconnectMutation, m.admin_widgets_integrations_disconnect_success())}
              >
                {disconnectMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Unplug className="h-4 w-4" />}
                {m.admin_widgets_integrations_disconnect()}
              </Button>
              {status?.help_desk_url && (
                <Button type="button" variant="secondary" size="sm" asChild>
                  <a href={status.help_desk_url} target="_blank" rel="noreferrer">
                    {m.admin_widgets_integrations_open_hubspot()}
                    <ExternalLink className="h-4 w-4" />
                  </a>
                </Button>
              )}
            </div>
          </div>
          {status?.status === 'not_configured' && (
            <p className="mt-4 rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-500">
              {m.admin_widgets_integrations_not_configured_help()}
            </p>
          )}
          {error && (
            <p className="mt-4 text-sm text-[var(--color-destructive)]">
              {error instanceof Error ? error.message : m.admin_shared_error_generic()}
            </p>
          )}
        </article>
      </div>
    </section>
  )
}

function StatusBadge({
  status,
  loading,
}: {
  status: string | undefined
  loading: boolean
}) {
  if (loading) {
    return (
      <Badge variant="secondary">
        <Loader2 className="mr-1 h-3 w-3 animate-spin" />
        {m.admin_shared_loading()}
      </Badge>
    )
  }
  if (status === 'connected') {
    return <Badge variant="success">{m.admin_widgets_integrations_status_connected()}</Badge>
  }
  if (status === 'disconnected') {
    return <Badge variant="warning">{m.admin_widgets_integrations_status_disconnected()}</Badge>
  }
  if (status === 'error') {
    return <Badge variant="destructive">{m.admin_widgets_integrations_status_error()}</Badge>
  }
  if (status === 'not_configured') {
    return <Badge variant="secondary">{m.admin_widgets_integrations_status_not_configured()}</Badge>
  }
  return <Badge variant="outline">{m.admin_widgets_integrations_status_not_connected()}</Badge>
}
