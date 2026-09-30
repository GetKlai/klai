import { useEffect, useRef, useState } from 'react'
import {
  CalendarCheck,
  ExternalLink,
  Loader2,
  MessageSquareText,
  PlugZap,
  Plus,
  RotateCcw,
  Ticket,
  Unplug,
  Users,
  X,
} from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Field } from '@/components/ui/field'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import * as m from '@/paraglide/messages'
import {
  useDeleteTicketIntegration,
  useFetchTicketPipelines,
  useHubSpotIntegration,
  useHubSpotIntegrationAction,
  useSaveTicketIntegration,
  useTicketIntegration,
  useUpdateWidget,
} from '../../-hooks'
import { ApiError } from '@/lib/apiFetch'
import { FetchError } from '@/lib/fetch-errors'
import type {
  HubSpotWidgetIntegration,
  TicketIntegrationSettings,
  TicketPipeline,
  TicketTarget,
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
        <TicketsCard widget={widget} />
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

const MAX_TICKET_TARGETS = 5

// Naive ASCII slug, good enough for a HubSpot pipeline label a human typed;
// the backend's unique-key constraint (§4.1) is the real duplicate guard.
// Ceiling: two labels that collapse to the same slug (e.g. "Sales" and
// "Sales!") both land on the same key until the server rejects the save.
function slugifyTargetKey(label: string, fallbackIndex: number): string {
  const slug = label
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 32)
  return slug || `target-${fallbackIndex + 1}`
}

function ticketIntegrationErrorMessage(err: unknown): string {
  const detail = err instanceof ApiError ? err.detail : null
  if (detail === 'invalid_service_key') return m.admin_widgets_tickets_error_invalid_key()
  if (detail === 'missing_scope') return m.admin_widgets_tickets_error_missing_scope()
  if (detail === 'unknown_pipeline_or_stage') return m.admin_widgets_tickets_error_unknown_pipeline_stage()
  if (detail === 'service_key_required') return m.admin_widgets_tickets_error_key_required()
  if (detail === 'portal_id_required') return m.admin_widgets_tickets_error_portal_id_required()
  if (detail === 'account_mismatch') return m.admin_widgets_tickets_error_account_mismatch()
  return err instanceof Error ? err.message : m.admin_shared_error_generic()
}

type TicketTargetRow = TicketTarget & { keyLocked: boolean }

