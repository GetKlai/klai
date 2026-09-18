// Speaker-role correction for call evidence (support-gap-detection.md, gap
// quality review). Imported calls arrive with every segment role "unknown"
// because the recorder cannot tell customer from agent; until a human confirms
// who spoke, a "content gap" drawn from a call is only provisional. This editor
// lets the reviewer set roles per segment, or in bulk for a whole speaker when
// the source carried speaker ids — it never guesses that all unlabelled
// segments are the same person. The timestamp and source text are read-only:
// only the role changes. The save carries ONLY the segments the reviewer
// actually changed, against the analysis revision they saw, so a stale case
// (409) comes back as a refresh prompt rather than a silent overwrite.
import { useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { ApiError, apiFetch } from '@/lib/apiFetch'
import { queryLogger } from '@/lib/logger'
import * as m from '@/paraglide/messages'
import {
  invalidateCaseCaches,
  type CaseMessage,
  type CaseMessageRole,
} from '../-support-helpers'
import { CaseStalePrompt } from './CaseStalePrompt'

interface CaseRoleReviewProps {
  caseId: string
  kbSlug: string
  analysisRevision: string
  messages: CaseMessage[]
}

const ROLES: CaseMessageRole[] = ['customer', 'agent', 'unknown']

function roleLabel(role: CaseMessageRole): string {
  if (role === 'customer') return m.support_case_role_customer()
  if (role === 'agent') return m.support_case_role_agent()
  return m.support_case_role_unknown()
}

function RoleOptions() {
  return (
    <>
      {ROLES.map((role) => (
        <option key={role} value={role}>
          {roleLabel(role)}
        </option>
      ))}
    </>
  )
}

export function CaseRoleReview({ caseId, kbSlug, analysisRevision, messages }: CaseRoleReviewProps) {
  const queryClient = useQueryClient()
  const [edits, setEdits] = useState<Record<string, CaseMessageRole>>({})
  const [bulkChoice, setBulkChoice] = useState<Record<string, CaseMessageRole>>({})
  const [stale, setStale] = useState(false)

  const roleOf = (message: CaseMessage): CaseMessageRole => edits[message.id] ?? message.role
  const needsReview = messages.some((message) => roleOf(message) === 'unknown')

  // Bulk correction is offered only when the source distinguished speakers.
  // Without speaker ids every segment is edited on its own — collapsing them
  // into one role would be exactly the guess this review exists to avoid.
  const speakers = useMemo(() => {
    const ids: string[] = []
    for (const message of messages) {
      if (message.speaker_id && !ids.includes(message.speaker_id)) ids.push(message.speaker_id)
    }
    return ids
  }, [messages])

  const applyBulk = (speakerId: string) => {
    const role = bulkChoice[speakerId]
    if (!role) return
    setEdits((prev) => {
      const next = { ...prev }
      for (const message of messages) {
        if (message.speaker_id === speakerId) next[message.id] = role
      }
      return next
    })
  }

  const saveMutation = useMutation({
    mutationFn: () =>
      apiFetch<{ case_id: string; status: string; changed: boolean; findings_count: number }>(
        `/api/app/knowledge-bases/${kbSlug}/support-cases/${caseId}/roles`,
        {
          method: 'PATCH',
          body: JSON.stringify({ analysis_revision: analysisRevision, message_roles: edits }),
        },
      ),
    onSuccess: (res) => {
      setStale(false)
      setEdits({})
      invalidateCaseCaches(queryClient, caseId, kbSlug)
      if (res.status === 'failed') {
        toast.error(m.support_case_reanalyze_failed())
        return
      }
      toast.success(m.support_case_roles_saved())
    },
    onError: (err: unknown) => {
      queryLogger.warn('Support case role save failed', { error: err })
      if (err instanceof ApiError && err.status === 409) {
        setStale(true)
        return
      }
      if (err instanceof ApiError && err.status === 503) {
        invalidateCaseCaches(queryClient, caseId, kbSlug)
        toast.error(m.support_case_reanalyze_failed())
        return
      }
      toast.error(m.support_case_roles_save_error())
    },
  })

  const hasEdits = Object.keys(edits).length > 0

  return (
    <form
      className="space-y-3 rounded-xl border border-gray-200 p-4"
      onSubmit={(e) => {
        e.preventDefault()
        if (hasEdits) saveMutation.mutate()
      }}
    >
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold text-gray-900">{m.support_case_roles_heading()}</h2>
        {needsReview && <Badge variant="warning">{m.support_case_roles_needs_review()}</Badge>}
      </div>
      <p className="text-xs text-gray-600">{m.support_case_roles_intro()}</p>

      {speakers.length > 0 && (
        <div className="space-y-2 rounded-lg border border-gray-200 bg-gray-50 p-3">
          {speakers.map((speakerId) => {
            const label = m.support_case_roles_speaker({ id: speakerId })
            return (
              <div key={speakerId} className="flex flex-wrap items-center gap-2">
                <span className="text-xs text-gray-600">
                  {m.support_case_roles_bulk_label({ speaker: label })}
                </span>
                <Select
                  aria-label={label}
                  value={bulkChoice[speakerId] ?? ''}
                  onChange={(e) =>
                    setBulkChoice((prev) => ({ ...prev, [speakerId]: e.target.value as CaseMessageRole }))
                  }
                  className="w-auto"
                >
                  <option value="" disabled>
                    {m.support_case_roles_choose()}
                  </option>
                  <RoleOptions />
                </Select>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={!bulkChoice[speakerId]}
                  onClick={() => applyBulk(speakerId)}
                >
                  {m.support_case_roles_bulk_apply()}
                </Button>
              </div>
            )
          })}
        </div>
      )}

      <div className="space-y-2">
        {messages.map((message, i) => (
          <div key={message.id} className="rounded-lg border border-gray-200 p-3">
            <div className="mb-1 flex flex-wrap items-center gap-2">
              {message.start_seconds != null && message.end_seconds != null && (
                <span className="text-xs tabular-nums text-gray-500">
                  {m.support_case_segment_time({
                    start: String(Math.round(message.start_seconds)),
                    end: String(Math.round(message.end_seconds)),
                  })}
                </span>
              )}
              {message.speaker_id && (
                <span className="text-xs text-gray-500">
                  {m.support_case_roles_speaker({ id: message.speaker_id })}
                </span>
              )}
            </div>
            <p className="mb-2 text-sm text-gray-900 whitespace-pre-wrap break-words">{message.text}</p>
            <div className="space-y-1">
              <Label htmlFor={`role-${message.id}`}>
                {`${m.support_case_roles_role_label()} ${i + 1}`}
              </Label>
              <Select
                id={`role-${message.id}`}
                value={roleOf(message)}
                onChange={(e) =>
                  setEdits((prev) => ({ ...prev, [message.id]: e.target.value as CaseMessageRole }))
                }
                className="w-auto"
              >
                <RoleOptions />
              </Select>
            </div>
          </div>
        ))}
      </div>

      {stale && <CaseStalePrompt caseId={caseId} message={m.support_case_roles_stale()} />}

      <Button type="submit" size="sm" disabled={!hasEdits || saveMutation.isPending}>
        {saveMutation.isPending ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin mr-2" />
            {m.support_case_review_saving()}
          </>
        ) : (
          m.support_case_roles_save()
        )}
      </Button>
    </form>
  )
}
