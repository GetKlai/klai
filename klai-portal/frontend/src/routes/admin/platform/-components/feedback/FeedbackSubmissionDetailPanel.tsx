import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import {
  ArchiveX,
  ArrowLeft,
  ChevronRight,
  Layers,
  LifeBuoy,
  Link2,
  Loader2,
  type LucideIcon,
  PlusCircle,
  RotateCcw,
  Save,
  Search,
  Sparkles,
  Trash2,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import * as m from '@/paraglide/messages'
import {
  usePlatformFeedbackCreateItem,
  usePlatformFeedbackDeleteSubmission,
  usePlatformFeedbackDismiss,
  usePlatformFeedbackItems,
  usePlatformFeedbackLinkItem,
  usePlatformFeedbackUpdateSubmission,
} from '../../-hooks'
import type { PlatformFeedbackSubmission } from '../../-types'
import {
  feedbackFallbackSummary,
  feedbackStatusLabel,
  feedbackSubmissionReporterLabel,
  normalizedFeedbackKind,
} from './-feedback-helpers'

type TriageMode = 'menu' | 'product' | 'status'

/** A calm, full-width disposition choice on the triage menu. */
function TriageChoice({
  icon: Icon,
  label,
  hint,
  onClick,
  disabled,
}: {
  icon: LucideIcon
  label: string
  hint: string
  onClick: () => void
  disabled?: boolean
}) {
  return (
    <Button
      type="button"
      variant="secondary"
      disabled={disabled}
      onClick={onClick}
      className="h-auto w-full justify-start gap-3 rounded-xl px-4 py-3 text-left"
    >
      <Icon className="h-4 w-4 shrink-0" />
      <span className="flex min-w-0 flex-col">
        <span className="text-sm font-medium">{label}</span>
        <span className="text-xs font-normal text-gray-400">{hint}</span>
      </span>
    </Button>
  )
}

export function FeedbackSubmissionDetailPanel({
  item,
  fmtDate,
  onClose,
}: {
  item: PlatformFeedbackSubmission
  fmtDate: (s: string | null) => string
  onClose: () => void
}) {
  const navigate = useNavigate()
  const canTriage = item.status === 'new'
  const suggestion = item.triage_suggestion
  const candidate = suggestion?.duplicate_candidates[0] ?? null
  const defaultKind =
    item.event_type === 'klai_assistant.problem_report' ? 'bug' : 'feature'
  const productKind = normalizedFeedbackKind(suggestion?.classification, defaultKind)
  const reportText = item.raw_text || feedbackFallbackSummary(item)
  const defaultTitle = (suggestion?.summary || item.raw_text || '').slice(0, 90).trim()
  const linkType =
    item.event_type === 'klai_assistant.problem_report' ? 'bug_repro' : 'evidence'
  const reporter = feedbackSubmissionReporterLabel(item)

  const [mode, setMode] = useState<TriageMode>('menu')
  const [search, setSearch] = useState('')
  const [draftStatus, setDraftStatus] = useState(item.status)
  const [confirmDismissOpen, setConfirmDismissOpen] = useState(false)
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false)

  const matches = usePlatformFeedbackItems(search, 'triage')
  const updateSubmission = usePlatformFeedbackUpdateSubmission()
  const deleteSubmission = usePlatformFeedbackDeleteSubmission()
  const dismiss = usePlatformFeedbackDismiss()
  const createItem = usePlatformFeedbackCreateItem()
  const linkItem = usePlatformFeedbackLinkItem()
  const busy =
    updateSubmission.isPending ||
    deleteSubmission.isPending ||
    dismiss.isPending ||
    createItem.isPending ||
    linkItem.isPending

  const openItem = (itemId: number | null | undefined) => {
    if (itemId) {
      void navigate({
        to: '/admin/platform/feedback/items/$itemId',
        params: { itemId: String(itemId) },
      })
    } else {
      onClose()
    }
  }
  const linkToItem = (itemId: number) =>
    linkItem.mutate(
      { submissionId: item.id, item_id: itemId, link_type: linkType },
      { onSuccess: (res) => openItem(res.item_id) },
    )
  const createProductItem = () =>
    createItem.mutate(
      {
        submissionId: item.id,
        kind: productKind,
        title: defaultTitle || reportText.slice(0, 90),
        summary: item.raw_text ?? null,
        area: suggestion?.suggested_area ?? null,
        link_type: linkType,
      },
      { onSuccess: (res) => openItem(res.item_id) },
    )
  const createSupportItem = () =>
    createItem.mutate(
      {
        submissionId: item.id,
        kind: 'support_pattern',
        title: defaultTitle || reportText.slice(0, 90),
        summary: item.raw_text ?? null,
        area: suggestion?.suggested_area ?? null,
        link_type: 'support_signal',
      },
      { onSuccess: (res) => openItem(res.item_id) },
    )
  const saveStatus = () =>
    updateSubmission.mutate(
      { submissionId: item.id, status: draftStatus },
      { onSuccess: onClose },
    )

  const header = (
    <div className="flex items-start gap-3">
      <div className="flex-1">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {canTriage
            ? m.platform_feedback_triage_title()
            : m.platform_feedback_submission_detail_title()}
        </h1>
        <p className="mt-1 text-sm text-gray-400">
          {item.org_name ?? item.org_slug ?? m.platform_feedback_unknown_organization()}
          {reporter ? ` · ${reporter}` : ''} · {fmtDate(item.created_at)}
        </p>
      </div>
      <Button type="button" variant="ghost" size="sm" onClick={onClose}>
        <ArrowLeft className="h-4 w-4 mr-2" />
        {m.platform_back_to_feedback()}
      </Button>
    </div>
  )

  const reportBlock = (
    <section className="space-y-2">
      <p className="whitespace-pre-wrap text-[15px] leading-7 text-gray-900">{reportText}</p>
      {item.page_url && (
        <p className="truncate font-mono text-xs text-gray-400">{item.page_url}</p>
      )}
    </section>
  )

  const deleteDialog = (
    <AlertDialog open={confirmDeleteOpen} onOpenChange={setConfirmDeleteOpen}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{m.platform_feedback_delete_submission_title()}</AlertDialogTitle>
          <AlertDialogDescription>
            {m.platform_feedback_delete_submission_description()}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{m.admin_users_cancel()}</AlertDialogCancel>
          <AlertDialogAction
            className="bg-[var(--color-destructive)] text-white hover:bg-[var(--color-destructive)]/90"
            onClick={() => deleteSubmission.mutate(item.id, { onSuccess: onClose })}
          >
            {m.platform_delete()}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )

  const dismissDialog = (
    <AlertDialog open={confirmDismissOpen} onOpenChange={setConfirmDismissOpen}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{m.platform_feedback_action_dismiss()}</AlertDialogTitle>
          <AlertDialogDescription>{m.platform_feedback_dismiss_help()}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{m.admin_users_cancel()}</AlertDialogCancel>
          <AlertDialogAction
            onClick={() => dismiss.mutate(item.id, { onSuccess: onClose })}
          >
            {m.platform_feedback_action_dismiss()}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )

  // --- processed submission: read-only receipt, no triage controls ----
  if (!canTriage) {
    return (
      <div className="space-y-6">
        {header}
        {reportBlock}
        {item.linked_item_id ? (
          <button
            type="button"
            onClick={() => openItem(item.linked_item_id)}
            className="flex w-full items-center justify-between gap-3 rounded-xl border border-gray-200 bg-white px-5 py-4 text-left klai-hover"
          >
            <span className="min-w-0">
              <span className="block text-xs font-medium text-gray-400">
                {m.platform_feedback_belongs_to_item()}
              </span>
              <span className="mt-0.5 block truncate text-sm font-medium text-gray-900">
                #{item.linked_item_id}
                {item.linked_item_title ? ` · ${item.linked_item_title}` : ''}
              </span>
              <span className="mt-0.5 block text-xs text-gray-400">
                {feedbackStatusLabel(item.status)}
              </span>
            </span>
            <ChevronRight className="h-4 w-4 shrink-0 text-gray-300" />
          </button>
        ) : (
          <section className="rounded-xl border border-gray-200 bg-white px-5 py-4">
            <p className="text-xs font-medium text-gray-400">{m.platform_col_status()}</p>
            <p className="mt-1 text-sm font-medium text-gray-900">
              {feedbackStatusLabel(item.status)}
            </p>
          </section>
        )}
        <div className="flex items-center justify-between border-t border-gray-200 pt-4">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="text-[var(--color-destructive)] hover:text-[var(--color-destructive)]"
            disabled={busy}
            onClick={() => setConfirmDeleteOpen(true)}
          >
            <Trash2 className="h-4 w-4 mr-2" />
            {m.platform_delete()}
          </Button>
          <Button
            type="button"
            variant="secondary"
            disabled={busy}
            onClick={() =>
              updateSubmission.mutate(
                { submissionId: item.id, status: 'new' },
                { onSuccess: onClose },
              )
            }
          >
            <RotateCcw className="h-4 w-4" />
            {m.platform_feedback_reopen()}
          </Button>
        </div>
        {deleteDialog}
      </div>
    )
  }

  // --- new submission: triage router ----------------------------------
  if (mode === 'product') {
    const showMatches = search.trim().length >= 2
    const rows = showMatches
      ? (matches.data ?? []).map((existing) => ({
          id: existing.id,
          title: existing.title,
          meta: [existing.kind, existing.status, existing.area].filter(Boolean).join(' · '),
          suggested: false,
        }))
      : candidate
        ? [
            {
              id: candidate.item_id,
              title: candidate.title ?? `#${candidate.item_id}`,
              meta: [candidate.kind, candidate.status, candidate.area]
                .filter(Boolean)
                .join(' · '),
              suggested: true,
            },
          ]
        : []

    return (
      <div className="space-y-6">
        {header}
        {reportBlock}
        <section className="space-y-4">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-sm font-medium text-gray-900">
              {m.platform_feedback_product_step_title()}
            </h2>
            <Button type="button" variant="ghost" size="sm" onClick={() => setMode('menu')}>
              <ArrowLeft className="h-4 w-4 mr-2" />
              {m.platform_feedback_back()}
            </Button>
          </div>

          <span className="relative block">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={m.platform_feedback_search_placeholder()}
              className="pl-9"
            />
          </span>

          {rows.length > 0 && (
            <div className="divide-y divide-gray-200 border-y border-gray-200">
              {rows.map((row) => (
                <div key={row.id} className="flex items-center justify-between gap-3 py-3">
                  <div className="min-w-0">
                    {row.suggested && (
                      <span className="mb-1 inline-flex items-center gap-1 text-xs text-[var(--color-rl-accent-dark)]">
                        <Sparkles className="h-3 w-3" />
                        {m.platform_feedback_klai_suggestion()}
                      </span>
                    )}
                    <p className="truncate text-sm font-medium text-gray-900">{row.title}</p>
                    {row.meta && <p className="truncate text-xs text-gray-400">{row.meta}</p>}
                  </div>
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    disabled={busy}
                    onClick={() => linkToItem(row.id)}
                  >
                    <Link2 className="h-4 w-4" />
                    {m.platform_feedback_link()}
                  </Button>
                </div>
              ))}
            </div>
          )}

          {showMatches && !matches.isFetching && rows.length === 0 && (
            <p className="rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-500">
              {m.platform_feedback_no_existing_item()}
            </p>
          )}

          <div className="flex items-center justify-between gap-3 rounded-xl border border-dashed border-gray-300 px-4 py-3">
            <span className="text-sm text-gray-600">{m.platform_feedback_create_new_fallback()}</span>
            <Button type="button" disabled={busy} onClick={createProductItem}>
              {createItem.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <PlusCircle className="h-4 w-4" />
              )}
              {m.platform_feedback_create_new_title()}
            </Button>
          </div>
        </section>
        {deleteDialog}
        {dismissDialog}
      </div>
    )
  }

  if (mode === 'status') {
    return (
      <div className="space-y-6">
        {header}
        {reportBlock}
        <section className="space-y-4">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-sm font-medium text-gray-900">
              {m.platform_feedback_choice_status()}
            </h2>
            <Button type="button" variant="ghost" size="sm" onClick={() => setMode('menu')}>
              <ArrowLeft className="h-4 w-4 mr-2" />
              {m.platform_feedback_back()}
            </Button>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`feedback-submission-status-${item.id}`}>{m.platform_col_status()}</Label>
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
          <Button type="button" disabled={busy || draftStatus === item.status} onClick={saveStatus}>
            {updateSubmission.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Save className="h-4 w-4" />
            )}
            {m.admin_shared_save()}
          </Button>
        </section>
        {deleteDialog}
        {dismissDialog}
      </div>
    )
  }

  // mode === 'menu'
  return (
    <div className="space-y-6">
      {header}
      {reportBlock}

      {suggestion && (
        <p className="flex items-start gap-2 text-sm text-[var(--color-rl-accent-dark)]">
          <Sparkles className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            {candidate
              ? m.platform_feedback_advice_match({
                  title: candidate.title ?? `#${candidate.item_id}`,
                })
              : m.platform_feedback_advice_new()}
          </span>
        </p>
      )}

      <section className="space-y-3">
        <h2 className="text-sm font-medium text-gray-900">
          {m.platform_feedback_triage_question()}
        </h2>
        <div className="space-y-2">
          <TriageChoice
            icon={Layers}
            label={m.platform_feedback_choice_product()}
            hint={m.platform_feedback_choice_product_hint()}
            onClick={() => setMode('product')}
          />
          <TriageChoice
            icon={LifeBuoy}
            label={m.platform_feedback_choice_support()}
            hint={m.platform_feedback_choice_support_hint()}
            disabled={busy}
            onClick={createSupportItem}
          />
          <TriageChoice
            icon={ArchiveX}
            label={m.platform_feedback_action_dismiss()}
            hint={m.platform_feedback_choice_dismiss_hint()}
            disabled={busy}
            onClick={() => setConfirmDismissOpen(true)}
          />
        </div>
        <button
          type="button"
          className="text-xs text-gray-400 underline-offset-2 hover:text-gray-600 hover:underline"
          onClick={() => setMode('status')}
        >
          {m.platform_feedback_choice_status()}
        </button>
      </section>

      <div className="flex border-t border-gray-200 pt-4">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="text-[var(--color-destructive)] hover:text-[var(--color-destructive)]"
          disabled={busy}
          onClick={() => setConfirmDeleteOpen(true)}
        >
          <Trash2 className="h-4 w-4 mr-2" />
          {m.platform_delete()}
        </Button>
      </div>
      {deleteDialog}
      {dismissDialog}
    </div>
  )
}
