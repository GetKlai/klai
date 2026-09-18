// Human review of one analyser finding (support-gap-detection.md, shared UI
// contract). The reviewer judges whether the analysis is right; the choice and
// an optional note are stored separately from the machine analysis and never
// change it. The save carries the exact analysis_revision the reviewer saw, so
// a stale review (the analysis changed underneath) comes back as 409 and is
// shown as a refresh prompt rather than a silent overwrite.
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { RadioCardGroup } from '@/components/ui/radio-card-group'
import { ApiError, apiFetch } from '@/lib/apiFetch'
import { queryLogger } from '@/lib/logger'
import * as m from '@/paraglide/messages'
import { getLocale } from '@/paraglide/runtime'

export interface FindingReviewValue {
  decision: 'correct' | 'incorrect' | 'uncertain'
  note: string
  reviewed_by: string
  reviewed_at: string
}

interface CaseFindingReviewProps {
  caseId: string
  kbSlug: string
  analysisRevision: string
  findingIndex: number
  review: FindingReviewValue | null
}

const NOTE_MAX = 2000

export function CaseFindingReview({
  caseId,
  kbSlug,
  analysisRevision,
  findingIndex,
  review,
}: CaseFindingReviewProps) {
  const queryClient = useQueryClient()
  const [decision, setDecision] = useState(review?.decision ?? '')
  const [note, setNote] = useState(review?.note ?? '')
  const [stale, setStale] = useState(false)

  const decisionOptions = [
    { value: 'correct', label: m.support_case_review_decision_correct() },
    { value: 'incorrect', label: m.support_case_review_decision_incorrect() },
    { value: 'uncertain', label: m.support_case_review_decision_uncertain() },
  ]

  const saveMutation = useMutation({
    mutationFn: () =>
      apiFetch<{ analysis_revision: string; review: FindingReviewValue }>(
        `/api/app/knowledge-bases/${kbSlug}/support-cases/${caseId}/findings/${findingIndex}/review`,
        {
          method: 'PATCH',
          body: JSON.stringify({ analysis_revision: analysisRevision, decision, note }),
        },
      ),
    onSuccess: () => {
      setStale(false)
      toast.success(m.support_case_review_saved())
      void queryClient.invalidateQueries({ queryKey: ['support-case', caseId] })
      void queryClient.invalidateQueries({ queryKey: ['support-cases', kbSlug] })
    },
    onError: (err: unknown) => {
      queryLogger.warn('Support case review save failed', { error: err })
      // 409 means the analysis changed since the case was opened; do not claim
      // the review was saved, prompt a refresh instead.
      if (err instanceof ApiError && err.status === 409) {
        setStale(true)
        return
      }
      toast.error(m.support_case_review_save_error())
    },
  })

  return (
    <form
      className="space-y-3 rounded-lg border border-gray-200 bg-gray-50 p-3"
      onSubmit={(e) => {
        e.preventDefault()
        if (decision) saveMutation.mutate()
      }}
    >
      <div className="space-y-0.5">
        <p className="text-xs font-medium text-gray-900">{m.support_case_review_heading()}</p>
        <p className="text-xs text-gray-600">{m.support_case_review_intro()}</p>
      </div>

      <RadioCardGroup
        compact
        aria-label={m.support_case_review_heading()}
        options={decisionOptions}
        value={decision}
        onChange={setDecision}
      />

      <div className="space-y-1.5">
        <Label htmlFor={`review-note-${findingIndex}`}>{m.support_case_review_note_label()}</Label>
        <Textarea
          id={`review-note-${findingIndex}`}
          value={note}
          maxLength={NOTE_MAX}
          rows={2}
          placeholder={m.support_case_review_note_placeholder()}
          onChange={(e) => setNote(e.target.value)}
        />
      </div>

      {stale && (
        <div className="rounded-lg border border-[var(--color-warning)]/30 bg-[var(--color-warning)]/5 p-2">
          <p className="text-xs text-[var(--color-warning-text)]">{m.support_case_review_stale()}</p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="mt-2"
            onClick={() => void queryClient.invalidateQueries({ queryKey: ['support-case', caseId] })}
          >
            {m.support_case_review_refresh()}
          </Button>
        </div>
      )}

      <div className="flex items-center gap-3">
        <Button type="submit" size="sm" disabled={!decision || saveMutation.isPending}>
          {saveMutation.isPending ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin mr-2" />
              {m.support_case_review_saving()}
            </>
          ) : (
            m.support_case_review_save()
          )}
        </Button>
        {review?.reviewed_by && (
          <span className="text-xs text-gray-500">
            {m.support_case_review_existing({ date: new Date(review.reviewed_at).toLocaleString(getLocale()) })}
          </span>
        )}
      </div>
    </form>
  )
}
