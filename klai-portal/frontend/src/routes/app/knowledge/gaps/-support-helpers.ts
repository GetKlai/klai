// Shared helpers for the support gap inbox, the support-case list and the
// support-case detail page (support-gap-detection.md). All three route files
// under `gaps/` use these, so per the file-organisation rule they live in a
// `-`-prefixed sibling rather than being duplicated or imported across routes.
import * as m from '@/paraglide/messages'

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
