// SPEC-KNOWLEDGE-ACTIVITY-001 §4.3: the activity list's URL search state, kept
// out of the route file so links and tests build the search the route validates.
import { BANDS, CAUSES, FAILURE_CATEGORIES, OUTCOMES, type ConversationBand } from '@/features/chat-activity'

export type ActivitySearch = {
  days: number
  widget_id?: string
  language?: string
  judge_outcome: string[]
  failure_category: string[]
  review_status?: 'unreviewed' | 'reviewed'
  cause: string[]
  band: ConversationBand[]
  rating?: 'thumbsUp' | 'thumbsDown' | 'none'
  sort: 'newest' | 'worst'
  cursor?: string
}

export const DAYS = [7, 14, 30]
export const RATINGS = ['thumbsUp', 'thumbsDown', 'none'] as const

export const ofSet = <T extends string | number>(allowed: readonly T[], value: unknown): T | undefined =>
  allowed.includes(value as T) ? (value as T) : undefined

/**
 * Parses one comma-separated URL value into the allowed codes it contains,
 * dropping anything that is not in `allowed` — the repeatable filter fields
 * (`judge_outcome`, `failure_category`, `cause`, `band`) are encoded as one
 * flat comma-joined string, not a repeated query key, to stay inside
 * main.tsx's flat search-value contract (see `stringifyActivitySearch`).
 */
const ofSetMulti = <T extends string>(allowed: readonly T[], value: unknown): T[] => {
  if (typeof value !== 'string' || !value) return []
  return value.split(',').filter((v): v is T => (allowed as readonly string[]).includes(v))
}

const text = (value: unknown): string | undefined =>
  typeof value === 'string' && value ? value : undefined

/**
 * Serialises the list's filters into one flat string. main.tsx installs a
 * router-wide `stringifySearch`/`parseSearch` that only supports flat string
 * values (kept flat so 18-digit Zitadel ids in the URL are never coerced to
 * Number); a nested object under a search key stringifies as
 * "[object Object]" instead of a query string. Carrying the list's filters as
 * one string value (the `back` param) keeps them inside that flat contract.
 * A multi-value filter is joined with a comma; an empty array is omitted.
 */
export function stringifyActivitySearch(search: ActivitySearch): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(search)) {
    if (value === undefined || value === null) continue
    if (Array.isArray(value)) {
      if (value.length > 0) params.set(key, value.join(','))
    } else {
      params.set(key, String(value))
    }
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
    judge_outcome: ofSetMulti(OUTCOMES, search.judge_outcome),
    failure_category: ofSetMulti(FAILURE_CATEGORIES, search.failure_category),
    review_status: ofSet(['unreviewed', 'reviewed'] as const, search.review_status),
    cause: ofSetMulti(CAUSES, search.cause),
    band: ofSetMulti(BANDS, search.band),
    rating: ofSet(RATINGS, search.rating),
    sort: ofSet(['newest', 'worst'] as const, search.sort) ?? 'newest',
    cursor: text(search.cursor),
  }
}
