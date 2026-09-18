// Whole-case reference answer key (support-gap-detection.md, gap quality
// review). This is the reviewer's own record of every reusable question the
// case should teach — including ones the analyser missed — kept deliberately
// separate from the machine findings and never pre-filled from them, so an
// unreviewed machine label is never mistaken for a confirmed one. Each question
// carries a diagnosis and the conversation segments it cites, chosen through
// the UI rather than typed as JSON. Marking the review complete with no
// questions is a real answer: "I read the whole case, nothing here is
// reusable." The save carries the case content hash so an edit against a case
// that changed underneath comes back as a refresh prompt (409), not a
// silent overwrite; on success the detail is refetched to show the stored form.
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2, Plus, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { ApiError, apiFetch } from '@/lib/apiFetch'
import { queryLogger } from '@/lib/logger'
import * as m from '@/paraglide/messages'
import { getLocale } from '@/paraglide/runtime'
import {
  diagnosisOptions,
  invalidateCaseCaches,
  type CaseMessage,
  type CaseReference,
  type CaseReferenceQuestion,
} from '../-support-helpers'
import { CaseStalePrompt } from './CaseStalePrompt'

interface CaseReferenceFormProps {
  caseId: string
  kbSlug: string
  contentHash: string
  messages: CaseMessage[]
  reference: CaseReference | null
}

const QUESTION_MAX = 2000

function evidenceLabel(message: CaseMessage): string {
  const preview = message.text.length > 120 ? `${message.text.slice(0, 120)}…` : message.text
  if (message.start_seconds != null && message.end_seconds != null) {
    const time = m.support_case_segment_time({
      start: String(Math.round(message.start_seconds)),
      end: String(Math.round(message.end_seconds)),
    })
    return `${time} · ${preview}`
  }
  return preview
}

