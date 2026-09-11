import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Loader2, MessageSquare, ThumbsDown, ThumbsUp, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { apiFetch } from '@/lib/apiFetch'
import * as m from '@/paraglide/messages'
import {
  useWidgetConversations,
  useWidgetConversation,
  useWidgetStats,
} from '../../-hooks'
import type {
  StatsPeriod,
  WidgetDetailResponse,
  ConversationListItem,
} from '../../-types'

// SPEC-WIDGET-ACTIVITY-001 - Activiteit tab: live audit trail of
// every chat that flows through the widget. Period picker drives a
// stats panel + hourly sparkline + top-queries list; a paginated
// recent-conversations list opens a side drawer with the full
// transcript.

/**
 * REQ-9 (Finding B-9): URL scheme allowlist for conversation source links.
 *
 * An LLM-controlled source URL could contain a `javascript:` URI. React 18+
 * still navigates on javascript: hrefs, enabling stored-XSS in the admin
 * session on my.getklai.com (CC-2 exploit chain).
 *
 * Only http: and https: schemes are allowed as clickable anchors. All other
 * schemes (javascript:, data:, vbscript:, file:, mailto:, scheme-less, etc.)
 * render as plain text without an href.
 *
 * Leading whitespace is stripped before the check to block bypass attempts
 * like "  javascript:alert(1)". Case is normalised by URL() itself.
 *
 * @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-9
 */
