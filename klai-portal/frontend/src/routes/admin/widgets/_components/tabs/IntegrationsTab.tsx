import { useEffect, useState } from 'react'
import {
  CalendarCheck,
  ExternalLink,
  Loader2,
  MessageSquareText,
  PlugZap,
  RotateCcw,
  Unplug,
  Users,
} from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import * as m from '@/paraglide/messages'
import {
  useHubSpotIntegration,
  useHubSpotIntegrationAction,
  useUpdateWidget,
} from '../../-hooks'
import { FetchError } from '@/lib/fetch-errors'
import type {
  HubSpotWidgetIntegration,
  WidgetConfig,
  WidgetDetailResponse,
  WidgetIntegrations,
} from '../../-types'

interface Props {
  widget: WidgetDetailResponse
}

// INTERIM appointment redirect - a fixed booking URL in widget_config
// until the chat booking API integration replaces it (mirrors the
// interim marker on the backend field). Absolute http(s) only, matching
// what partner.py's _widget_booking_url will actually deliver to the
// widget; anything else would silently never show a button.
function isAbsoluteHttpUrl(value: string): boolean {
  try {
    const parsed = new URL(value)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:'
  } catch {
    return false
  }
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
  // HubSpot has one channel platform-wide, so the connect endpoints answer 404
  // for every other tenant. Rather than teach the frontend which tenant that
  // is, take the backend's own answer: no card, no error, and the booking-URL
  // card below still shows. When the backend gains per-tenant channels this
  // starts working on its own.
  const hubspotUnavailable =
    statusQuery.error instanceof FetchError && statusQuery.error.status === 404

  const error =
    (hubspotUnavailable ? null : statusQuery.error) ||
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
        <p className="max-w-2xl text-sm text-gray-600">
          {m.admin_widgets_integrations_intro({ name: widget.name })}
        </p>
      </div>

      <div className="grid gap-3">
        {hubspotUnavailable ? null : (
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
              <dt className="text-xs font-medium text-gray-600">
                {m.admin_widgets_integrations_target_label()}
              </dt>
              <dd className="mt-1 font-medium text-gray-800">
                {status?.inbox_id ? 'HubSpot Help Desk' : '-'}
              </dd>
            </div>
            <div className="rounded-md border border-gray-100 bg-gray-50 px-3 py-2">
              <dt className="text-xs font-medium text-gray-600">
                {m.admin_widgets_integrations_channel_label()}
              </dt>
              <dd className="mt-1 font-medium text-gray-800">
                {status?.channel_id ? 'Klai Webchat Support' : '-'}
              </dd>
            </div>
            <div className="rounded-md border border-gray-100 bg-gray-50 px-3 py-2">
              <dt className="text-xs font-medium text-gray-600">
                {m.admin_widgets_integrations_mode_label()}
              </dt>
              <dd className="mt-1 font-medium text-gray-800">
                {m.admin_widgets_integrations_mode_realtime()}
              </dd>
            </div>
          </dl>
          {(status?.channel_account_id || status?.last_test_thread_id) && (
            <div className="mt-4 flex flex-wrap gap-x-5 gap-y-1 text-xs text-gray-600">
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
        )}

        <NerdsCard widget={widget} />
        <BookingCard widget={widget} />
      </div>
    </section>
  )
}

// Saving the nerds integration goes through the generic widget PATCH, which
// rewrites the whole integrations block — so the untouched hubspot state
// must ride along. Widgets created before hubspot existed carry no block at
// all; the backend default is not_connected, mirrored here.
const HUBSPOT_NOT_CONNECTED: HubSpotWidgetIntegration = {
  status: 'not_connected',
  portal_id: null,
  channel_id: null,
  channel_account_id: null,
  inbox_id: null,
  help_desk_url: null,
  last_connected_at: null,
  last_disconnected_at: null,
  last_rebuilt_at: null,
  last_tested_at: null,
  last_test_thread_id: null,
  last_error: null,
}

// Voys-specific: the Nerds booking panel. Not a configurable framework —
// exactly one named integration, rendered as a card beside HubSpot.
function NerdsCard({ widget }: Props) {
  const updateMutation = useUpdateWidget(String(widget.id))
  const config = widget.widget_config
  const nerds = config.integrations?.nerds
  const [enabled, setEnabled] = useState(nerds?.enabled ?? false)
  const [bookingUrl, setBookingUrl] = useState(nerds?.booking_url ?? '')

  useEffect(() => {
    setEnabled(nerds?.enabled ?? false)
    setBookingUrl(nerds?.booking_url ?? '')
  }, [nerds?.enabled, nerds?.booking_url])

  const trimmed = bookingUrl.trim()
  const isEmpty = trimmed.length === 0
  const isValid = isEmpty || isAbsoluteHttpUrl(trimmed)
  // partner.py only ever delivers the panel with a usable URL: enabling
  // without one saves a dead switch, so the form requires it up front.
  const showUrlError = !isValid || (enabled && isEmpty)
  const isDirty = enabled !== (nerds?.enabled ?? false) || trimmed !== (nerds?.booking_url ?? '')

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (showUrlError) return
    const integrations: WidgetIntegrations = {
      ...config.integrations,
      hubspot: config.integrations?.hubspot ?? HUBSPOT_NOT_CONNECTED,
      nerds: {
        enabled,
        booking_url: isEmpty ? null : trimmed,
      },
    }
    const next: WidgetConfig = { ...config, integrations }
    updateMutation.mutate(
      { widget_config: next },
      { onSuccess: () => toast.success(m.admin_shared_success_updated()) },
    )
  }

  return (
    <article className="rounded-lg border border-gray-200 bg-white p-5">
      <header className="flex items-start gap-3">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-[var(--color-rl-accent)]/10 text-[var(--color-rl-dark)]">
          <Users className="h-5 w-5" />
        </div>
        <div className="min-w-0 space-y-1">
          <h3 className="text-base font-semibold text-gray-900">
            {m.admin_widgets_integrations_nerds_title()}
          </h3>
          <p className="text-sm text-gray-500">
            {m.admin_widgets_integrations_nerds_description()}
          </p>
        </div>
      </header>

      <form onSubmit={handleSubmit} noValidate className="mt-4 space-y-3">
        <div className="flex items-center gap-3">
          <Switch
            id="widget-nerds-enabled"
            checked={enabled}
            onCheckedChange={setEnabled}
          />
          <Label htmlFor="widget-nerds-enabled">
            {m.admin_widgets_integrations_nerds_enabled_label()}
          </Label>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="widget-nerds-url">
            {m.admin_widgets_integrations_booking_url_label()}
          </Label>
          <p className="text-xs text-gray-600">
            {m.admin_widgets_integrations_nerds_url_help()}
          </p>
          <Input
            id="widget-nerds-url"
            type="url"
            value={bookingUrl}
            onChange={(e) => setBookingUrl(e.target.value)}
            placeholder={m.admin_widgets_integrations_booking_url_placeholder()}
            aria-invalid={showUrlError}
            className="max-w-xl"
          />
          {showUrlError && (
            <p className="text-sm text-[var(--color-destructive)]">
              {m.admin_widgets_integrations_booking_error_invalid()}
            </p>
          )}
        </div>
        <p className="text-xs text-gray-600">
          {m.admin_widgets_integrations_nerds_hint()}
        </p>
        {updateMutation.error && (
          <p className="text-sm text-[var(--color-destructive)]">
            {updateMutation.error instanceof Error
              ? updateMutation.error.message
              : m.admin_shared_error_generic()}
          </p>
        )}
        <div>
          <Button
            type="submit"
            size="sm"
            disabled={updateMutation.isPending || !isDirty || showUrlError}
          >
            {updateMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {m.admin_shared_save()}
          </Button>
        </div>
      </form>
    </article>
  )
}

function BookingCard({ widget }: Props) {
  const updateMutation = useUpdateWidget(String(widget.id))
  const config = widget.widget_config
  const [bookingUrl, setBookingUrl] = useState(config.booking_url ?? '')

  useEffect(() => {
    setBookingUrl(config.booking_url ?? '')
  }, [config.booking_url])

  const trimmed = bookingUrl.trim()
  const isEmpty = trimmed.length === 0
  const isValid = isEmpty || isAbsoluteHttpUrl(trimmed)
  const isDirty = trimmed !== (config.booking_url ?? '')

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!isValid) return
    const next: WidgetConfig = {
      ...config,
      // Empty field = no appointment button for the visitor (same
      // contract the widget client implements).
      booking_url: isEmpty ? null : trimmed,
    }
    updateMutation.mutate(
      { widget_config: next },
      { onSuccess: () => toast.success(m.admin_shared_success_updated()) },
    )
  }

  return (
    <article className="rounded-lg border border-gray-200 bg-white p-5">
      <header className="flex items-start gap-3">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-[var(--color-rl-accent)]/10 text-[var(--color-rl-dark)]">
          <CalendarCheck className="h-5 w-5" />
        </div>
        <div className="min-w-0 space-y-1">
          <h3 className="text-base font-semibold text-gray-900">
            {m.admin_widgets_integrations_booking_title()}
          </h3>
          <p className="text-sm text-gray-500">
            {m.admin_widgets_integrations_booking_description()}
          </p>
        </div>
      </header>

      <form onSubmit={handleSubmit} noValidate className="mt-4 space-y-3">
        <div className="space-y-1.5">
          <Label htmlFor="widget-booking-url">
            {m.admin_widgets_integrations_booking_url_label()}
          </Label>
          <p className="text-xs text-gray-600">
            {m.admin_widgets_integrations_booking_url_help()}
          </p>
          <Input
            id="widget-booking-url"
            type="url"
            value={bookingUrl}
            onChange={(e) => setBookingUrl(e.target.value)}
            placeholder={m.admin_widgets_integrations_booking_url_placeholder()}
            aria-invalid={!isValid}
            className="max-w-xl"
          />
          {!isValid && (
            <p className="text-sm text-[var(--color-destructive)]">
              {m.admin_widgets_integrations_booking_error_invalid()}
            </p>
          )}
        </div>
        <p className="text-xs text-gray-600">
          {m.admin_widgets_integrations_booking_button_hint()}
        </p>
        <p className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-500">
          {m.admin_widgets_integrations_booking_interim_note()}
        </p>
        {updateMutation.error && (
          <p className="text-sm text-[var(--color-destructive)]">
            {updateMutation.error instanceof Error
              ? updateMutation.error.message
              : m.admin_shared_error_generic()}
          </p>
        )}
        <div>
          <Button
            type="submit"
            size="sm"
            disabled={updateMutation.isPending || !isDirty || !isValid}
          >
            {updateMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {m.admin_shared_save()}
          </Button>
        </div>
      </form>
    </article>
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
