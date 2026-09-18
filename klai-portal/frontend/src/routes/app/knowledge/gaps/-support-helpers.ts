// Shared helpers for the support gap inbox, the support-case list and the
// support-case detail page (support-gap-detection.md). All three route files
// under `gaps/` use these, so per the file-organisation rule they live in a
// `-`-prefixed sibling rather than being duplicated or imported across routes.
import type { QueryClient } from '@tanstack/react-query'
import * as m from '@/paraglide/messages'

export type CaseMessageRole = 'customer' | 'agent' | 'unknown'

/** One conversation turn (email/chat message or call segment). Shared by the
    detail page, the speaker-role editor and the reference form, so it lives
    here rather than being redefined in each. */
export interface CaseMessage {
  id: string
  kind: string
  role: CaseMessageRole
  text: string
  occurred_at: string | null
  visibility: 'customer' | 'internal' | 'unknown'
  start_seconds: number | null
  end_seconds: number | null
  medium: string | null
  thread_id: string | null
  reply_to_id: string | null
  speaker_id: string | null
}

export interface CaseReferenceQuestion {
  question: string
  diagnosis: string
  message_ids: string[]
}

/** The reviewer's whole-case answer key, saved separately from the machine
    analysis. `complete` with an empty `questions` records "reviewed, nothing
    reusable"; it is never auto-filled from the analyser's findings. */
export interface CaseReference {
  content_hash: string
  questions: CaseReferenceQuestion[]
  complete: boolean
  reviewed_by: string
  reviewed_at: string
}

/** The nine analyser diagnoses, in the order a reviewer scans them (content
    gaps first, then the non-gap outcomes). Used by the finding-review diagnosis
    correction and the whole-case reference form so both offer the same set. */
export const DIAGNOSES = [
  'missing',
  'incomplete',
  'outdated',
  'contradictory',
  'findability',
  'audience',
  'covered',
  'non_knowledge',
  'uncertain',
] as const

export type Diagnosis = (typeof DIAGNOSES)[number]

/** `{value,label}` options for a diagnosis `<Select>`; the label is localised
    through the same `diagnosisLabel` the read-only finding view uses. */
export function diagnosisOptions(): { value: Diagnosis; label: string }[] {
  return DIAGNOSES.map((value) => ({ value, label: diagnosisLabel(value) }))
}

/** Every reviewer mutation on a case changes what the detail page, the case
    list and the gap inbox show, so all three caches are refetched together. */
export function invalidateCaseCaches(queryClient: QueryClient, caseId: string, kbSlug: string): void {
  void queryClient.invalidateQueries({ queryKey: ['support-case', caseId] })
  void queryClient.invalidateQueries({ queryKey: ['support-cases', kbSlug] })
  void queryClient.invalidateQueries({ queryKey: ['app-gaps'] })
}

/** A finding is not always a content gap: the analyser also reports that the
    knowledge base already covered the question, that it was uncertain, or that
    the question was not a knowledge question at all. These outcomes must read
    as outcomes, never as gaps (support-gap-detection.md). */
export type FindingKind = 'gap' | 'covered' | 'uncertain' | 'non_knowledge'

export function findingKind(diagnosis: string): FindingKind {
  switch (diagnosis) {
    case 'covered': return 'covered'
    case 'uncertain': return 'uncertain'
    case 'non_knowledge': return 'non_knowledge'
    default: return 'gap'
  }
}

/** Localised label for a finding's diagnosis or outcome. Falls back to the raw
    value if the backend ever adds one this UI does not know yet. */
export function diagnosisLabel(diagnosis: string): string {
  switch (diagnosis) {
    case 'missing': return m.gaps_diagnosis_missing()
    case 'incomplete': return m.gaps_diagnosis_incomplete()
    case 'outdated': return m.gaps_diagnosis_outdated()
    case 'contradictory': return m.gaps_diagnosis_contradictory()
    case 'findability': return m.gaps_diagnosis_findability()
    case 'audience': return m.gaps_diagnosis_audience()
    case 'covered': return m.support_case_outcome_covered()
    case 'uncertain': return m.support_case_outcome_uncertain()
    case 'non_knowledge': return m.support_case_outcome_non_knowledge()
    default: return diagnosis
  }
}

export type SupportBadgeVariant = 'destructive' | 'warning' | 'info' | 'secondary'

/** Status pill for an imported support case. `analyzed` is the only terminal
    success; everything else is a pending/failed/incomplete state that must be
    shown distinctly rather than rendered as an empty analysis. */
export function supportCaseStatusBadge(status: string): { variant: SupportBadgeVariant; label: string } {
  switch (status) {
    case 'failed': return { variant: 'destructive', label: m.support_case_status_failed() }
    case 'incomplete': return { variant: 'warning', label: m.support_case_status_incomplete() }
    case 'analyzed': return { variant: 'info', label: m.support_case_status_analyzed() }
    default: return { variant: 'secondary', label: m.support_case_status_pending() }
  }
}

/** The vendor the case was imported from, kept separate from the per-message
    medium (a HubSpot thread can carry email and chat messages). */
export function caseSourceLabel(source: string): string {
  switch (source) {
    case 'audio': return m.support_case_source_audio()
    case 'hubspot': return m.support_case_source_hubspot()
    default: return source
  }
}
