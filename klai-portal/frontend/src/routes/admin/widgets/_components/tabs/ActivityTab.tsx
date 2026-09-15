import { useState } from 'react'
import { Link } from '@tanstack/react-router'
import { Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'
import { parseActivitySearch } from '@/routes/app/knowledge/activity/-search'
import { useWidgetStats } from '../../-hooks'
import type { StatsPeriod, WidgetDetailResponse } from '../../-types'

// SPEC-WIDGET-ACTIVITY-001 - Activiteit tab: live audit trail of
// every chat that flows through the widget. Period picker drives a
// stats panel + hourly sparkline + top-queries list.
//
// SPEC-KNOWLEDGE-ACTIVITY-001 §3 - reviewing individual conversations moved to
// the knowledge side; this tab links there instead of showing its own list and
// drawer.

// REQ-9 (Finding B-9): the URL scheme allowlist behind conversation source
// links moved to @/features/chat-activity with the transcript itself; the
// re-export keeps existing importers (and its unit test) in place.
export { _isSafeHttpUrl } from '@/features/chat-activity/urlAllowlist'

interface Props {
  widget: WidgetDetailResponse
}

const PERIOD_OPTIONS: { value: StatsPeriod; label: string }[] = [
  { value: '7d', label: '7 dagen' },
  { value: '30d', label: '30 dagen' },
  { value: 'all', label: 'Alles' },
]

export function ActivityTab({ widget }: Props) {
  const [period, setPeriod] = useState<StatsPeriod>('7d')

  const widgetId = String(widget.id)
  const statsQuery = useWidgetStats(widgetId, period)
  const outcomes = statsQuery.data?.outcome_counts

  return (
    <section className="space-y-8">
      {/* Period picker */}
      <div role="radiogroup" className="inline-flex items-center gap-0.5 rounded-full border border-gray-200 p-0.5">
        {PERIOD_OPTIONS.map((opt) => (
          <Button
            key={opt.value}
            type="button"
            variant="outline"
            size="sm"
            role="radio"
            aria-checked={period === opt.value}
            onClick={() => setPeriod(opt.value)}
            className={
              period === opt.value
                ? 'rounded-full bg-gray-900 px-4 py-1.5 text-[0.75rem] font-medium text-white transition-colors'
                : 'rounded-full px-4 py-1.5 text-[0.75rem] text-gray-500 hover:text-gray-900 klai-hover'
            }
          >
            {opt.label}
          </Button>
        ))}
      </div>

      {/* Stats cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <StatCard
          label="Gesprekken"
          value={statsQuery.data?.total_conversations}
          loading={statsQuery.isLoading}
        />
        <StatCard
          label="Berichten"
          value={statsQuery.data?.total_messages}
          loading={statsQuery.isLoading}
        />
        <StatCard
          label="Gem. berichten / gesprek"
          value={
            statsQuery.data
              ? statsQuery.data.avg_messages_per_conversation.toFixed(1)
              : undefined
          }
          loading={statsQuery.isLoading}
        />
      </div>

      {/* Outcome distribution - heuristic labels, not a verdict */}
      <div>
        <SectionHeading>{m.admin_widgets_activity_outcomes_title()}</SectionHeading>
        <div className="rounded-xl border border-gray-200 bg-white px-4 py-3">
          {statsQuery.isLoading ? (
            <p className="text-sm text-gray-600">
              <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
              {m.admin_shared_loading()}
            </p>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 md:grid-cols-5">
                <OutcomeStat
                  label={m.admin_widgets_activity_outcome_resolved()}
                  value={outcomes?.resolved ?? 0}
                />
                <OutcomeStat
                  label={m.admin_widgets_activity_outcome_escalated()}
                  value={outcomes?.escalated ?? 0}
                />
                <OutcomeStat
                  label={m.admin_widgets_activity_outcome_abandoned()}
                  value={outcomes?.abandoned ?? 0}
                />
                <OutcomeStat
                  label={m.admin_widgets_activity_outcome_unknown()}
                  value={outcomes?.unknown ?? 0}
                />
                <OutcomeStat
                  label={m.admin_widgets_activity_outcome_unlabeled()}
                  value={outcomes?.unlabeled ?? 0}
                />
              </div>
              <p className="mt-3 text-xs leading-5 text-gray-600">
                {m.admin_widgets_activity_outcomes_note()}
              </p>
            </>
          )}
        </div>
      </div>

      {/* Hourly activity sparkline */}
      <div>
        <SectionHeading>{m.admin_widgets_activity_hourly_title()}</SectionHeading>
        <HourlySparkline data={statsQuery.data?.hourly_activity} />
      </div>

      {/* Top queries */}
      <div>
        <SectionHeading>{m.admin_widgets_activity_top_questions_title()}</SectionHeading>
        {statsQuery.isLoading ? (
          <p className="text-sm text-gray-600">{m.admin_shared_loading()}</p>
        ) : (statsQuery.data?.top_queries ?? []).length === 0 ? (
          <p className="text-sm text-gray-600">{m.admin_widgets_activity_no_questions()}</p>
        ) : (
          <ol className="space-y-2">
            {(statsQuery.data?.top_queries ?? []).map((q, idx) => (
              <li
                key={`${q.query}-${idx}`}
                className="flex items-start justify-between gap-3 rounded-lg border border-gray-200 px-3 py-2.5"
              >
                <span className="text-sm text-gray-900 truncate">{q.query}</span>
                <span className="text-xs font-medium text-gray-600 tabular-nums shrink-0">
                  {q.count}×
                </span>
              </li>
            ))}
          </ol>
        )}
      </div>

      {/* SPEC-KNOWLEDGE-ACTIVITY-001 §3: reviewing conversations happens on
          the knowledge side, pre-filtered to this widget. The list's own search
          parser supplies the defaults so this tab stops guessing them. */}
      <Button asChild variant="outline">
        <Link
          to="/app/knowledge/activity"
          search={parseActivitySearch({ widget_id: String(widget.id) })}
        >
          {m.admin_widgets_activity_review_conversations_link()}
        </Link>
      </Button>
    </section>
  )
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="text-[0.6875rem] font-semibold uppercase tracking-[0.06em] text-gray-600 mb-3">
      {children}
    </h3>
  )
}

