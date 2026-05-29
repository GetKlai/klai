import { useState } from "react"
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  Copy,
  Loader2,
  Save,
  Trash2,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select } from "@/components/ui/select"
import { StepIndicator, type StepItem } from "@/components/ui/step-indicator"
import { Textarea } from "@/components/ui/textarea"
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
  FeedbackMetaRow,
  buildFeedbackDebugInstructions,
  defaultResolutionSummary,
  feedbackActionErrorMessage,
  feedbackItemKindLabel,
  feedbackItemStatusLabel,
  feedbackKindLabel,
  feedbackLinkTypeLabel,
  feedbackResolveLabel,
  feedbackSubmissionReporterLabel,
} from "./-feedback-helpers"

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
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false)
  const [itemStep, setItemStep] = useState<'understand' | 'debug' | 'fix' | 'message'>(
    'understand',
  )
  const resolveLabel = feedbackResolveLabel(item.kind)
  const isClosed = CLOSED_FEEDBACK_ITEM_STATUSES.has(status)
  const debugInstructions = buildFeedbackDebugInstructions(item, submissions, fmtDate)
  const itemStepOrder: Array<'understand' | 'debug' | 'fix' | 'message'> =
    item.kind === 'bug'
      ? ['understand', 'debug', 'fix', 'message']
      : ['understand', 'fix', 'message']
  const itemStepIndex = Math.max(0, itemStepOrder.indexOf(itemStep))
  const itemWizardSteps: StepItem[] = itemStepOrder.map((step) => ({
    label:
      step === 'understand'
        ? m.platform_feedback_item_details()
        : step === 'debug'
          ? m.platform_feedback_copy_debug_title()
          : step === 'fix'
            ? m.platform_feedback_follow_up_title()
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
    updateItem.mutate({
      itemId: item.id,
      status,
      title: title.trim(),
      summary: summary.trim() || null,
    })
  }
  const closeItem = () => {
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
          setResolveNotice(
            m.platform_feedback_update_created({
              count: String(result.notifications.length),
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
  const copyDebugInstructions = async () => {
    if (typeof navigator === 'undefined' || !navigator.clipboard) {
      setCopyNotice(m.platform_feedback_copy_debug_failed())
      return
    }
    try {
      await navigator.clipboard.writeText(debugInstructions)
      setCopyNotice(m.platform_feedback_copy_debug_copied())
    } catch {
      setCopyNotice(m.platform_feedback_copy_debug_failed())
    }
  }

  return (
    <div className="space-y-8">
      <StepIndicator steps={itemWizardSteps} currentIndex={itemStepIndex} />

      {itemStep === 'understand' && (
        <>
      <section className="space-y-4">
        <div>
          <h2 className="mt-1 text-xl font-display-bold text-gray-900">{title}</h2>
          <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-gray-700">
            {summary || m.platform_feedback_no_description()}
          </p>
        </div>
        <div className="grid gap-3 text-sm text-gray-600 sm:grid-cols-2 lg:grid-cols-4">
          <FeedbackMetaRow label={m.platform_col_status()} value={feedbackItemStatusLabel(status)} />
          <FeedbackMetaRow label={m.platform_col_created()} value={fmtDate(item.created_at)} />
          <FeedbackMetaRow label={m.platform_feedback_col_updated()} value={fmtDate(item.updated_at)} />
          <FeedbackMetaRow
            label={m.platform_feedback_item_signal()}
            value={m.platform_feedback_reporter_counts({
              orgs: String(item.org_count),
              users: String(item.user_count),
            })}
          />
        </div>
        <div className="flex flex-wrap gap-2 text-xs text-gray-500">
          <Badge variant="outline">{feedbackItemKindLabel(item.kind)}</Badge>
          <Badge variant={isClosed ? 'secondary' : 'outline'}>
            {feedbackItemStatusLabel(status)}
          </Badge>
          <Badge variant="outline">
            {m.platform_feedback_org_count({ count: String(item.org_count) })}
          </Badge>
          <Badge variant="outline">
            {m.platform_feedback_user_count({ count: String(item.user_count) })}
          </Badge>
          <Badge variant="outline">
            {m.platform_feedback_score({ score: String(item.priority_score) })}
          </Badge>
          {item.area && <Badge variant="outline">{item.area}</Badge>}
        </div>
      </section>

      <section className="space-y-3">
        <h3 className="text-sm font-medium text-gray-900">
          {m.platform_feedback_linked_feedback({ count: String(submissions.length) })}
        </h3>
        {submissions.length === 0 ? (
          <p className="rounded-lg border border-gray-200 px-4 py-3 text-sm text-gray-600">
            {m.platform_feedback_no_linked_feedback_warning()}
          </p>
        ) : (
          <div className="divide-y divide-gray-200 border-t border-b border-gray-200">
            {submissions.map((submission) => (
              <div key={submission.id} className="py-4">
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
        )}
      </section>
        </>
      )}

      {item.kind === 'bug' && itemStep === 'debug' && (
        <section className="space-y-4">
          <div>
            <h3 className="mt-1 text-base font-display-bold text-gray-900">
              {m.platform_feedback_copy_debug_title()}
            </h3>
            <p className="mt-1 text-sm leading-6 text-gray-600">
              {m.platform_feedback_copy_debug_description()}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button type="button" onClick={() => void copyDebugInstructions()}>
              <Copy className="h-4 w-4" />
              {m.platform_feedback_copy_debug_button()}
            </Button>
            {copyNotice && (
              <p className="text-sm text-[var(--color-success)]">
                {copyNotice}
              </p>
            )}
          </div>
        </section>
      )}

      {itemStep === 'fix' && (
        <section className="space-y-4">
        <div>
          <h3 className="mt-1 text-base font-display-bold text-gray-900">
            {m.platform_feedback_follow_up_title()}
          </h3>
        </div>
        <div className="space-y-4">
          <div className="space-y-1.5">
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
          <div className="space-y-1.5">
            <Label htmlFor={`feedback-item-status-${item.id}`}>
              {m.platform_col_status()}
            </Label>
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
          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="button"
              variant="secondary"
              disabled={updateItem.isPending || deleteItem.isPending}
              onClick={() => setConfirmDeleteOpen(true)}
            >
              {deleteItem.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Trash2 className="h-4 w-4" />
              )}
              {m.platform_feedback_delete_item()}
            </Button>
            <Button
              type="button"
              disabled={updateItem.isPending || deleteItem.isPending || title.trim().length < 3}
              onClick={saveItem}
            >
              {updateItem.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Save className="h-4 w-4" />
              )}
              {m.admin_shared_save()}
            </Button>
          </div>
        </div>
        {updateItem.isSuccess && (
          <p className="text-sm text-[var(--color-success)]">
            {m.platform_feedback_item_saved()}
          </p>
        )}
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
        {isClosed && resolutionSummary && (
          <div className="rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-900">
            {resolutionSummary}
          </div>
        )}
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
        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="button"
            variant="secondary"
            disabled={
              resolveItem.isPending ||
              resolutionSummary.trim().length < 3 ||
              (!notifyInApp && !notifyEmail)
            }
            onClick={closeItem}
          >
            {resolveItem.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <CheckCircle2 className="h-4 w-4" />
            )}
            {resolveItem.isPending
              ? m.platform_feedback_resolving()
              : isClosed
                ? m.platform_feedback_resend_update()
                : resolveLabel.button}
          </Button>
          {resolveNotice && (
            <p className="text-sm text-[var(--color-success)]">
              {resolveNotice}
            </p>
          )}
          {resolveError && (
            <p className="text-sm text-[var(--color-destructive)]">
              {resolveError}
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
