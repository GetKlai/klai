import { useState } from "react"
import {
  ArchiveX,
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  LifeBuoy,
  Link2,
  Loader2,
  PlusCircle,
  Save,
  Search,
  Trash2,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
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
  usePlatformFeedbackCreateItem,
  usePlatformFeedbackDeleteSubmission,
  usePlatformFeedbackDismiss,
  usePlatformFeedbackItems,
  usePlatformFeedbackLinkItem,
  usePlatformFeedbackSupport,
  usePlatformFeedbackUpdateSubmission,
} from "../../-hooks"
import type { PlatformFeedbackSubmission } from "../../-types"
import {
  FeedbackMetaRow,
  feedbackFallbackSummary,
  feedbackItemKindLabel,
  feedbackItemSearchTerm,
  feedbackKindLabel,
  feedbackStatusLabel,
  feedbackSubmissionReporterLabel,
  feedbackSuggestionActionLabel,
  feedbackSuggestionPrimaryLabel,
  normalizedFeedbackKind,
} from "./-feedback-helpers"

export function FeedbackSubmissionDetailPanel({
  item,
  fmtDate,
  onClose,
}: {
  item: PlatformFeedbackSubmission
  fmtDate: (s: string | null) => string
  onClose: () => void
}) {
  const defaultKind =
    item.event_type === 'klai_assistant.problem_report' ? 'bug' : 'feature'
  const suggestion = item.triage_suggestion
  const bestCandidate = suggestion?.duplicate_candidates[0] ?? null
  const suggestedKind = normalizedFeedbackKind(suggestion?.classification, defaultKind)
  const suggestedTitle = (suggestion?.summary || item.raw_text || '').slice(0, 90)
  const suggestedSearch = feedbackItemSearchTerm(item, suggestion)
  const [itemSearch, setItemSearch] = useState(suggestedSearch)
  const [kind, setKind] = useState(suggestedKind)
  const [title, setTitle] = useState(suggestedTitle)
  const [summary, setSummary] = useState(suggestion?.summary ?? item.raw_text ?? '')
  const [area, setArea] = useState(suggestion?.suggested_area ?? '')
  const [draftStatus, setDraftStatus] = useState(item.status)
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false)
  const [triageAction, setTriageAction] = useState<
    'recommended' | 'link' | 'create' | 'support' | 'dismiss'
  >('recommended')
  const [submissionStep, setSubmissionStep] = useState<'report' | 'proposal' | 'decision'>(
    'report',
  )

  const items = usePlatformFeedbackItems(itemSearch, 'triage')
  const existingItems = items.data ?? []
  const bestSearchMatch = itemSearch.trim().length >= 4 ? (existingItems[0] ?? null) : null
  const updateSubmission = usePlatformFeedbackUpdateSubmission()
  const deleteSubmission = usePlatformFeedbackDeleteSubmission()
  const dismiss = usePlatformFeedbackDismiss()
  const support = usePlatformFeedbackSupport()
  const createItem = usePlatformFeedbackCreateItem()
  const linkItem = usePlatformFeedbackLinkItem()
  const busy =
    updateSubmission.isPending ||
    deleteSubmission.isPending ||
    dismiss.isPending ||
    support.isPending ||
    createItem.isPending ||
    linkItem.isPending
  const canTriage = item.status === 'new'
  const linkType =
    item.event_type === 'klai_assistant.problem_report'
      ? 'bug_repro'
      : suggestion?.classification === 'support_pattern'
        ? 'support_signal'
        : 'evidence'
  const recommendedAction =
    bestCandidate || bestSearchMatch
      ? 'link_existing'
      : items.isFetching
        ? 'review'
      : suggestion?.suggested_action || 'create_item'
  const recommendedItem = bestCandidate
    ? {
        id: bestCandidate.item_id,
        title: bestCandidate.title ?? `Item #${bestCandidate.item_id}`,
        kind: bestCandidate.kind,
        status: bestCandidate.status,
        area: bestCandidate.area,
      }
    : bestSearchMatch
      ? {
          id: bestSearchMatch.id,
          title: bestSearchMatch.title,
          kind: bestSearchMatch.kind,
          status: bestSearchMatch.status,
          area: bestSearchMatch.area,
        }
      : null
  const reportText = item.raw_text || feedbackFallbackSummary(item)
  const proposalSummary = suggestion?.summary || item.raw_text || feedbackFallbackSummary(item)
  const saveSubmissionStatus = () => {
    updateSubmission.mutate({
      submissionId: item.id,
      status: draftStatus,
    })
  }
  const acceptCreateItem = () => {
    const fallbackTitle = (suggestion?.summary || item.raw_text || '').slice(0, 90)
    createItem.mutate(
      {
        submissionId: item.id,
        kind: suggestedKind,
        title: fallbackTitle.trim(),
        summary: item.raw_text || suggestion?.summary || null,
        area: suggestion?.suggested_area || area.trim() || null,
        link_type: linkType,
      },
      { onSuccess: onClose },
    )
  }
  const acceptRecommendedAction = () => {
    if (recommendedAction === 'link_existing' && recommendedItem) {
      linkItem.mutate(
        {
          submissionId: item.id,
          item_id: recommendedItem.id,
          link_type: linkType,
        },
        { onSuccess: onClose },
      )
      return
    }
    if (recommendedAction === 'support') {
      support.mutate(item.id, { onSuccess: onClose })
      return
    }
    if (recommendedAction === 'dismiss') {
      dismiss.mutate(item.id, { onSuccess: onClose })
      return
    }
    acceptCreateItem()
  }
  const canAcceptRecommendedAction =
    (recommendedAction !== 'link_existing' || recommendedItem !== null) &&
    (recommendedAction !== 'create_item' || suggestedTitle.trim().length >= 3) &&
    recommendedAction !== 'review'
  const selectedActionClass =
    'border-gray-900 bg-gray-900 text-white hover:bg-gray-800 hover:text-white'
  const actionButtonClass =
    'h-auto min-h-14 justify-start rounded-lg px-4 py-3 text-left whitespace-normal'
  const submissionStepOrder: Array<'report' | 'proposal' | 'decision'> = [
    'report',
    'proposal',
    'decision',
  ]
  const activeSubmissionStep = canTriage ? submissionStep : 'report'
  const submissionStepIndex = Math.max(0, submissionStepOrder.indexOf(activeSubmissionStep))
  const submissionWizardSteps: StepItem[] = submissionStepOrder.map((step) => ({
    label:
      step === 'report'
        ? m.platform_feedback_read_report_title()
        : step === 'proposal'
          ? m.platform_feedback_suggestion_title()
          : m.platform_feedback_choose_action_title(),
    onClick: () => setSubmissionStep(step),
  }))
  const previousSubmissionStep = () => {
    if (submissionStepIndex > 0) setSubmissionStep(submissionStepOrder[submissionStepIndex - 1])
  }
  const nextSubmissionStep = () => {
    if (submissionStepIndex < submissionStepOrder.length - 1) {
      setSubmissionStep(submissionStepOrder[submissionStepIndex + 1])
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start gap-3">
        <div className="flex-1">
          <h1 className="page-title text-[26px] font-display-bold text-gray-900">
            {canTriage
              ? m.platform_feedback_triage_title()
              : m.platform_feedback_submission_detail_title()}
          </h1>
          <p className="mt-1 text-sm text-gray-400">
            {item.org_name ?? item.org_slug ?? m.platform_feedback_unknown_organization()} -{' '}
            {feedbackSubmissionReporterLabel(item)
              ? `${feedbackSubmissionReporterLabel(item)} - `
              : ''}
            {fmtDate(item.created_at)}
          </p>
        </div>
        <Button type="button" variant="ghost" size="sm" onClick={onClose}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.platform_back_to_feedback()}
        </Button>
      </div>

      <div className="space-y-8">
        {canTriage && (
          <StepIndicator steps={submissionWizardSteps} currentIndex={submissionStepIndex} />
        )}

        {activeSubmissionStep === 'report' && (
        <section className="space-y-4">
          <div>
            <h2 className="mt-1 text-base font-display-bold text-gray-900">
              {m.platform_feedback_read_report_title()}
            </h2>
            <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-gray-700">
              {reportText}
            </p>
          </div>
          <div className="grid gap-3 text-sm text-gray-600 sm:grid-cols-2 lg:grid-cols-3">
            <FeedbackMetaRow
              label={m.platform_col_organization()}
              value={item.org_name ?? item.org_slug ?? m.platform_feedback_unknown_organization()}
            />
            <FeedbackMetaRow
              label={m.platform_feedback_reporter_label()}
              value={feedbackSubmissionReporterLabel(item) ?? '-'}
            />
            <FeedbackMetaRow label={m.platform_feedback_page_url()} value={item.page_url ?? '-'} />
            <FeedbackMetaRow label={m.platform_feedback_route_id()} value={item.route_id ?? '-'} />
            <FeedbackMetaRow
              label={m.platform_feedback_context()}
              value={[item.locale, item.viewport].filter(Boolean).join(' / ') || '-'}
            />
            <FeedbackMetaRow label={m.platform_col_created()} value={fmtDate(item.created_at)} />
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant="outline">{feedbackKindLabel(item.event_type)}</Badge>
            <Badge variant={item.status === 'new' ? 'outline' : 'secondary'}>
              {feedbackStatusLabel(item.status)}
            </Badge>
            {(item.feedback_type || item.severity) && (
              <Badge variant="secondary">
                {item.feedback_type || item.severity}
              </Badge>
            )}
          </div>
        </section>
        )}

          {canTriage ? (
            <>
              {activeSubmissionStep === 'proposal' && (
              <section className="space-y-4">
                <div>
                  <h2 className="mt-1 text-base font-display-bold text-gray-900">
                    {m.platform_feedback_triage_proposal_title()}
                  </h2>
                  <p className="mt-2 text-sm leading-6 text-gray-700">{proposalSummary}</p>
                </div>
                <div className="flex flex-wrap gap-2 text-xs">
                  <Badge variant="outline">
                    {m.platform_feedback_suggestion_action({
                      action: feedbackSuggestionActionLabel(recommendedAction),
                    })}
                  </Badge>
                  <Badge variant="outline">
                    {m.platform_feedback_suggestion_type({
                      type: feedbackItemKindLabel(suggestedKind),
                    })}
                  </Badge>
                  {suggestion?.suggested_area && (
                    <Badge variant="outline">
                      {m.platform_feedback_suggestion_area({
                        area: suggestion.suggested_area,
                      })}
                    </Badge>
                  )}
                  {suggestion?.suggested_severity && (
                    <Badge variant="outline">
                      {m.platform_feedback_suggestion_severity({
                        severity: suggestion.suggested_severity,
                      })}
                    </Badge>
                  )}
                </div>
                {recommendedItem && (
                  <div className="rounded-lg border border-gray-200 px-4 py-3">
                    <p className="text-xs font-medium text-gray-500">
                      {m.platform_feedback_existing_item_found()}
                    </p>
                    <p className="mt-1 truncate text-sm font-medium text-gray-900">
                      {recommendedItem.title}
                    </p>
                    <p className="mt-1 text-xs text-gray-400">
                      {[recommendedItem.kind, recommendedItem.status, recommendedItem.area]
                        .filter(Boolean)
                        .join(' / ')}
                    </p>
                  </div>
                )}
              </section>
              )}

              {activeSubmissionStep === 'decision' && (
              <>
              <section className="space-y-4">
                <div>
                  <h2 className="mt-1 text-base font-display-bold text-gray-900">
                    {m.platform_feedback_choose_action_title()}
                  </h2>
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  <Button
                    type="button"
                    variant="secondary"
                    className={`${actionButtonClass} ${
                      triageAction === 'recommended' ? selectedActionClass : ''
                    }`}
                    onClick={() => setTriageAction('recommended')}
                  >
                    <CheckCircle2 className="h-4 w-4 shrink-0" />
                    {feedbackSuggestionPrimaryLabel(
                      recommendedAction,
                      recommendedItem?.title,
                      suggestedKind,
                    )}
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    className={`${actionButtonClass} ${
                      triageAction === 'link' ? selectedActionClass : ''
                    }`}
                    onClick={() => setTriageAction('link')}
                  >
                    <Link2 className="h-4 w-4 shrink-0" />
                    {m.platform_feedback_link_existing_title()}
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    className={`${actionButtonClass} ${
                      triageAction === 'create' ? selectedActionClass : ''
                    }`}
                    onClick={() => setTriageAction('create')}
                  >
                    <PlusCircle className="h-4 w-4 shrink-0" />
                    {m.platform_feedback_create_new_title()}
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    className={`${actionButtonClass} ${
                      triageAction === 'support' ? selectedActionClass : ''
                    }`}
                    onClick={() => setTriageAction('support')}
                    disabled={busy}
                  >
                    <LifeBuoy className="h-4 w-4 shrink-0" />
                    {m.platform_feedback_primary_support()}
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    className={`${actionButtonClass} ${
                      triageAction === 'dismiss' ? selectedActionClass : ''
                    } md:col-span-2`}
                    onClick={() => setTriageAction('dismiss')}
                  >
                    <ArchiveX className="h-4 w-4 shrink-0" />
                    {m.platform_feedback_action_dismiss()}
                  </Button>
                </div>
              </section>

              <section className="space-y-4">
                {triageAction === 'recommended' && (
                  <div className="space-y-3">
                    <h3 className="text-sm font-medium text-gray-900">
                      {m.platform_feedback_recommended_action_title()}
                    </h3>
                    <Button
                      type="button"
                      disabled={busy || !canAcceptRecommendedAction || items.isFetching}
                      onClick={acceptRecommendedAction}
                    >
                      {items.isFetching ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <CheckCircle2 className="h-4 w-4" />
                      )}
                      {feedbackSuggestionPrimaryLabel(
                        recommendedAction,
                        recommendedItem?.title,
                        suggestedKind,
                      )}
                    </Button>
                  </div>
                )}

                {triageAction === 'support' && (
                  <div className="space-y-3">
                    <p className="text-sm leading-6 text-gray-600">
                      {m.platform_feedback_support_help()}
                    </p>
                    <Button
                      type="button"
                      disabled={busy}
                      onClick={() => {
                        support.mutate(item.id, { onSuccess: onClose })
                      }}
                    >
                      <LifeBuoy className="h-4 w-4" />
                      {m.platform_feedback_primary_support()}
                    </Button>
                  </div>
                )}

                {triageAction === 'dismiss' && (
                  <div className="space-y-3">
                    <p className="text-sm leading-6 text-gray-600">
                      {m.platform_feedback_dismiss_help()}
                    </p>
                    <Button
                      type="button"
                      variant="secondary"
                      disabled={busy}
                      onClick={() => {
                        dismiss.mutate(item.id, { onSuccess: onClose })
                      }}
                    >
                      <ArchiveX className="h-4 w-4" />
                      {m.platform_feedback_action_dismiss()}
                    </Button>
                  </div>
                )}

                {triageAction === 'link' && (
                  <div className="space-y-4">
                    <div className="flex items-center justify-between gap-3">
                      <h3 className="text-sm font-medium text-gray-900">
                        {m.platform_feedback_link_existing_title()}
                      </h3>
                      {items.isFetching && <Loader2 className="h-4 w-4 animate-spin text-gray-400" />}
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor={`feedback-item-search-${item.id}`}>
                        {m.platform_feedback_smart_search_label()}
                      </Label>
                      <span className="relative block">
                        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
                        <Input
                          id={`feedback-item-search-${item.id}`}
                          value={itemSearch}
                          onChange={(event) => setItemSearch(event.target.value)}
                          placeholder={m.platform_feedback_search_placeholder()}
                          className="pl-9"
                        />
                      </span>
                    </div>
                    <div className="space-y-2">
                      {(items.data ?? []).map((existing) => (
                        <div
                          key={existing.id}
                          className="flex items-start justify-between gap-3 border-t border-gray-200 py-3 first:border-t-0"
                        >
                          <div className="min-w-0">
                            <p className="truncate text-sm font-medium text-gray-900">
                              {existing.title}
                            </p>
                            <p className="mt-1 text-xs text-gray-400">
                              {[existing.kind, existing.status, existing.area]
                                .filter(Boolean)
                                .join(' / ')}
                            </p>
                            <p className="mt-1 text-xs text-gray-400">
                              {m.platform_feedback_reporter_counts({
                                orgs: existing.org_count,
                                users: existing.user_count,
                              })}
                            </p>
                          </div>
                          <Button
                            type="button"
                            size="sm"
                            variant="secondary"
                            disabled={busy}
                            onClick={() => {
                              linkItem.mutate(
                                {
                                  submissionId: item.id,
                                  item_id: existing.id,
                                  link_type: linkType,
                                },
                                { onSuccess: onClose },
                              )
                            }}
                          >
                            <Link2 className="h-4 w-4" />
                            {m.platform_feedback_link()}
                          </Button>
                        </div>
                      ))}
                      {!items.isFetching && (items.data ?? []).length === 0 && (
                        <p className="rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-500">
                          {m.platform_feedback_no_existing_item()}
                        </p>
                      )}
                    </div>
                  </div>
                )}

                {triageAction === 'create' && (
                  <div className="space-y-3">
                    <h3 className="text-sm font-medium text-gray-900">
                      {m.platform_feedback_create_new_title()}
                    </h3>
                    <Select value={kind} onChange={(event) => setKind(event.target.value)}>
                      <option value="feature">{m.platform_feedback_item_kind_feature()}</option>
                      <option value="bug">{m.platform_feedback_item_kind_bug()}</option>
                      <option value="ux_confusion">{m.platform_feedback_item_kind_ux()}</option>
                      <option value="docs">{m.platform_feedback_item_kind_docs()}</option>
                      <option value="support_pattern">{m.platform_feedback_item_kind_support()}</option>
                    </Select>
                    <Input
                      value={title}
                      onChange={(event) => setTitle(event.target.value)}
                      placeholder={m.platform_feedback_title_placeholder()}
                    />
                    <Textarea
                      value={summary}
                      onChange={(event) => setSummary(event.target.value)}
                      rows={4}
                      placeholder={m.platform_feedback_summary_placeholder()}
                    />
                    <Input
                      value={area}
                      onChange={(event) => setArea(event.target.value)}
                      placeholder={m.platform_feedback_area_placeholder()}
                    />
                    <Button
                      type="button"
                      disabled={busy || title.trim().length < 3}
                      onClick={() => {
                        createItem.mutate(
                          {
                            submissionId: item.id,
                            kind,
                            title: title.trim(),
                            summary: summary.trim() || null,
                            area: area.trim() || null,
                            link_type: linkType,
                          },
                          { onSuccess: onClose },
                        )
                      }}
                    >
                      <PlusCircle className="h-4 w-4" />
                      {m.platform_feedback_create_item()}
                    </Button>
                  </div>
                )}
              </section>
              </>
              )}
            </>
          ) : null}

          {(dismiss.isSuccess || support.isSuccess || createItem.isSuccess || linkItem.isSuccess) && (
            <div className="flex items-center gap-2 rounded-lg bg-[var(--color-success-bg)] px-3 py-2 text-sm text-[var(--color-success-text)]">
              <CheckCircle2 className="h-4 w-4" />
              {m.admin_settings_saved()}
            </div>
          )}

          {!canTriage && (
            <section className="space-y-3 border-t border-gray-200 pt-6">
              <div className="space-y-1.5">
                <Label htmlFor={`feedback-submission-status-${item.id}`}>
                  {m.platform_col_status()}
                </Label>
                <Select
                  id={`feedback-submission-status-${item.id}`}
                  value={draftStatus}
                  onChange={(event) => setDraftStatus(event.target.value)}
                >
                  <option value="new">{m.platform_feedback_status_new()}</option>
                  <option value="open">{m.platform_feedback_status_open()}</option>
                  <option value="support">{m.platform_feedback_status_support()}</option>
                  <option value="resolved">{m.platform_feedback_status_resolved()}</option>
                  <option value="dismissed">{m.platform_feedback_status_dismissed()}</option>
                </Select>
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <Button
                  type="button"
                  disabled={busy || draftStatus === item.status}
                  onClick={saveSubmissionStatus}
                >
                  {updateSubmission.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Save className="h-4 w-4" />
                  )}
                  {m.admin_shared_save()}
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={busy}
                  onClick={() => setConfirmDeleteOpen(true)}
                >
                  {deleteSubmission.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Trash2 className="h-4 w-4" />
                  )}
                  {m.platform_delete()}
                </Button>
              </div>
              {updateSubmission.isSuccess && (
                <p className="text-sm text-[var(--color-success)]">
                  {m.platform_feedback_submission_saved()}
                </p>
              )}
            </section>
          )}

          {canTriage && (
            <div className="flex items-center justify-between border-t border-gray-200 pt-4">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="text-[var(--color-destructive)] hover:text-[var(--color-destructive)]"
                disabled={busy}
                onClick={() => setConfirmDeleteOpen(true)}
              >
                {deleteSubmission.isPending ? (
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                ) : (
                  <Trash2 className="h-4 w-4 mr-2" />
                )}
                {m.platform_delete()}
              </Button>
              <div className="flex items-center gap-3">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={submissionStepIndex === 0}
                  onClick={previousSubmissionStep}
                >
                  <ArrowLeft className="h-4 w-4 mr-2" />
                  {m.admin_shared_wizard_previous()}
                </Button>
                {submissionStepIndex < submissionStepOrder.length - 1 && (
                  <Button type="button" onClick={nextSubmissionStep}>
                    {m.admin_shared_wizard_next()}
                    <ArrowRight className="h-4 w-4 ml-2" />
                  </Button>
                )}
              </div>
            </div>
          )}
      </div>
      <AlertDialog open={confirmDeleteOpen} onOpenChange={setConfirmDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {m.platform_feedback_delete_submission_title()}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {m.platform_feedback_delete_submission_description()}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{m.admin_users_cancel()}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-[var(--color-destructive)] text-white hover:bg-[var(--color-destructive)]/90"
              onClick={() => {
                deleteSubmission.mutate(item.id, { onSuccess: onClose })
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
