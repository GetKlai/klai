// SPEC-KNOWLEDGE-ACTIVITY-001 §4.3 + Appendix A: the per-answer review form on
// the conversation detail. A knowledge admin records whether an assistant
// answer was right and, when it was not, why — pre-filled from the nightly
// judge so the common case is a single click on save.
import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { Link } from '@tanstack/react-router'
import { Loader2, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Field } from '@/components/ui/field'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import { Select } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import {
  useActivityKnowledgeBases,
  useDeleteReview,
  useUpsertReview,
  type ConversationReview,
  type ConversationReviewCause,
  type ConversationReviewVerdict,
} from './api'
import type { ConversationQuality } from './types'
import * as m from '@/paraglide/messages'

const VERDICTS: { value: ConversationReviewVerdict; label: () => string }[] = [
  { value: 'correct', label: m.activity_verdict_correct },
  { value: 'incomplete', label: m.activity_verdict_incomplete },
  { value: 'wrong', label: m.activity_verdict_wrong },
  { value: 'not_a_fault', label: m.activity_verdict_not_a_fault },
]

const CAUSES: { value: ConversationReviewCause; label: () => string }[] = [
  { value: 'knowledge_missing', label: m.activity_cause_knowledge_missing },
  { value: 'knowledge_wrong', label: m.activity_cause_knowledge_wrong },
  { value: 'behaviour', label: m.activity_cause_behaviour },
]

/** A knowledge cause points at a knowledge base the admin can open directly. */
const KNOWLEDGE_CAUSES: ConversationReviewCause[] = ['knowledge_missing', 'knowledge_wrong']

/** The two failure verdicts are only valid together with a real cause. */
const needsCause = (verdict: ConversationReviewVerdict | null) =>
  verdict === 'incomplete' || verdict === 'wrong'

/**
 * Judge suggestion -> the pair it pre-selects (SPEC Appendix A). The failure
 * categories carry the cause; `resolved` is an outcome, so it is matched last.
 */
const SUGGESTED_BY_CATEGORY: Record<
  string,
  { verdict: ConversationReviewVerdict; cause: ConversationReviewCause }
> = {
  retrieval_miss: { verdict: 'wrong', cause: 'knowledge_missing' },
  retrieval_wrong: { verdict: 'wrong', cause: 'knowledge_wrong' },
  generation_error: { verdict: 'wrong', cause: 'behaviour' },
  policy_refusal: { verdict: 'not_a_fault', cause: 'none' },
  scope_mismatch: { verdict: 'not_a_fault', cause: 'none' },
  user_confusion: { verdict: 'not_a_fault', cause: 'none' },
}

function suggestedReview(quality?: ConversationQuality | null) {
  if (!quality) return null
  if (quality.failure_category) return SUGGESTED_BY_CATEGORY[quality.failure_category] ?? null
  return quality.outcome === 'resolved'
    ? { verdict: 'correct' as const, cause: 'none' as const }
    : null
}

