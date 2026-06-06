import { useState } from "react"
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  ChevronRight,
  Copy,
  Loader2,
  Save,
  X,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select } from "@/components/ui/select"
import { StepIndicator, type StepItem } from "@/components/ui/step-indicator"
import { Textarea } from "@/components/ui/textarea"
import { BorderedRowActionIconButton, RowActionGroup } from "@/components/ui/row-action"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import * as m from "@/paraglide/messages"
import {
  usePlatformFeedbackDeleteItem,
  usePlatformFeedbackItem,
  usePlatformFeedbackResolveItem,
  usePlatformFeedbackUpdateItem,
} from "../../-hooks"
import type {
  PlatformFeedbackItem,
  PlatformFeedbackLinkedSubmission,
} from "../../-types"
import {
  CLOSED_FEEDBACK_ITEM_STATUSES,
  buildFeedbackDebugInstructions,
  buildFeedbackFeatureInstructions,
  defaultResolutionSummary,
  feedbackActionErrorMessage,
  feedbackItemKindLabel,
  feedbackItemStatusLabel,
  feedbackKindLabel,
  feedbackLinkTypeLabel,
  feedbackResolveLabel,
  feedbackSubmissionReporterLabel,
} from "./-feedback-helpers"

const FEEDBACK_UPDATE_CREATED_STATES = new Set(['sent', 'queued'])

export function FeedbackItemDetailPanel({
  itemId,
  fmtDate,
  onClose,
}: {
  itemId: number
  fmtDate: (s: string | null) => string
  onClose: () => void
}) {
  const detail = usePlatformFeedbackItem(itemId)

  return (
    <div className="space-y-6">
      <div className="flex items-start gap-3">
        <div className="flex-1">
          <h1 className="page-title text-[26px] font-display-bold text-gray-900">
            {m.platform_feedback_item_title()}
          </h1>
          <p className="mt-1 text-sm text-gray-400">
            {m.platform_feedback_item_description()}
          </p>
        </div>
        <Button type="button" variant="ghost" size="sm" onClick={onClose}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.platform_back_to_feedback()}
        </Button>
      </div>

      {detail.isLoading ? (
        <div className="flex items-center gap-2 text-sm text-gray-500">
          <Loader2 className="h-4 w-4 animate-spin" />
          {m.admin_shared_loading()}
        </div>
      ) : detail.data ? (
        <FeedbackItemDetailForm
          key={detail.data.item.id}
          item={detail.data.item}
          submissions={detail.data.submissions}
          fmtDate={fmtDate}
          onClose={onClose}
        />
      ) : (
        <p className="text-sm text-gray-500">{m.platform_feedback_item_not_found()}</p>
      )}
    </div>
  )
}