export function CaseReferenceForm({ caseId, kbSlug, contentHash, messages, reference }: CaseReferenceFormProps) {
  const queryClient = useQueryClient()
  const diagnoses = diagnosisOptions()
  // Seed only from the reviewer's saved reference, never from the analyser.
  const [questions, setQuestions] = useState<CaseReferenceQuestion[]>(
    () => (reference?.questions ?? []).map((q) => ({ ...q, message_ids: [...q.message_ids] })),
  )
  const [complete, setComplete] = useState(reference?.complete ?? false)
  const [blankError, setBlankError] = useState(false)
  const [stale, setStale] = useState(false)

  const updateQuestion = (index: number, patch: Partial<CaseReferenceQuestion>) =>
    setQuestions((prev) => prev.map((q, i) => (i === index ? { ...q, ...patch } : q)))

  const addQuestion = () =>
    setQuestions((prev) => [...prev, { question: '', diagnosis: diagnoses[0].value, message_ids: [] }])

  const removeQuestion = (index: number) =>
    setQuestions((prev) => prev.filter((_, i) => i !== index))

  const toggleEvidence = (index: number, messageId: string) =>
    setQuestions((prev) =>
      prev.map((q, i) => {
        if (i !== index) return q
        const has = q.message_ids.includes(messageId)
        return {
          ...q,
          message_ids: has ? q.message_ids.filter((id) => id !== messageId) : [...q.message_ids, messageId],
        }
      }),
    )

  const saveMutation = useMutation({
    mutationFn: () =>
      apiFetch(
        `/api/app/knowledge-bases/${kbSlug}/support-cases/${caseId}/reference`,
        {
          method: 'PUT',
          body: JSON.stringify({
            content_hash: contentHash,
            questions: questions.map((q) => ({
              question: q.question.trim(),
              diagnosis: q.diagnosis,
              message_ids: q.message_ids,
            })),
            complete,
          }),
        },
      ),
    onSuccess: () => {
      setStale(false)
      toast.success(m.support_case_reference_saved())
      invalidateCaseCaches(queryClient, caseId, kbSlug)
    },
    onError: (err: unknown) => {
      queryLogger.warn('Support case reference save failed', { error: err })
      if (err instanceof ApiError && err.status === 409) {
        setStale(true)
        return
      }
      toast.error(m.support_case_reference_save_error())
    },
  })

  const submit = () => {
    if (questions.some((q) => !q.question.trim())) {
      setBlankError(true)
      return
    }
    setBlankError(false)
    saveMutation.mutate()
  }

  return (
    <form
      className="space-y-3 rounded-xl border border-gray-200 p-4"
      onSubmit={(e) => {
        e.preventDefault()
        submit()
      }}
    >
      <div>
        <h2 className="text-sm font-semibold text-gray-900">{m.support_case_reference_heading()}</h2>
        <p className="text-xs text-gray-600">{m.support_case_reference_intro()}</p>
      </div>

      {questions.length === 0 ? (
        <p className="text-sm text-gray-500">{m.support_case_reference_empty()}</p>
      ) : (
        <div className="space-y-4">
          {questions.map((q, index) => (
            <div key={index} className="space-y-2 rounded-lg border border-gray-200 p-3">
              <div className="space-y-1">
                <Label htmlFor={`ref-q-${index}`}>{m.support_case_reference_question_label()}</Label>
                <Textarea
                  id={`ref-q-${index}`}
                  value={q.question}
                  maxLength={QUESTION_MAX}
                  rows={2}
                  placeholder={m.support_case_reference_question_placeholder()}
                  onChange={(e) => updateQuestion(index, { question: e.target.value })}
                />
              </div>

              <div className="space-y-1">
                <Label htmlFor={`ref-d-${index}`}>{m.support_case_reference_diagnosis_label()}</Label>
                <Select
                  id={`ref-d-${index}`}
                  value={q.diagnosis}
                  onChange={(e) => updateQuestion(index, { diagnosis: e.target.value })}
                  className="w-auto"
                >
                  {diagnoses.map((d) => (
                    <option key={d.value} value={d.value}>
                      {d.label}
                    </option>
                  ))}
                </Select>
              </div>

              {messages.length > 0 && (
                <fieldset className="space-y-1">
                  <legend className="text-sm font-medium text-gray-900">
                    {m.support_case_reference_evidence_label()}
                  </legend>
                  <div className="max-h-40 space-y-1 overflow-y-auto">
                    {messages.map((message) => (
                      <Checkbox
                        key={message.id}
                        label={evidenceLabel(message)}
                        checked={q.message_ids.includes(message.id)}
                        onChange={() => toggleEvidence(index, message.id)}
                      />
                    ))}
                  </div>
                </fieldset>
              )}

              <Button type="button" variant="outline" size="sm" onClick={() => removeQuestion(index)}>
                <Trash2 className="h-4 w-4 mr-2" />
                {m.support_case_reference_remove()}
              </Button>
            </div>
          ))}
        </div>
      )}

      <Button type="button" variant="outline" size="sm" onClick={addQuestion}>
        <Plus className="h-4 w-4 mr-2" />
        {m.support_case_reference_add_question()}
      </Button>

      <div className="space-y-1 border-t border-gray-200 pt-3">
        <Checkbox
          label={m.support_case_reference_complete_label()}
          checked={complete}
          onChange={(e) => setComplete(e.target.checked)}
        />
        <p className="pl-7 text-xs text-gray-600">{m.support_case_reference_complete_hint()}</p>
      </div>

      {blankError && (
        <p className="text-xs text-[var(--color-destructive-text)]">{m.support_case_reference_blank_error()}</p>
      )}

      {stale && <CaseStalePrompt caseId={caseId} message={m.support_case_reference_stale()} />}

      <div className="flex items-center gap-3">
        <Button type="submit" size="sm" disabled={saveMutation.isPending}>
          {saveMutation.isPending ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin mr-2" />
              {m.support_case_review_saving()}
            </>
          ) : (
            m.support_case_reference_save()
          )}
        </Button>
        {reference?.reviewed_by && (
          <span className="text-xs text-gray-500">
            {m.support_case_reference_reviewed({ date: new Date(reference.reviewed_at).toLocaleString(getLocale()) })}
          </span>
        )}
      </div>
    </form>
  )
}
