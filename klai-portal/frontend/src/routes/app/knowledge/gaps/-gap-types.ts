// Small shapes and display helpers shared by the Knowledge gaps list, the group
// detail and the transcript import: the organisation's knowledge bases, and a
// relative timestamp in the portal language.
import { getLocale } from '@/paraglide/runtime'

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
