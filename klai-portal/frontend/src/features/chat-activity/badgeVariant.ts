import type { ConversationQuality } from './types'

/**
 * Outcome → existing Badge semantic variant; no ad-hoc colors. Shared by the
 * judge panel and the knowledge activity list so both surfaces read the same
 * meaning for the same verdict (SPEC-KNOWLEDGE-ACTIVITY-001 §4.3).
 */
export const OUTCOME_BADGE_VARIANT: Record<
  ConversationQuality['outcome'],
  'success' | 'warning' | 'secondary'
> = {
  resolved: 'success',
  partially_resolved: 'success',
  escalated: 'warning',
  unresolved: 'secondary',
  abandoned_early: 'secondary',
  out_of_scope: 'secondary',
}
