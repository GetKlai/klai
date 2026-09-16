// SPEC-KNOWLEDGE-ACTIVITY-001 §4.3: the activity list's URL search state, kept
// out of the route file so links and tests build the search the route validates.
import type { ConversationBand } from '@/features/chat-activity'

export type ActivitySearch = {
  days: number
  widget_id?: string
  language?: string
  judge_outcome?: string
  failure_category?: string
  review_status?: 'unreviewed' | 'reviewed'
  cause?: string
  band?: ConversationBand
  rating?: 'thumbsUp' | 'thumbsDown' | 'none'
  queue: boolean
  sort: 'newest' | 'worst'
  cursor?: string
}

export const DAYS = [7, 14, 30]
export const BANDS: ConversationBand[] = ['high', 'medium', 'low', 'unknown']
export const OUTCOMES = [
  'resolved',
  'partially_resolved',
  'escalated',
  'unresolved',
  'abandoned_early',
  'out_of_scope',
]
export const RATINGS = ['thumbsUp', 'thumbsDown', 'none'] as const

export const ofSet = <T extends string | number>(allowed: readonly T[], value: unknown): T | undefined =>
  allowed.includes(value as T) ? (value as T) : undefined

const text = (value: unknown): string | undefined =>
  typeof value === 'string' && value ? value : undefined

const parseBoolean = (value: unknown, fallback: boolean): boolean =>
  value === 'true' || value === true
    ? true
    : value === 'false' || value === false
      ? false
      : fallback

/**
 * Serialises the list's filters into one flat string. main.tsx installs a
 * router-wide `stringifySearch`/`parseSearch` that only supports flat string
 * values (kept flat so 18-digit Zitadel ids in the URL are never coerced to
 * Number); a nested object under a search key stringifies as
 * "[object Object]" instead of a query string. Carrying the list's filters as
 * one string value (the `back` param) keeps them inside that flat contract.
 */
export function stringifyActivitySearch(search: ActivitySearch): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(search)) {
    if (value !== undefined && value !== null) params.set(key, String(value))
  }
  return params.toString()
}

/** The list's URL search contract: exported so links and tests build the same
    search the route validates, instead of guessing at the defaults. */
export function parseActivitySearch(search: Record<string, unknown>): ActivitySearch {
  return {
    days: ofSet(DAYS, Number(search.days)) ?? 7,
    widget_id: text(search.widget_id),
    language: text(search.language),
    judge_outcome: ofSet(OUTCOMES, search.judge_outcome),
    failure_category: text(search.failure_category),
    review_status: ofSet(['unreviewed', 'reviewed'] as const, search.review_status),
    cause: text(search.cause),
    band: ofSet(BANDS, search.band),
    rating: ofSet(RATINGS, search.rating),
    queue: parseBoolean(search.queue, true),
    sort: ofSet(['newest', 'worst'] as const, search.sort) ?? 'newest',
    cursor: text(search.cursor),
  }
}