// Platform-wide ticket-creation targets (SPEC-KNOWLEDGE-ESCALATION-001 §4.2):
// unlike NerdsCard/BookingCard this is not Voys-specific — any tenant with a
// HubSpot ticket pipeline can configure it.
function TicketsCard({ widget }: Props) {
  const widgetId = String(widget.id)
  const query = useTicketIntegration(widgetId)
  const saveMutation = useSaveTicketIntegration(widgetId)
  const deleteMutation = useDeleteTicketIntegration(widgetId)
  const pipelinesMutation = useFetchTicketPipelines(widgetId)

  const [serviceKey, setServiceKey] = useState('')
  const [portalId, setPortalId] = useState('')
  // Where the shown account ID came from. An ID fetched from HubSpot is not
  // sent back, so swapping the key for one on another account refetches it
  // instead of tripping account_mismatch; typing in the field makes it manual.
  const [portalIdSource, setPortalIdSource] = useState<TicketIntegrationSettings['portal_id_source']>(null)
  const [targets, setTargets] = useState<TicketTargetRow[]>([])
  const [pipelines, setPipelines] = useState<TicketPipeline[] | null>(null)
  const [confirmingDelete, setConfirmingDelete] = useState(false)

  const fromServer = (rows: TicketTarget[]): TicketTargetRow[] =>
    rows.map((target) => ({ ...target, keyLocked: true }))

  // Seed the editable rows once, the first time the initial GET resolves.
  // Only that first load may overwrite local state — a save/delete re-seeds
  // explicitly from its own response instead of from a generic effect on
  // `query.data`, so a background refetch (or one that resolves after the
  // admin already started adding a target) never clobbers an unsaved edit.
  const seededRef = useRef(false)
  useEffect(() => {
    if (query.data && !seededRef.current) {
      seededRef.current = true
      setTargets(fromServer(query.data.targets))
      setPortalId(query.data.hubspot_portal_id ? String(query.data.hubspot_portal_id) : '')
      setPortalIdSource(query.data.portal_id_source)
    }
  }, [query.data])

  const configured = query.data?.configured ?? false

  const addTarget = () => {
    if (targets.length >= MAX_TICKET_TARGETS) return
    setTargets((prev) => [...prev, { key: '', label: '', pipeline_id: '', stage_id: '', keyLocked: false }])
  }

  const removeTarget = (index: number) => setTargets((prev) => prev.filter((_, i) => i !== index))

  const updateLabel = (index: number, label: string) =>
    setTargets((prev) =>
      prev.map((target, i) =>
        i === index ? { ...target, label, key: target.keyLocked ? target.key : slugifyTargetKey(label, index) } : target,
      ),
    )

  const updatePipeline = (index: number, pipelineId: string) =>
    setTargets((prev) =>
      prev.map((target, i) => (i === index ? { ...target, pipeline_id: pipelineId, stage_id: '' } : target)),
    )

  const updateStage = (index: number, stageId: string) =>
    setTargets((prev) => prev.map((target, i) => (i === index ? { ...target, stage_id: stageId } : target)))

  const handleFetchPipelines = () => {
    pipelinesMutation.mutate(serviceKey.trim() || undefined, {
      onSuccess: (data) => setPipelines(data),
      onError: (err) => toast.error(ticketIntegrationErrorMessage(err)),
    })
  }

  const needsKey = !configured
  const keyOk = !needsKey || serviceKey.trim().length > 0
  // Optional (SPEC §4.2): the server fetches it when the key may.
  const portalIdValid = portalId.trim() === '' || /^[1-9]\d*$/.test(portalId.trim())
  const targetsValid =
    targets.length > 0 && targets.every((target) => target.label.trim() && target.pipeline_id && target.stage_id)

  const handleSave = () => {
    if (!portalIdValid) return
    const payload: { service_key?: string; hubspot_portal_id?: number; targets: TicketTarget[] } = {
      targets: targets.map(({ key, label, pipeline_id, stage_id }) => ({ key, label, pipeline_id, stage_id })),
    }
    if (serviceKey.trim()) payload.service_key = serviceKey.trim()
    if (portalId.trim() && portalIdSource !== 'hubspot') payload.hubspot_portal_id = Number(portalId.trim())
    saveMutation.mutate(payload, {
      onSuccess: (data) => {
        toast.success(m.admin_shared_success_updated())
        setServiceKey('')
        setTargets(fromServer(data.targets))
        setPortalId(data.hubspot_portal_id ? String(data.hubspot_portal_id) : '')
        setPortalIdSource(data.portal_id_source)
      },
      onError: (err) => toast.error(ticketIntegrationErrorMessage(err)),
    })
  }

  const handleDelete = () => {
    deleteMutation.mutate(undefined, {
      onSuccess: () => {
        toast.success(m.admin_widgets_tickets_delete_success())
        setServiceKey('')
        setPortalId('')
        setPortalIdSource(null)
        setPipelines(null)
        setTargets([])
        setConfirmingDelete(false)
      },
    })
  }

  return (
    <article className="rounded-lg border border-gray-200 bg-white p-5">
      <header className="flex items-start gap-3">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-[#ff7a59]/10 text-[#ff7a59]">
          <Ticket className="h-5 w-5" />
        </div>
        <div className="min-w-0 space-y-1">
          <h3 className="text-base font-semibold text-gray-900">{m.admin_widgets_tickets_title()}</h3>
          <p className="text-sm text-gray-500">{m.admin_widgets_tickets_description()}</p>
        </div>
      </header>

      {!query.data ? (
        <p className="mt-4 text-sm text-gray-500">{m.admin_shared_loading()}</p>
      ) : (
      <div className="mt-4 space-y-4">
        <Field
          id="widget-tickets-portal-id"
          label={m.admin_widgets_tickets_portal_id_label()}
          hint={
            portalIdSource === 'hubspot'
              ? m.admin_widgets_tickets_portal_id_from_hubspot()
              : m.admin_widgets_tickets_portal_id_help()
          }
        >
          <Input
            type="number"
            min={1}
            step={1}
            value={portalId}
            onChange={(e) => {
              setPortalId(e.target.value)
              setPortalIdSource('manual')
            }}
            className="max-w-xs"
          />
        </Field>

        <Field
          id="widget-tickets-service-key"
          label={m.admin_widgets_tickets_key_label()}
          hint={m.admin_widgets_tickets_key_scopes_help()}
        >
          <Input
            type="password"
            value={serviceKey}
            onChange={(e) => setServiceKey(e.target.value)}
            placeholder={configured ? m.admin_widgets_tickets_key_placeholder_configured() : ''}
            className="max-w-md"
          />
        </Field>

        <div>
          <Button type="button" variant="secondary" size="sm" disabled={pipelinesMutation.isPending} onClick={handleFetchPipelines}>
            {pipelinesMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {m.admin_widgets_tickets_fetch_pipelines()}
          </Button>
        </div>

        <div className="space-y-3">
          {targets.map((target, index) => {
            const selectedPipeline = pipelines?.find((p) => p.id === target.pipeline_id)
            return (
              <div key={index} className="flex flex-wrap items-end gap-2 rounded-md border border-gray-100 bg-gray-50 p-3">
                <div className="space-y-1.5">
                  <Label htmlFor={`widget-tickets-target-label-${index}`}>
                    {m.admin_widgets_tickets_target_label_placeholder()}
                  </Label>
                  <Input
                    id={`widget-tickets-target-label-${index}`}
                    value={target.label}
                    onChange={(e) => updateLabel(index, e.target.value)}
                    className="w-40"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor={`widget-tickets-target-pipeline-${index}`}>
                    {m.admin_widgets_tickets_pipeline_placeholder()}
                  </Label>
                  <Select
                    id={`widget-tickets-target-pipeline-${index}`}
                    value={target.pipeline_id}
                    onChange={(e) => updatePipeline(index, e.target.value)}
                    disabled={!pipelines}
                    className="w-44"
                  >
                    {!pipelines && (
                      <option value={target.pipeline_id}>
                        {target.pipeline_id || m.admin_widgets_tickets_pipeline_placeholder()}
                      </option>
                    )}
                    {pipelines?.map((pipeline) => (
                      <option key={pipeline.id} value={pipeline.id}>
                        {pipeline.label}
                      </option>
                    ))}
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor={`widget-tickets-target-stage-${index}`}>
                    {m.admin_widgets_tickets_stage_placeholder()}
                  </Label>
                  <Select
                    id={`widget-tickets-target-stage-${index}`}
                    value={target.stage_id}
                    onChange={(e) => updateStage(index, e.target.value)}
                    disabled={!selectedPipeline}
                    className="w-44"
                  >
                    {!selectedPipeline && (
                      <option value={target.stage_id}>
                        {target.stage_id || m.admin_widgets_tickets_stage_placeholder()}
                      </option>
                    )}
                    {selectedPipeline?.stages.map((stage) => (
                      <option key={stage.id} value={stage.id}>
                        {stage.label}
                      </option>
                    ))}
                  </Select>
                </div>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  aria-label={m.admin_widgets_tickets_remove_target()}
                  onClick={() => removeTarget(index)}
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
            )
          })}
        </div>

        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={targets.length >= MAX_TICKET_TARGETS}
          onClick={addTarget}
        >
          <Plus className="h-4 w-4" />
          {m.admin_widgets_tickets_add_target()}
        </Button>

        {(saveMutation.error || deleteMutation.error) && (
          <p className="text-sm text-[var(--color-destructive)]">
            {ticketIntegrationErrorMessage(saveMutation.error ?? deleteMutation.error)}
          </p>
        )}

        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="button"
            size="sm"
            disabled={saveMutation.isPending || !keyOk || !targetsValid || !portalIdValid}
            onClick={handleSave}
          >
            {saveMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {m.admin_shared_save()}
          </Button>
          {configured && (
            <InlineDeleteConfirm
              isConfirming={confirmingDelete}
              isPending={deleteMutation.isPending}
              label={m.admin_widgets_tickets_delete_confirm()}
              cancelLabel={m.admin_users_cancel()}
              onConfirm={handleDelete}
              onCancel={() => setConfirmingDelete(false)}
            >
              <Button type="button" variant="destructive" size="sm" onClick={() => setConfirmingDelete(true)}>
                {m.admin_widgets_tickets_delete_button()}
              </Button>
            </InlineDeleteConfirm>
          )}
        </div>
      </div>
      )}
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
