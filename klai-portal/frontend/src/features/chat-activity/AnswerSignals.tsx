// SPEC-KNOWLEDGE-ACTIVITY-001 §4.3: the confidence signals and sources for
// the answer under review, always visible next to the review form — a
// reviewer must see the evidence before judging, not open a disclosure for
// it. The judge panel above (QualityPanel) stays a separate, existing block.
import { Badge } from '@/components/ui/badge'
import { _isSafeHttpUrl } from './urlAllowlist'
import type { ConversationAnswerSignals, MessageSource } from './types'
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

function answerTypeLabel(signals: ConversationAnswerSignals): string {
  if (signals.refused) return m.activity_signals_refused()
  if (signals.broad_mode) return m.activity_signals_broad_mode()
  return m.activity_signals_answer_type_normal()
}

function gapLabel(gapType: ConversationAnswerSignals['gap_type']): string {
  if (gapType === 'hard') return m.gaps_type_hard()
  if (gapType === 'soft') return m.gaps_type_soft()
  return m.activity_cause_none()
}

export function AnswerSignals({
  signals,
  sources,
}: {
  signals: ConversationAnswerSignals | null
  sources: MessageSource[] | null
}) {
  if (!signals) {
    return <p className="mt-4 text-xs text-gray-500">{m.activity_signals_none()}</p>
  }

  const sourceList = sources ?? []

  return (
    <div className="mt-4 space-y-3 rounded-xl border border-gray-200 px-4 py-3">
      <dl className="grid grid-cols-2 gap-x-3 gap-y-2.5 text-xs">
        <div>
          <dt className="text-gray-500">{m.activity_signals_certainty_label()}</dt>
          <dd className="mt-1 flex items-center gap-1.5">
            {signals.band && (
              <Badge variant={BAND_BADGE_VARIANT[signals.band]}>{BAND_LABEL[signals.band]()}</Badge>
            )}
            {typeof signals.top_score === 'number' && (
              <span className="font-medium tabular-nums text-gray-900">{signals.top_score.toFixed(2)}</span>
            )}
          </dd>
        </div>
        <div>
          <dt className="text-gray-500">{m.activity_signals_language()}</dt>
          <dd className="mt-1 font-medium text-gray-900">{signals.language ?? '—'}</dd>
        </div>
        <div>
          <dt className="text-gray-500">{m.activity_signals_model()}</dt>
          <dd className="mt-1 truncate font-medium text-gray-900">{signals.model ?? '—'}</dd>
        </div>
        <div>
          <dt className="text-gray-500">{m.activity_signals_answer_type_label()}</dt>
          <dd className="mt-1 font-medium text-gray-900">{answerTypeLabel(signals)}</dd>
        </div>
        <div>
          <dt className="text-gray-500">{m.activity_signals_gap_label()}</dt>
          <dd className="mt-1 font-medium text-gray-900">{gapLabel(signals.gap_type ?? null)}</dd>
        </div>
      </dl>

      <div>
        <p className="text-xs font-medium text-gray-900">
          {m.activity_signals_sources_label()} ({sourceList.length})
        </p>
        {sourceList.length > 0 && (
          <ul className="mt-1.5 space-y-1 text-xs">
            {sourceList.map((source) => (
              <li key={`${source.label}-${source.title}`} className="text-gray-700">
                {/* REQ-9: only http/https schemes render as anchors, same allowlist as the transcript */}
                {_isSafeHttpUrl(source.url) ? (
                  <a
                    href={source.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="klai-hover text-gray-900 underline underline-offset-2"
                  >
                    {source.title}
                  </a>
                ) : (
                  <span className="text-gray-900">{source.title}</span>
                )}
                <span className="ml-1 text-gray-500">({source.label})</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