export function _isSafeHttpUrl(url: string): boolean {
  const trimmed = url.trim()
  if (!trimmed) return false
  try {
    const parsed = new URL(trimmed)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:'
  } catch {
    // URL() throws on relative paths, scheme-less, and malformed inputs.
    return false
  }
}

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
  const [openConvId, setOpenConvId] = useState<number | null>(null)

  const widgetId = String(widget.id)
  const statsQuery = useWidgetStats(widgetId, period)
  const convsQuery = useWidgetConversations(widgetId)
  const conversations: ConversationListItem[] = Array.isArray(convsQuery.data)
    ? convsQuery.data
    : []
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

      {/* Recent conversations */}
      <div>
        <SectionHeading>{m.admin_widgets_activity_recent_conversations_title()}</SectionHeading>
        {convsQuery.isLoading ? (
          <p className="text-sm text-gray-600">
            <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
            {m.admin_widgets_loading()}
          </p>
        ) : conversations.length === 0 ? (
          <p className="text-sm text-gray-600">
            Nog geen gesprekken. Zodra iemand met de bot praat verschijnt
            het hier.
          </p>
        ) : (
          <ul className="divide-y divide-gray-200 border-t border-b border-gray-200">
            {conversations.map((c) => (
              <li key={c.id}>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setOpenConvId(c.id)}
                  className="h-auto w-full justify-start rounded-none px-2 py-3.5 text-left"
                >
                  <MessageSquare className="h-4 w-4 mt-0.5 text-gray-500 shrink-0" />
                  <div className="flex-1 min-w-0">
                    <p className="truncate text-sm text-gray-900">
                      {c.first_user_query || (
                        <span className="text-gray-600 italic">
                          (geen vraag opgeslagen)
                        </span>
                      )}
                    </p>
                    <p className="mt-0.5 flex items-center gap-2 text-xs text-gray-600">
                      <span>{formatRelative(c.started_at)}</span>
                      <span>·</span>
                      <span>
                        {c.message_count === 1
                          ? '1 bericht'
                          : `${c.message_count} berichten`}
                      </span>
                      {c.language_detected && (
                        <>
                          <span>·</span>
                          <span className="uppercase">
                            {c.language_detected}
                          </span>
                        </>
                      )}
                    </p>
                  </div>
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Drawer */}
      {openConvId !== null && (
        <ConversationDrawer
          widgetId={widgetId}
          convId={openConvId}
          onClose={() => setOpenConvId(null)}
        />
      )}
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

/**
 * REQ-3 (SPEC-CHAT-QUALITY-LOOP-001): the nightly judge's verdict, read from
 * its own sidecar endpoint so the transcript types stay untouched. Local
 * shape on purpose — duplicated in the platform drawer, no shared module.
 */
interface ConversationQuality {
  outcome: string
  failure_category: string | null
  reasoning: string | null
  confidence: string | null
  suggested_action: string | null
  judged_at: string | null
}

/** Outcome → existing Badge semantic variant; no ad-hoc colors. */
const OUTCOME_BADGE_VARIANT: Record<string, 'success' | 'warning' | 'secondary'> = {
  resolved: 'success',
  partially_resolved: 'success',
  escalated: 'warning',
  unresolved: 'secondary',
  abandoned_early: 'secondary',
  out_of_scope: 'secondary',
}

/** Judge verdict panel for a conversation drawer. Renders only when a
 * judgment exists — a 404 ("not judged yet") is normal and shows nothing. */
function QualityPanel({ quality }: { quality: ConversationQuality }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3">
      <Badge variant={OUTCOME_BADGE_VARIANT[quality.outcome] ?? 'secondary'}>
        {quality.outcome}
      </Badge>
      {quality.reasoning && (
        <p className="mt-2 text-xs leading-5 text-gray-600">{quality.reasoning}</p>
      )}
      {quality.suggested_action && (
        <p className="mt-1.5 text-xs leading-5 text-gray-700">{quality.suggested_action}</p>
      )}
    </div>
  )
}

function ConversationDrawer({
  widgetId,
  convId,
  onClose,
}: {
  widgetId: string
  convId: number
  onClose: () => void
}) {
  const query = useWidgetConversation(widgetId, convId)
  // 404 = not judged yet (normal state): retry off, error never surfaced.
  const qualityQuery = useQuery({
    queryKey: ['widget-conversation-quality', widgetId, convId],
    queryFn: () =>
      apiFetch<ConversationQuality>(
        `/api/admin/widgets/${widgetId}/conversations/${convId}/quality`,
      ),
    enabled: !!convId,
    retry: false,
  })
  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex justify-end"
    >
      <div
        className="absolute inset-0 bg-black/40"
        onClick={onClose}
        aria-hidden
      />
      <div className="relative h-full w-full max-w-lg bg-white overflow-y-auto">
        <div className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-gray-200 bg-white px-5 py-3.5">
          <div className="min-w-0">
            <p className="text-sm font-medium text-gray-900 truncate">
              Gesprek #{convId}
            </p>
            {query.data && (
              <p className="text-xs text-gray-600">
                {formatRelative(query.data.started_at)} ·{' '}
                {query.data.message_count === 1
                  ? '1 bericht'
                  : `${query.data.message_count} berichten`}
              </p>
            )}
          </div>
          <Button
            type="button"
            onClick={onClose}
            variant="outline"
            size="icon"
            className="h-8 w-8 text-gray-500"
            aria-label="Sluiten"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>

        <div className="px-5 py-4 space-y-3">
          {qualityQuery.data && <QualityPanel quality={qualityQuery.data} />}
          {query.isLoading && (
            <p className="text-sm text-gray-600">
              <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
              Laden…
            </p>
          )}
          {query.error && (
            <p className="text-sm text-[var(--color-destructive)]">
              Kon gesprek niet laden.
            </p>
          )}
          {query.data?.messages.map((msg) => (
            <div
              key={msg.id}
              className={
                msg.role === 'user'
                  ? 'ml-auto max-w-[85%] rounded-2xl rounded-br-md bg-gray-900 px-4 py-2.5 text-sm text-white whitespace-pre-wrap'
                  : 'mr-auto max-w-[85%] rounded-2xl rounded-bl-md bg-[var(--color-rl-cream)] px-4 py-2.5 text-sm text-gray-900 whitespace-pre-wrap'
              }
            >
              {msg.content}
              {msg.role === 'assistant' && msg.sources && msg.sources.length > 0 && (
                <ul className="mt-2 flex flex-wrap gap-1.5">
                  {msg.sources.map((s) => (
                    <li key={`${msg.id}-${s.label}`}>
                      {/* REQ-9: only http/https schemes render as anchors */}
                      {_isSafeHttpUrl(s.url) ? (
                        <a
                          href={s.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          title={s.title}
                          className="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-white px-2 py-0.5 text-[0.6875rem] text-gray-700 klai-hover"
                        >
                          <span className="font-medium">({s.label})</span>
                          <span className="truncate max-w-[12rem]">{s.title}</span>
                        </a>
                      ) : (
                        <span
                          title={s.title}
                          className="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-white px-2 py-0.5 text-[0.6875rem] text-gray-700"
                        >
                          <span className="font-medium">({s.label})</span>
                          <span className="truncate max-w-[12rem]">{s.title}</span>
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
              {msg.role === 'assistant' && msg.rating && (
                msg.rating === 'thumbsUp' ? (
                  <ThumbsUp
                    role="img"
                    aria-label="Door klant beoordeeld met duim omhoog"
                    className="mt-2 h-3.5 w-3.5 text-[var(--color-success-text)]"
                  />
                ) : (
                  <ThumbsDown
                    role="img"
                    aria-label="Door klant beoordeeld met duim omlaag"
                    className="mt-2 h-3.5 w-3.5 text-[var(--color-destructive)]"
                  />
                )
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function formatRelative(iso: string): string {
  const then = new Date(iso).getTime()
  const diffMs = Date.now() - then
  const min = Math.round(diffMs / 60000)
  if (min < 1) return 'zojuist'
  if (min < 60) return `${min} min geleden`
  const hr = Math.round(min / 60)
  if (hr < 24) return `${hr} uur geleden`
  const day = Math.round(hr / 24)
  if (day < 7) return `${day} dag${day === 1 ? '' : 'en'} geleden`
  return new Date(iso).toLocaleDateString('nl-NL', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}
