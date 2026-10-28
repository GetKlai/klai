// Shared between the gap inbox list and its detail route (SPEC-KNOWLEDGE-ACTIVITY-001
// §4.5/§4.9): the `/api/app/gaps` response shape, and the two small display
// helpers both screens need for a closed row (relative time, closer name).
import { getLocale } from '@/paraglide/runtime'
import * as m from '@/paraglide/messages'

export interface GapRow {
  query_text: string
  // "hard"/"soft" for retrieval telemetry; "content" for support-case findings
  // (support-gap-detection.md: support findings use gap_type="content").
  gap_type: string
  top_score: number | null
  nearest_kb_slug: string | null
  occurrence_count: number
  last_occurred: string
  language: string | null
  source: 'automatic' | 'review' | 'judge' | 'support'
  conversation_id: number | null
  resolved_at: string | null
  resolved_by: 'rescorer' | 'review' | 'manual' | 'test' | null
  resolved_by_name: string | null
  // Support-case fields (null for automatic/review rows). diagnosis is one of
  // the six content diagnoses; support_case_ids links the evidence detail page.
  diagnosis: string | null
  audience: string | null
  support_case_ids: number[]
  support_sources?: string[]
  group_key?: string | null
  topic: { id: number; name: string } | null
}

export interface GapsResponse {
  gaps: GapRow[]
  total: number
}

export interface KnowledgeBase {
  id: number
  name: string
  slug: string
  owner_type: string
}

export interface KBsResponse {
  knowledge_bases: KnowledgeBase[]
}

/** Relative timestamp for a closed row/status line. */
export function formatRelativeTime(isoString: string): string {
  // The portal language, not the browser's: mixed-language lines otherwise.
  const rtf = new Intl.RelativeTimeFormat(getLocale(), { numeric: 'auto' })
  const diffSeconds = (new Date(isoString).getTime() - Date.now()) / 1000
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ['year', 31536000],
    ['month', 2592000],
    ['day', 86400],
    ['hour', 3600],
    ['minute', 60],
  ]
  for (const [unit, seconds] of units) {
    if (Math.abs(diffSeconds) >= seconds) {
      return rtf.format(Math.round(diffSeconds / seconds), unit)
    }
  }
  return rtf.format(Math.round(diffSeconds), 'second')
}

/** "rescorer", "beoordeling/review" and "testbericht" are fixed labels; only
    'manual' names the actual colleague who closed it. */
export function closerLabel(gap: Pick<GapRow, 'resolved_by' | 'resolved_by_name'>): string {
  if (gap.resolved_by === 'rescorer') return m.gaps_resolved_by_rescorer()
  if (gap.resolved_by === 'review') return m.gaps_resolved_by_review()
  if (gap.resolved_by === 'test') return m.gaps_resolved_by_test()
  return gap.resolved_by_name ?? ''
}
