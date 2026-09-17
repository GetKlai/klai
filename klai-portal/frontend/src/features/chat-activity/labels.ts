// SPEC-KNOWLEDGE-ACTIVITY-001 §4.3: shared code lists and code->label maps for
// the raw strings the activity API returns (judge outcome, failure category,
// review cause, review verdict, certainty band). The list route, the
// calibration/quality panels and the review form all render the same human
// text for the same code, so this is the one place that pairs a code with
// its Paraglide message instead of each surface keeping its own copy.
import * as m from '@/paraglide/messages'
import type { ConversationBand } from './api'

export const OUTCOMES = [
  'resolved',
  'partially_resolved',
  'escalated',
  'unresolved',
  'abandoned_early',
  'out_of_scope',
] as const

export const FAILURE_CATEGORIES = [
  'retrieval_miss',
  'retrieval_wrong',
  'generation_error',
  'policy_refusal',
  'scope_mismatch',
  'user_confusion',
] as const

export const CAUSES = ['knowledge_missing', 'knowledge_wrong', 'behaviour'] as const

export const BANDS: ConversationBand[] = ['high', 'medium', 'low', 'unknown']

export const OUTCOME_LABEL: Record<string, () => string> = {
  resolved: m.activity_outcome_resolved,
  partially_resolved: m.activity_outcome_partially_resolved,
  escalated: m.activity_outcome_escalated,
  unresolved: m.activity_outcome_unresolved,
  abandoned_early: m.activity_outcome_abandoned_early,
  out_of_scope: m.activity_outcome_out_of_scope,
}

export const FAILURE_CATEGORY_LABEL: Record<string, () => string> = {
  retrieval_miss: m.activity_failure_category_retrieval_miss,
  retrieval_wrong: m.activity_failure_category_retrieval_wrong,
  generation_error: m.activity_failure_category_generation_error,
  policy_refusal: m.activity_failure_category_policy_refusal,
  scope_mismatch: m.activity_failure_category_scope_mismatch,
  user_confusion: m.activity_failure_category_user_confusion,
}

export const CAUSE_LABEL: Record<string, () => string> = {
  knowledge_missing: m.activity_cause_knowledge_missing,
  knowledge_wrong: m.activity_cause_knowledge_wrong,
  behaviour: m.activity_cause_behaviour,
}

export const VERDICT_LABEL: Record<string, () => string> = {
  correct: m.activity_verdict_correct,
  incomplete: m.activity_verdict_incomplete,
  wrong: m.activity_verdict_wrong,
  not_a_fault: m.activity_verdict_not_a_fault,
}

export const BAND_LABEL: Record<ConversationBand, () => string> = {
  high: m.activity_band_high,
  medium: m.activity_band_medium,
  low: m.activity_band_low,
  unknown: m.activity_band_unknown,
}