function StatCard({
  label,
  value,
  loading,
}: {
  label: string
  value: number | string | undefined
  loading: boolean
}) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white px-4 py-3">
      <p className="text-[0.6875rem] font-semibold uppercase tracking-[0.06em] text-gray-600">
        {label}
      </p>
      <p className="mt-1 text-2xl font-display-bold text-gray-900 tabular-nums">
        {loading ? (
          <Loader2 className="inline h-4 w-4 animate-spin text-gray-500" />
        ) : value === undefined ? (
          '-'
        ) : (
          value
        )}
      </p>
    </div>
  )
}

function OutcomeStat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <p className="text-[0.6875rem] font-semibold uppercase tracking-[0.06em] text-gray-600">
        {label}
      </p>
      <p className="mt-0.5 text-lg font-display-bold text-gray-900 tabular-nums">
        {value}
      </p>
    </div>
  )
}

function HourlySparkline({ data }: { data: number[] | undefined }) {
  const buckets = data ?? Array(24).fill(0)
  const max = Math.max(1, ...buckets)
  return (
    <div className="rounded-xl border border-gray-200 bg-white px-4 pt-4 pb-2">
      <div className="flex items-end gap-1 h-20">
        {buckets.map((count, hour) => {
          const heightPct = (count / max) * 100
          return (
            <div
              key={hour}
              className="flex-1 flex flex-col items-center justify-end h-full"
              title={`${hour}:00 - ${count} ${count === 1 ? 'gesprek' : 'gesprekken'}`}
            >
              <div
                className="w-full rounded-t bg-[var(--color-rl-accent)] transition-all"
                style={{
                  height: count > 0 ? `${Math.max(heightPct, 6)}%` : '2px',
                  opacity: count > 0 ? 1 : 0.3,
                }}
              />
            </div>
          )
        })}
      </div>
      <div className="mt-1 flex justify-between text-[0.625rem] text-gray-600 tabular-nums">
        <span>00</span>
        <span>06</span>
        <span>12</span>
        <span>18</span>
        <span>23</span>
      </div>
    </div>
  )
}