function FeedbackItemDetailForm({
  item,
  submissions,
  fmtDate,
  onClose,
}: {
  item: PlatformFeedbackItem
  submissions: PlatformFeedbackLinkedSubmission[]
  fmtDate: (s: string | null) => string
  onClose: () => void
}) {
  const updateItem = usePlatformFeedbackUpdateItem()
  const resolveItem = usePlatformFeedbackResolveItem()
  const deleteItem = usePlatformFeedbackDeleteItem()
  const [status, setStatus] = useState(item.status)
  const [title, setTitle] = useState(item.title)
  const [summary, setSummary] = useState(item.summary ?? '')
  const [resolutionSummary, setResolutionSummary] = useState(
    item.resolution_summary ?? defaultResolutionSummary(item),
  )
  const [notifyInApp, setNotifyInApp] = useState(true)
  const [notifyEmail, setNotifyEmail] = useState(false)
  const [resolveNotice, setResolveNotice] = useState<string | null>(null)
  const [resolveError, setResolveError] = useState<string | null>(null)
  const [copyNotice, setCopyNotice] = useState<string | null>(null)
  const [hasCreatedUpdate, setHasCreatedUpdate] = useState(
    FEEDBACK_UPDATE_CREATED_STATES.has(item.notification_state ?? ''),
  )
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false)
  const [isEditing, setIsEditing] = useState(false)
  const [itemStep, setItemStep] = useState<'understand' | 'debug' | 'message'>(
    'understand',
  )
  const resolveLabel = feedbackResolveLabel(item.kind)
  const isClosed = CLOSED_FEEDBACK_ITEM_STATUSES.has(status)
  const hasLlmPrompt = item.kind === 'bug' || item.kind === 'feature'
  const llmPrompt =
    item.kind === 'feature'
      ? buildFeedbackFeatureInstructions(item, submissions, fmtDate)
      : hasLlmPrompt
        ? buildFeedbackDebugInstructions(item, submissions, fmtDate)
        : ''
  const llmPromptLabel =
    item.kind === 'feature'
      ? {
          title: m.platform_feedback_copy_feature_prompt_title(),
          description: m.platform_feedback_copy_feature_prompt_description(),
          button: m.platform_feedback_copy_feature_prompt_button(),
          copied: m.platform_feedback_copy_feature_prompt_copied(),
        }
      : {
          title: m.platform_feedback_copy_debug_title(),
          description: m.platform_feedback_copy_debug_description(),
          button: m.platform_feedback_copy_debug_button(),
          copied: m.platform_feedback_copy_debug_copied(),
        }
  const itemStepOrder: Array<'understand' | 'debug' | 'message'> =
    hasLlmPrompt
      ? ['understand', 'debug', 'message']
      : ['understand', 'message']
  const itemStepIndex = Math.max(0, itemStepOrder.indexOf(itemStep))
  const itemWizardSteps: StepItem[] = itemStepOrder.map((step) => ({
    label:
      step === 'understand'
        ? m.platform_feedback_item_details()
        : step === 'debug'
          ? llmPromptLabel.title
          : resolveLabel.title,
    onClick: () => setItemStep(step),
  }))
  const previousItemStep = () => {
    if (itemStepIndex > 0) setItemStep(itemStepOrder[itemStepIndex - 1])
  }
  const nextItemStep = () => {
    if (itemStepIndex < itemStepOrder.length - 1) {
      setItemStep(itemStepOrder[itemStepIndex + 1])
    }
  }
  const saveItem = () => {
    updateItem.mutate(
      { itemId: item.id, title: title.trim(), summary: summary.trim() || null },
      { onSuccess: () => setIsEditing(false) },
    )
  }
  const cancelEdit = () => {
    setTitle(item.title)
    setSummary(item.summary ?? '')
    setIsEditing(false)
  }
  const closeItem = () => {
    // Non-resolved outcomes (reopen / dismiss) just persist the status; no
    // customer message is sent for those.
    if (status !== 'resolved') {
      updateItem.mutate({ itemId: item.id, status }, { onSuccess: onClose })
      return
    }
    const channels: Array<'in_app' | 'email'> = []
    if (notifyInApp) channels.push('in_app')
    if (notifyEmail) channels.push('email')
    setResolveNotice(m.platform_feedback_update_creating())
    setResolveError(null)
    resolveItem.mutate(
      {
        itemId: item.id,
        resolution_summary: resolutionSummary.trim(),
        subject: `${resolveLabel.subject}: ${title.trim() || item.title}`,
        channels,
      },
      {
        onSuccess: (result) => {
          setStatus(result.item.status)
          setResolutionSummary(result.item.resolution_summary ?? '')
          setHasCreatedUpdate(
            FEEDBACK_UPDATE_CREATED_STATES.has(result.item.notification_state ?? ''),
          )
          setResolveNotice(
            m.platform_feedback_update_created({
              count: String(result.recipient_count),
            }),
          )
          setResolveError(null)
        },
        onError: (error) => {
          setResolveNotice(null)
          setResolveError(feedbackActionErrorMessage(error))
        },
      },
    )
  }
  const copyLlmPrompt = async () => {
    if (typeof navigator === 'undefined' || !navigator.clipboard) {
      setCopyNotice(m.platform_feedback_copy_debug_failed())
      return
    }
    try {
      await navigator.clipboard.writeText(llmPrompt)
      setCopyNotice(llmPromptLabel.copied)
    } catch {
      setCopyNotice(m.platform_feedback_copy_debug_failed())
    }
  }

  return (
    <div className="space-y-8">
      <StepIndicator steps={itemWizardSteps} currentIndex={itemStepIndex} />

      {itemStep === 'understand' && (
        <>
      <section className="space-y-3">
        <div className="flex items-start justify-between gap-3">
          {isEditing ? (
            <div className="flex-1 space-y-1.5">
              <Label htmlFor={`feedback-item-title-${item.id}`}>
                {m.platform_feedback_title_placeholder()}
              </Label>
              <Input
                id={`feedback-item-title-${item.id}`}
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder={m.platform_feedback_title_placeholder()}
              />
            </div>
          ) : (
            <h2 className="text-xl font-display-bold text-gray-900">{title}</h2>
          )}
          <div className="flex shrink-0 items-center gap-1">
            <Badge variant={isClosed ? 'secondary' : 'outline'}>
              {feedbackItemStatusLabel(status)}
            </Badge>
            {!isEditing && (
              <RowActionGroup className="ml-1">
                <BorderedRowActionIconButton
                  action="edit"
                  label={m.platform_feedback_edit_item_title()}
                  onClick={() => setIsEditing(true)}
                />
                <BorderedRowActionIconButton
                  action="delete"
                  label={m.platform_feedback_delete_item()}
                  onClick={() => setConfirmDeleteOpen(true)}
                />
              </RowActionGroup>
            )}
          </div>
        </div>

        {isEditing ? (
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor={`feedback-item-summary-${item.id}`}>
                {m.platform_feedback_short_note_placeholder()}
              </Label>
              <Textarea
                id={`feedback-item-summary-${item.id}`}
                value={summary}
                onChange={(event) => setSummary(event.target.value)}
                rows={4}
                placeholder={m.platform_feedback_short_note_placeholder()}
              />
            </div>
            <div className="flex items-center gap-2">
              <Button
                type="button"
                size="sm"
                disabled={updateItem.isPending || title.trim().length < 3}
                onClick={saveItem}
              >
                {updateItem.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Save className="h-4 w-4" />
                )}
                {m.admin_shared_save()}
              </Button>
              <Button type="button" variant="ghost" size="sm" onClick={cancelEdit}>
                <X className="h-4 w-4" />
                {m.admin_users_cancel()}
              </Button>
            </div>
          </div>
        ) : (
          <p className="whitespace-pre-wrap text-sm leading-6 text-gray-700">
            {summary || m.platform_feedback_no_description()}
          </p>
        )}

        <p className="text-xs text-gray-400">
          {[
            feedbackItemKindLabel(item.kind),
            item.area,
            m.platform_feedback_reporter_counts({
              orgs: String(item.org_count),
              users: String(item.user_count),
            }),
            m.platform_feedback_score({ score: String(item.priority_score) }),
          ]
            .filter(Boolean)
            .join(' · ')}
        </p>
        <p className="text-xs text-gray-400">
          {m.platform_col_created()} {fmtDate(item.created_at)} · {m.platform_feedback_col_updated()}{' '}
          {fmtDate(item.updated_at)}
        </p>
        {updateItem.isSuccess && !isEditing && (
          <p className="text-sm text-[var(--color-success)]">
            {m.platform_feedback_item_saved()}
          </p>
        )}
      </section>

      {submissions.length === 0 ? (
        <section className="space-y-3">
          <h3 className="text-sm font-medium text-gray-900">
            {m.platform_feedback_linked_feedback({ count: '0' })}
          </h3>
          <p className="rounded-lg border border-gray-200 px-4 py-3 text-sm text-gray-600">
            {m.platform_feedback_no_linked_feedback_warning()}
          </p>
        </section>
      ) : (
        <details className="group rounded-xl border border-gray-200 bg-white">
          <summary className="flex min-h-12 cursor-pointer list-none items-center gap-3 px-4 py-3 text-sm text-gray-700 [&::-webkit-details-marker]:hidden">
            <ChevronRight className="h-4 w-4 shrink-0 text-gray-400 transition-transform group-open:rotate-90" />
            <span className="min-w-0 flex-1 font-medium">
              {m.platform_feedback_linked_feedback({ count: String(submissions.length) })}
            </span>
          </summary>
          <div className="divide-y divide-gray-200 border-t border-gray-200">
            {submissions.map((submission) => (
              <div key={submission.id} className="px-4 py-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="outline">{feedbackKindLabel(submission.event_type)}</Badge>
                  <Badge variant="secondary">{feedbackLinkTypeLabel(submission.link_type)}</Badge>
                  <span className="text-xs text-gray-400">{fmtDate(submission.created_at)}</span>
                </div>
                <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-gray-900">
                  {submission.raw_text}
                </p>
                <div className="mt-2 grid gap-2 text-xs text-gray-400 sm:grid-cols-2">
                  <span>
                    {submission.org_name ?? submission.org_slug ?? m.platform_feedback_unknown_organization()}
                    {feedbackSubmissionReporterLabel(submission)
                      ? ` / ${feedbackSubmissionReporterLabel(submission)}`
                      : ''}
                  </span>
                  <span>{submission.page_url || submission.route_id || '-'}</span>
                </div>
              </div>
            ))}
          </div>
        </details>
      )}
        </>
      )}

      {hasLlmPrompt && itemStep === 'debug' && (
        <section className="space-y-4">
          <div>
            <h3 className="mt-1 text-base font-display-bold text-gray-900">
              {llmPromptLabel.title}
            </h3>
            <p className="mt-1 text-sm leading-6 text-gray-600">
              {llmPromptLabel.description}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button type="button" onClick={() => void copyLlmPrompt()}>
              <Copy className="h-4 w-4" />
              {llmPromptLabel.button}
            </Button>
            {copyNotice && (
              <p className="text-sm text-[var(--color-success)]">
                {copyNotice}
              </p>
            )}
          </div>
        </section>
      )}

      {itemStep === 'message' && (
        <section className="space-y-4">
        <div>
          <h3 className="mt-1 text-base font-display-bold text-gray-900">{resolveLabel.title}</h3>
          <p className="mt-1 text-sm leading-6 text-gray-600">
            {m.platform_feedback_resolve_description()}
          </p>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`feedback-item-status-${item.id}`}>{m.platform_col_status()}</Label>
          <Select
            id={`feedback-item-status-${item.id}`}
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            <option value="open">{m.platform_feedback_status_open()}</option>
            <option value="resolved">{m.platform_feedback_status_resolved()}</option>
            <option value="dismissed">{m.platform_feedback_status_dismissed()}</option>
          </Select>
        </div>
        {status === 'resolved' && (
          <>
            <div className="space-y-1.5">
              <Label htmlFor={`feedback-item-resolution-${item.id}`}>
                {m.platform_feedback_resolution_placeholder()}
              </Label>
              <Textarea
                id={`feedback-item-resolution-${item.id}`}
                value={resolutionSummary}
                onChange={(event) => setResolutionSummary(event.target.value)}
                rows={3}
                placeholder={m.platform_feedback_resolution_placeholder()}
              />
            </div>
            <div className="flex flex-wrap items-center gap-5">
              <Checkbox
                checked={notifyInApp}
                onChange={(event) => setNotifyInApp(event.target.checked)}
                label={m.platform_feedback_channel_in_app()}
              />
              <Checkbox
                checked={notifyEmail}
                onChange={(event) => setNotifyEmail(event.target.checked)}
                label={m.platform_feedback_channel_email()}
              />
            </div>
          </>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="button"
            variant="secondary"
            disabled={
              status === 'resolved'
                ? resolveItem.isPending ||
                  resolutionSummary.trim().length < 3 ||
                  (!notifyInApp && !notifyEmail)
                : updateItem.isPending || status === item.status
            }
            onClick={closeItem}
          >
            {resolveItem.isPending || updateItem.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <CheckCircle2 className="h-4 w-4" />
            )}
            {status !== 'resolved'
              ? m.admin_shared_save()
              : resolveItem.isPending
                ? m.platform_feedback_resolving()
                : isClosed && hasCreatedUpdate
                  ? m.platform_feedback_resend_update()
                  : resolveLabel.button}
          </Button>
          {resolveNotice && (
            <p className="text-sm text-[var(--color-success)]">{resolveNotice}</p>
          )}
          {resolveError && (
            <p className="text-sm text-[var(--color-destructive)]">{resolveError}</p>
          )}
          {updateItem.isSuccess && status !== 'resolved' && (
            <p className="text-sm text-[var(--color-success)]">
              {m.platform_feedback_item_saved()}
            </p>
          )}
        </div>
      </section>
      )}

      <div className="flex items-center justify-between pt-2">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={itemStepIndex === 0}
          onClick={previousItemStep}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_shared_wizard_previous()}
        </Button>
        {itemStepIndex < itemStepOrder.length - 1 && (
          <Button type="button" onClick={nextItemStep}>
            {m.admin_shared_wizard_next()}
            <ArrowRight className="h-4 w-4 ml-2" />
          </Button>
        )}
      </div>
      <AlertDialog open={confirmDeleteOpen} onOpenChange={setConfirmDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{m.platform_feedback_delete_item_title()}</AlertDialogTitle>
            <AlertDialogDescription>
              {m.platform_feedback_delete_item_description()}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{m.admin_users_cancel()}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-[var(--color-destructive)] text-white hover:bg-[var(--color-destructive)]/90"
              onClick={() => {
                deleteItem.mutate(item.id, { onSuccess: onClose })
              }}
            >
              {m.platform_delete()}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
