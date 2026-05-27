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
  const canConnect = Boolean(status?.configured) && status?.status !== 'connected'
  const canRebuild = Boolean(status?.configured) && (
    status?.status === 'connected' ||
    status?.status === 'disconnected' ||
    status?.status === 'error'
  )
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
        <article className="rounded-lg border border-gray-200 bg-white p-5">
          <header className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div className="flex min-w-0 items-start gap-3">
              <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-[#ff7a59]/10 text-[#ff7a59]">
                <MessageSquareText className="h-5 w-5" />
              </div>
              <div className="min-w-0 space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-base font-semibold text-gray-900">HubSpot</h3>
                  <StatusBadge status={status?.status} loading={statusQuery.isLoading} />
                </div>
                <p className="text-sm text-gray-500">
                  {statusSummary(status?.status)}
                </p>
              </div>
            </div>
            <div className="flex flex-wrap gap-2 lg:justify-end">
              {canConnect && (
                <Button
                  type="button"
                  variant="default"
                  size="sm"
                  disabled={!canUseActions}
                  onClick={() => runAction(connectMutation, m.admin_widgets_integrations_connect_success())}
                >
                  {connectMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <PlugZap className="h-4 w-4" />}
                  {m.admin_widgets_integrations_connect()}
                </Button>
              )}
              {isConnected && (
                <Button
                  type="button"
                  variant="default"
                  size="sm"
                  disabled={!canUseActions}
                  onClick={() => runAction(testMutation, m.admin_widgets_integrations_test_success())}
                >
                  {testMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                  {m.admin_widgets_integrations_test_message()}
                </Button>
              )}
              {canRebuild && (
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
              )}
              {isConnected && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={!canUseActions}
                  onClick={() => runAction(disconnectMutation, m.admin_widgets_integrations_disconnect_success())}
                >
                  {disconnectMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Unplug className="h-4 w-4" />}
                  {m.admin_widgets_integrations_disconnect()}
                </Button>
              )}
              {status?.help_desk_url && (
                <Button type="button" variant="secondary" size="sm" asChild>
                  <a href={status.help_desk_url} target="_blank" rel="noreferrer">
                    {m.admin_widgets_integrations_open_hubspot()}
                    <ExternalLink className="h-4 w-4" />
                  </a>
                </Button>
              )}
            </div>
          </header>
          <p className="mt-4 max-w-3xl text-sm leading-6 text-gray-500">
            {m.admin_widgets_integrations_hubspot_description()}
          </p>
          <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-3">
            <div className="rounded-md border border-gray-100 bg-gray-50 px-3 py-2">
              <dt className="text-xs font-medium text-gray-400">
                {m.admin_widgets_integrations_target_label()}
              </dt>
              <dd className="mt-1 font-medium text-gray-800">
                {status?.inbox_id ? 'HubSpot Help Desk' : '-'}
              </dd>
            </div>
            <div className="rounded-md border border-gray-100 bg-gray-50 px-3 py-2">
              <dt className="text-xs font-medium text-gray-400">
                {m.admin_widgets_integrations_channel_label()}
              </dt>
              <dd className="mt-1 font-medium text-gray-800">
                {status?.channel_id ? 'Klai Webchat Support' : '-'}
              </dd>
            </div>
            <div className="rounded-md border border-gray-100 bg-gray-50 px-3 py-2">
              <dt className="text-xs font-medium text-gray-400">
                {m.admin_widgets_integrations_mode_label()}
              </dt>
              <dd className="mt-1 font-medium text-gray-800">
                {m.admin_widgets_integrations_mode_realtime()}
              </dd>
            </div>
          </dl>
          {(status?.channel_account_id || status?.last_test_thread_id) && (
            <div className="mt-4 flex flex-wrap gap-x-5 gap-y-1 text-xs text-gray-400">
              {status?.channel_account_id && (
                <span>
                  {m.admin_widgets_integrations_channel_account_label()}: {status.channel_account_id}
                </span>
              )}
              {status?.last_test_thread_id && (
                <span>
                  {m.admin_widgets_integrations_last_test_thread_label()}: {status.last_test_thread_id}
                </span>
              )}
            </div>
          )}
          {status?.last_error && (
            <p className="mt-4 rounded-md border border-[var(--color-destructive-bg)] bg-[var(--color-destructive-bg)] px-3 py-2 text-sm text-[var(--color-destructive-text)]">
              {status.last_error}
            </p>
          )}
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

function statusSummary(status: string | undefined) {
  if (status === 'connected') {
    return m.admin_widgets_integrations_status_summary_connected()
  }
  if (status === 'disconnected') {
    return m.admin_widgets_integrations_status_summary_disconnected()
  }
  if (status === 'error') {
    return m.admin_widgets_integrations_status_summary_error()
  }
  if (status === 'not_configured') {
    return m.admin_widgets_integrations_status_summary_not_configured()
  }
  return m.admin_widgets_integrations_status_summary_not_connected()
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