function Toggle({
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

/**
 * Compact per-answer review. Renders under each assistant turn; the current
 * review (when one exists) is the starting point, otherwise the judge's
 * suggestion is.
 */
export function ReviewForm({
  messageId,
  review,
  quality,
}: {
  messageId: number
  review: ConversationReview | null
  quality?: ConversationQuality | null
}) {
  const suggestion = suggestedReview(review ? null : quality)
  // Several review forms render on one page, so their group labels need unique ids.
  const fieldId = useId()
  const upsert = useUpsertReview(messageId)
  const remove = useDeleteReview(messageId)
  const knowledgeBases = useActivityKnowledgeBases()

  const seed = () => ({
    verdict: review?.verdict ?? suggestion?.verdict ?? null,
    cause: review?.cause ?? suggestion?.cause ?? null,
    note: review?.note ?? '',
    kbSlug: review?.kb_slug ?? '',
  })

  const [form, setForm] = useState(seed)
  const [confirmingDelete, setConfirmingDelete] = useState(false)

  // Re-seed when the stored review changes underneath the form — after a
  // delete it must fall back to the judge suggestion, not keep the stale draft.
  const seededFrom = useRef(review)
  useEffect(() => {
    if (seededFrom.current !== review) {
      seededFrom.current = review
      setForm(seed())
      setConfirmingDelete(false)
    }
    // Only the identity of the stored review matters; the rest is derived from it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [review])

  const patch = (next: Partial<ReturnType<typeof seed>>) =>
    setForm((prev) => ({ ...prev, ...next }))

  const valid =
    form.verdict !== null && (!needsCause(form.verdict) || (form.cause !== null && form.cause !== 'none'))

  const onKeyDown = (event: KeyboardEvent<HTMLFormElement>) => {
    // Never hijack typing in a field or a native select's own key handling.
    if (/^(INPUT|TEXTAREA|SELECT)$/.test((event.target as HTMLElement).tagName)) return
    if (event.metaKey || event.ctrlKey || event.altKey) return
    const index = ['1', '2', '3', '4'].indexOf(event.key)
    if (index === -1) return
    event.preventDefault()
    patch({ verdict: VERDICTS[index].value })
  }

  const save = () => {
    if (form.verdict === null) return
    const cause = needsCause(form.verdict) ? form.cause : 'none'
    if (cause === null) return
    upsert.mutate(
      {
        verdict: form.verdict,
        cause,
        note: form.note.trim() || null,
        kb_slug: form.kbSlug || null,
      },
      {
        onSuccess: () => toast.success(m.activity_review_saved()),
        onError: () => toast.error(m.activity_review_failed()),
      },
    )
  }

  const orgKbs = (knowledgeBases.data?.knowledge_bases ?? []).filter(
    (kb) => kb.owner_type === 'org',
  )
  const openInKb =
    form.kbSlug !== '' && form.cause !== null && KNOWLEDGE_CAUSES.includes(form.cause)

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault()
        if (valid) save()
      }}
      onKeyDown={onKeyDown}
      className="mt-2 space-y-2"
    >
      <div className="space-y-1.5" role="group" aria-labelledby={`${fieldId}-verdict`}>
        <p id={`${fieldId}-verdict`} className="text-xs font-medium text-gray-900">
          {m.activity_review_verdict_label()}
        </p>
        <div className="flex flex-wrap gap-1.5">
          {VERDICTS.map((option) => (
            <Toggle
              key={option.value}
              pressed={form.verdict === option.value}
              onClick={() => patch({ verdict: option.value })}
            >
              {option.label()}
            </Toggle>
          ))}
        </div>
      </div>

      {needsCause(form.verdict) && (
        <div className="space-y-1.5" role="group" aria-labelledby={`${fieldId}-cause`}>
          <p id={`${fieldId}-cause`} className="text-xs font-medium text-gray-900">
            {m.activity_review_cause_label()}
          </p>
          <div className="flex flex-wrap gap-1.5">
            {CAUSES.map((option) => (
              <Toggle
                key={option.value}
                pressed={form.cause === option.value}
                onClick={() => patch({ cause: option.value })}
              >
                {option.label()}
              </Toggle>
            ))}
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-end gap-4">
        <Field label={m.activity_review_note_label()} className="min-w-56 flex-1">
          <Textarea
            rows={2}
            value={form.note}
            placeholder={m.activity_review_note_placeholder()}
            onChange={(event) => patch({ note: event.target.value })}
          />
        </Field>
        <Field label={m.activity_review_kb_label()}>
          <Select
            containerClassName="w-56"
            value={form.kbSlug}
            onChange={(event) => patch({ kbSlug: event.target.value })}
          >
            <option value="">{m.activity_review_kb_none()}</option>
            {orgKbs.map((kb) => (
              <option key={kb.slug} value={kb.slug}>
                {kb.name}
              </option>
            ))}
          </Select>
        </Field>
      </div>

      {openInKb && (
        <p>
          <Link
            to="/app/docs/$kbSlug"
            params={{ kbSlug: form.kbSlug }}
            className="text-xs text-gray-900 underline underline-offset-2"
          >
            {m.activity_review_open_in_kb()}
          </Link>
        </p>
      )}

      <div
        className={`flex flex-wrap items-center gap-3 ${
          confirmingDelete ? 'bg-[var(--color-hover)]' : ''
        }`}
      >
        <Button type="submit" size="sm" disabled={!valid || upsert.isPending}>
          {upsert.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {m.activity_review_save()}
        </Button>
        {review && (
          <InlineDeleteConfirm
            isConfirming={confirmingDelete}
            isPending={remove.isPending}
            label={m.activity_review_delete()}
            cancelLabel={m.activity_review_delete_cancel()}
            onConfirm={() =>
              remove.mutate(undefined, {
                onSuccess: () => toast.success(m.activity_review_deleted()),
                onError: () => toast.error(m.activity_review_failed()),
              })
            }
            onCancel={() => setConfirmingDelete(false)}
          >
            <Button type="button" variant="outline" size="sm" onClick={() => setConfirmingDelete(true)}>
              <Trash2 className="mr-2 h-4 w-4" />
              {m.activity_review_delete()}
            </Button>
          </InlineDeleteConfirm>
        )}
        {review?.reviewer_name && (
          <span className="text-xs text-gray-600">
            {m.activity_review_by({ name: review.reviewer_name })}
            {review.reviewed_at ? ` · ${new Date(review.reviewed_at).toLocaleDateString()}` : ''}
          </span>
        )}
      </div>
    </form>
  )
}
