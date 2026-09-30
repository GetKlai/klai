// SPEC-KNOWLEDGE-ESCALATION-001 §5: the ticket UI under the review form. Two
// pieces render independently because they answer different questions —
// TicketCreatePanel is "start a new ticket" (opened by ReviewForm's "Maak
// ticket" button, closed on cancel or a successful create) and TicketHistory
// is "what already happened" (visible whenever the conversation has tickets,
// even when creating a new one is unavailable — e.g. a test-marked
// conversation keeps its ticket history after being marked).
import { useState } from 'react'
import { AlertTriangle, Check, ExternalLink, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/apiFetch'
import { _isSafeHttpUrl } from './urlAllowlist'
import {
  useCreateTicket,
  useTicketPreview,
  type ConversationTicketInfo,
  type TicketOut,
} from './api'
import * as m from '@/paraglide/messages'

// HubSpot's own lifecycle stage values (SPEC §2.7): shown as given, mapped to
// a label where we have one. An unmapped value renders raw rather than
// disappearing, so a HubSpot-side stage the tenant added still shows something.
const LIFECYCLE_LABEL: Record<string, () => string> = {
  subscriber: m.activity_ticket_lifecycle_subscriber,
  lead: m.activity_ticket_lifecycle_lead,
  marketingqualifiedlead: m.activity_ticket_lifecycle_mql,
  salesqualifiedlead: m.activity_ticket_lifecycle_sql,
  opportunity: m.activity_ticket_lifecycle_opportunity,
  customer: m.activity_ticket_lifecycle_customer,
  evangelist: m.activity_ticket_lifecycle_evangelist,
  other: m.activity_ticket_lifecycle_other,
}

/** Same toggle-button look as ReviewForm's verdict/cause pickers. */
function TargetToggle({
  pressed,
  onClick,
  children,
}: {
  pressed: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={
        pressed
          ? 'rounded-lg border border-gray-900 bg-[var(--color-secondary)] px-2.5 py-1 text-xs font-medium text-gray-900'
          : 'klai-hover rounded-lg border border-gray-200 px-2.5 py-1 text-xs text-gray-600'
      }
    >
      {children}
    </button>
  )
}

/** Backend `detail` -> the message shown to the reviewer. A 502's detail is
    already the human-readable HubSpot failure reason (§4.4 step 6); the 409
    and 422 codes below need mapping to something a reviewer can act on. */
function ticketErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.detail === 'ticket_exists') return m.activity_ticket_error_exists()
    if (err.detail === 'ticket_pending') return m.activity_ticket_error_pending()
    if (err.detail === 'unknown_target') return m.activity_ticket_error_unknown_target()
    if (err.detail) return err.detail
  }
  return m.activity_ticket_create_failed()
}

function ContactLine({ preview }: { preview: ReturnType<typeof useTicketPreview> }) {
  if (preview.isLoading) {
    return (
      <p className="flex items-center gap-2 text-xs text-gray-600">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        {m.activity_ticket_preview_loading()}
      </p>
    )
  }
  if (preview.isError) {
    return <p className="text-xs text-[var(--color-destructive)]">{m.activity_ticket_preview_failed()}</p>
  }
  const data = preview.data
  if (!data) return null
  if (data.contact === 'new') {
    return <p className="text-xs text-gray-700">{m.activity_ticket_contact_new()}</p>
  }
  const stageLabel = data.lifecycle_stage
    ? (LIFECYCLE_LABEL[data.lifecycle_stage] ?? (() => data.lifecycle_stage as string))()
    : null
  return (
    <p className="text-xs text-gray-700">
      {m.activity_ticket_contact_existing()}
      {stageLabel ? ` · ${stageLabel}` : ''}
      {data.contact_name ? ` · ${data.contact_name}` : ''}
      {data.company_name ? ` · ${data.company_name}` : ''}
    </p>
  )
}

/**
 * The inline "start a ticket" panel (SPEC §5): pick a target, see the
 * contact HubSpot would use, confirm or cancel. Targets that already have a
 * `created` ticket are not offered again — the history list below covers
 * those.
 */
