// SPEC-KNOWLEDGE-ACTIVITY-001 §4.3: the confidence signals the answer pipeline
// wrote for one assistant turn, shown as a Chat Disclosure Row under the answer
// so provenance stays available but secondary to the answer itself.
import { ChevronRight } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import type { ConversationAnswerSignals } from './types'
import * as m from '@/paraglide/messages'

const BAND_BADGE_VARIANT: Record<
  NonNullable<ConversationAnswerSignals['band']>,
  'success' | 'warning' | 'secondary'
> = {
  high: 'success',
  medium: 'secondary',
  low: 'warning',
  unknown: 'secondary',
}

const BAND_LABEL: Record<NonNullable<ConversationAnswerSignals['band']>, () => string> = {
  high: m.activity_band_high,
  medium: m.activity_band_medium,
  low: m.activity_band_low,
  unknown: m.activity_band_unknown,
}

export function AnswerSignals({ signals }: { signals: ConversationAnswerSignals | null }) {
  if (!signals) return null

  const facts: { label: string; value: string }[] = []
  if (typeof signals.top_score === 'number') {
    facts.push({ label: m.activity_signals_top_score(), value: signals.top_score.toFixed(2) })
  }
  if (typeof signals.sources_count === 'number') {
    facts.push({
      label: m.activity_signals_sources_label(),
      value:
        signals.sources_count === 1
          ? m.activity_signals_sources_count_one()
          : m.activity_signals_sources_count_other({ count: String(signals.sources_count) }),
    })
  }
  if (signals.refused) facts.push({ label: m.activity_signals_answer(), value: m.activity_signals_refused() })
  if (signals.broad_mode) facts.push({ label: m.activity_signals_answer(), value: m.activity_signals_broad_mode() })
  if (signals.language) facts.push({ label: m.activity_signals_language(), value: signals.language })
  if (signals.model) facts.push({ label: m.activity_signals_model(), value: signals.model })

  return (
    <div className="mt-4 space-y-0.5">
      <details className="group max-w-xl bg-transparent">
        <summary className="inline-flex min-h-7 cursor-pointer list-none items-center gap-1.5 rounded-md px-1 py-0.5 text-[13px] text-[color:rgb(25_25_24_/_0.5)] hover:bg-[var(--color-muted)]/60 hover:text-gray-900 [&::-webkit-details-marker]:hidden">
          <ChevronRight className="h-3 w-3 shrink-0 text-[color:rgb(25_25_24_/_0.3)] transition-transform group-open:rotate-90" />
          <span className="min-w-0 flex-1 font-medium">{m.activity_signals_title()}</span>
          {typeof signals.sources_count === 'number' && (
            <span className="shrink-0 text-xs font-normal tabular-nums text-[color:rgb(25_25_24_/_0.3)] before:mr-1.5 before:text-[color:rgb(25_25_24_/_0.2)] before:content-['·']">
              {signals.sources_count === 1
                ? m.activity_signals_sources_count_one()
                : m.activity_signals_sources_count_other({ count: String(signals.sources_count) })}
            </span>
          )}
        </summary>
        <div className="pb-2 pl-4 pt-1 text-[13px] text-[color:rgb(25_25_24_/_0.5)]">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
            {signals.band && (
              <Badge variant={BAND_BADGE_VARIANT[signals.band]}>{BAND_LABEL[signals.band]()}</Badge>
            )}
            {facts.map((fact) => (
              <span key={fact.label} className="whitespace-nowrap">
                {fact.label}:{' '}
                <span className="font-medium text-gray-900 tabular-nums">{fact.value}</span>
              </span>
            ))}
          </div>
        </div>
      </details>
    </div>
  )
}