export function TicketCreatePanel({
  conversationId,
  ticket,
  onClose,
}: {
  conversationId: string | number
  ticket: ConversationTicketInfo
  onClose: () => void
}) {
  const preview = useTicketPreview(conversationId, true)
  const createTicket = useCreateTicket(conversationId)
  const [targetKey, setTargetKey] = useState<string | null>(null)

  const createdKeys = new Set(
    ticket.tickets.filter((t) => t.status === 'created').map((t) => t.target_key),
  )
  const availableTargets = ticket.targets.filter((target) => !createdKeys.has(target.key))

  const submit = () => {
    if (!targetKey) return
    createTicket.mutate(
      { target_key: targetKey },
      {
        onSuccess: () => {
          toast.success(m.activity_ticket_created_toast())
          onClose()
        },
        onError: (err) => toast.error(ticketErrorMessage(err)),
      },
    )
  }

  return (
    <div className="mt-3 space-y-3 rounded-lg border border-gray-200 bg-gray-50 p-3">
      <div className="flex flex-wrap gap-1.5">
        {availableTargets.map((target) => (
          <TargetToggle key={target.key} pressed={targetKey === target.key} onClick={() => setTargetKey(target.key)}>
            {target.label}
          </TargetToggle>
        ))}
      </div>
      <ContactLine preview={preview} />
      <div className="flex items-center gap-2">
        <Button type="button" size="sm" disabled={!targetKey || createTicket.isPending} onClick={submit}>
          {createTicket.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {m.activity_ticket_create()}
        </Button>
        <Button type="button" variant="outline" size="sm" onClick={onClose}>
          {m.activity_ticket_cancel()}
        </Button>
      </div>
    </div>
  )
}

function TicketRow({ ticket, onRetry, pending }: { ticket: TicketOut; onRetry: () => void; pending: boolean }) {
  if (ticket.status === 'pending') {
    return (
      <p className="flex items-center gap-2 text-xs text-gray-600">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        {m.activity_ticket_pending({ label: ticket.target_label })}
      </p>
    )
  }
  if (ticket.status === 'failed') {
    return (
      <p className="flex flex-wrap items-center gap-2 text-xs text-[var(--color-destructive)]">
        <span>{m.activity_ticket_failed({ label: ticket.target_label, reason: ticket.error ?? '' })}</span>
        <Button type="button" variant="outline" size="sm" disabled={pending} onClick={onRetry}>
          {pending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {m.activity_ticket_retry()}
        </Button>
      </p>
    )
  }
  const safeUrl = ticket.ticket_url && _isSafeHttpUrl(ticket.ticket_url) ? ticket.ticket_url : null
  // SPEC §5: a step HubSpot refused for a missing scope is named per ticket,
  // so an admin sees which scope to add instead of a silently thinner ticket.
  const skipped = [
    ticket.contact_status === 'create_forbidden' ? m.activity_ticket_contact_create_forbidden() : null,
    ticket.company_status === 'forbidden' ? m.activity_ticket_company_forbidden() : null,
  ].filter((line) => line !== null)
  return (
    <div className="space-y-0.5">
      <p className="flex flex-wrap items-center gap-1.5 text-xs text-gray-700">
        <Check className="h-3.5 w-3.5 text-[var(--color-success-text)]" aria-hidden="true" />
        <span>
          {m.activity_ticket_created({
            label: ticket.target_label,
            date: new Date(ticket.created_at).toLocaleDateString(),
            name: ticket.created_by_name ?? '',
          })}
        </span>
        {safeUrl && (
          <a
            href={safeUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-gray-900 underline underline-offset-2"
          >
            {m.activity_ticket_open_hubspot()}
            <ExternalLink className="h-3 w-3" />
          </a>
        )}
      </p>
      {skipped.map((line) => (
        <p key={line} className="flex items-center gap-1.5 pl-5 text-xs text-[var(--color-warning-text)]">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {line}
        </p>
      ))}
    </div>
  )
}

/** The conversation's ticket history (SPEC §5): stays visible even when
    `ticket.available` is false, so marking a conversation as a test after a
    ticket exists never hides that it happened. */
export function TicketHistory({
  tickets,
  conversationId,
}: {
  tickets: TicketOut[]
  conversationId: string | number
}) {
  const retry = useCreateTicket(conversationId)
  if (tickets.length === 0) return null
  return (
    <div className="mt-3 space-y-1.5">
      {tickets.map((ticket) => (
        <TicketRow
          key={ticket.target_key}
          ticket={ticket}
          pending={retry.isPending}
          onRetry={() =>
            retry.mutate(
              { target_key: ticket.target_key },
              { onError: (err) => toast.error(ticketErrorMessage(err)) },
            )
          }
        />
      ))}
    </div>
  )
}
