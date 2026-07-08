import { useMemo, useState } from 'react'
import { BarChart3 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  DataTable,
  DataTableBody,
  DataTableCell,
  DataTableHead,
  DataTableHeader,
  DataTableRow,
} from '@/components/ui/data-table'
import { ListEmptyState, ListLoadingState } from '@/components/ui/list-state'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { StatCard } from '@/components/ui/stat-card'
import { Tooltip } from '@/components/ui/tooltip'
import * as m from '@/paraglide/messages'
import { usePlatformUsageTenantDetail } from '../../-hooks'
import type { DailyUsagePoint, PlatformUsageRange } from '../../-types'

type UsageGranularity = 'daily' | 'weekly'

interface ChartUsagePoint {
  key: string
  label: string
  tooltipLabel: string
  events: number
  knowledge_queries: number
  failed_requests: number
}

const numberFmt = new Intl.NumberFormat(undefined)
const compactFmt = new Intl.NumberFormat(undefined, {
  notation: 'compact',
  maximumFractionDigits: 1,
})
const dayMonthFmt = new Intl.DateTimeFormat(undefined, {
  day: 'numeric',
  month: 'short',
})
const usdFmt = new Intl.NumberFormat(undefined, {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 2,
})

function fmtNumber(value: number | null | undefined) {
  return value === null || value === undefined ? '-' : numberFmt.format(value)
}

function fmtCompact(value: number | null | undefined) {
  return value === null || value === undefined ? '-' : compactFmt.format(value)
}

function fmtSpend(value: number | null | undefined) {
  return value === null || value === undefined ? '-' : usdFmt.format(value)
}

function parseUsageDate(value: string) {
  return new Date(`${value}T00:00:00Z`)
}

function formatShortDate(value: string) {
  return dayMonthFmt.format(parseUsageDate(value))
}

function formatDateRange(start: string, end: string) {
  return start === end
    ? formatShortDate(start)
    : `${formatShortDate(start)} - ${formatShortDate(end)}`
}

function weekStartKey(value: string) {
  const date = parseUsageDate(value)
  const day = date.getUTCDay()
  const daysSinceMonday = day === 0 ? 6 : day - 1
  date.setUTCDate(date.getUTCDate() - daysSinceMonday)
  return date.toISOString().slice(0, 10)
}

function chartTooltipLabel(point: ChartUsagePoint) {
  return [
    point.tooltipLabel,
    `${m.platform_usage_events_legend()} ${numberFmt.format(point.events)}`,
    `${m.platform_usage_knowledge_legend()} ${numberFmt.format(point.knowledge_queries)}`,
    `${m.platform_usage_failed_legend()} ${numberFmt.format(point.failed_requests)}`,
  ].join(' · ')
}

function toDailyChartPoints(points: DailyUsagePoint[]): ChartUsagePoint[] {
  return points.map((point) => ({
    key: point.date,
    label: formatShortDate(point.date),
    tooltipLabel: formatShortDate(point.date),
    events: point.events,
    knowledge_queries: point.knowledge_queries,
    failed_requests: point.failed_requests ?? 0,
  }))
}

function toWeeklyChartPoints(points: DailyUsagePoint[]): ChartUsagePoint[] {
  const buckets = new Map<
    string,
    {
      start: string
      end: string
      events: number
      knowledge_queries: number
      failed_requests: number
    }
  >()

  for (const point of points) {
    const key = weekStartKey(point.date)
    const bucket = buckets.get(key)
    if (bucket) {
      bucket.end = point.date
      bucket.events += point.events
      bucket.knowledge_queries += point.knowledge_queries
      bucket.failed_requests += point.failed_requests ?? 0
    } else {
      buckets.set(key, {
        start: point.date,
        end: point.date,
        events: point.events,
        knowledge_queries: point.knowledge_queries,
        failed_requests: point.failed_requests ?? 0,
      })
    }
  }

  return Array.from(buckets, ([key, bucket]) => {
    const label = formatDateRange(bucket.start, bucket.end)
    return {
      key,
      label,
      tooltipLabel: label,
      events: bucket.events,
      knowledge_queries: bucket.knowledge_queries,
      failed_requests: bucket.failed_requests,
    }
  })
}

export function UsageSection({
  orgId,
  range,
  fmtDate,
}: {
  orgId: string
  range: PlatformUsageRange
  fmtDate: (s: string | null) => string
}) {
  const [granularity, setGranularity] = useState<UsageGranularity>('daily')
  const query = usePlatformUsageTenantDetail(orgId, range)
  const data = query.data
  const totals = data?.daily.reduce(
    (acc, point) => ({
      events: acc.events + point.events,
      knowledge: acc.knowledge + point.knowledge_queries,
      requests: acc.requests + (point.api_requests ?? 0),
      failed: acc.failed + (point.failed_requests ?? 0),
      tokens: acc.tokens + (point.tokens ?? 0),
      spend: acc.spend + (point.spend_usd ?? 0),
    }),
    { events: 0, knowledge: 0, requests: 0, failed: 0, tokens: 0, spend: 0 },
  )

  if (query.isLoading) return <ListLoadingState label={m.admin_shared_loading()} />
  if (query.error) {
    return (
      <QueryErrorState
        error={query.error instanceof Error ? query.error : new Error(String(query.error))}
        onRetry={() => void query.refetch()}
      />
    )
  }
  if (!data || !totals) {
    return <ListEmptyState title={m.platform_usage_empty_detail()} />
  }

  const hasLitellmMetrics = data.litellm_available && data.litellm_mapped
  const litellmUnavailableLabel = !data.litellm_mapped
    ? m.platform_usage_litellm_unmapped()
    : data.litellm_configured
      ? m.platform_usage_litellm_error()
      : m.platform_usage_litellm_unconfigured()
  const litellmCardSub = hasLitellmMetrics ? undefined : litellmUnavailableLabel

  return (
    <section className="space-y-6">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-6">
        <StatCard size="sm" label={m.platform_usage_total_events()} value={totals.events} />
        <StatCard size="sm" label={m.platform_usage_knowledge_queries()} value={totals.knowledge} />
        <StatCard size="sm" label={m.platform_usage_active_users()} value={data.active_users} />
        <StatCard size="sm" label={m.platform_usage_api_requests()} value={fmtNumber(hasLitellmMetrics ? totals.requests : null)} sub={litellmCardSub} />
        <StatCard size="sm" label={m.platform_usage_failed_requests()} value={fmtNumber(hasLitellmMetrics ? totals.failed : null)} sub={litellmCardSub} tone={totals.failed ? 'destructive' : 'default'} />
        <StatCard size="sm" label={m.platform_usage_spend()} value={fmtSpend(hasLitellmMetrics ? totals.spend : null)} sub={litellmCardSub} />
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between gap-4">
          <h2 className="text-base font-display-bold text-gray-900">
            {granularity === 'daily'
              ? m.platform_usage_daily_trend()
              : m.platform_usage_weekly_trend()}
          </h2>
          <p className="text-xs text-gray-400">
            {m.platform_usage_last_activity()}: {fmtDate(data.last_activity_at)}
          </p>
        </div>
        <UsageBars points={data.daily} granularity={granularity} setGranularity={setGranularity} />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <BreakdownTable
          title={m.platform_usage_event_breakdown()}
          empty={m.platform_usage_empty_events()}
          headers={[m.platform_usage_event_type(), m.platform_usage_count()]}
          rows={data.event_type_breakdown.map((row) => [
            row.event_type,
            fmtNumber(row.count),
          ])}
        />
        <BreakdownTable
          title={m.platform_usage_model_breakdown()}
          empty={
            hasLitellmMetrics
              ? m.platform_usage_empty_models()
              : litellmUnavailableLabel
          }
          headers={[
            m.platform_usage_model(),
            m.platform_usage_api_requests(),
            m.platform_usage_tokens(),
            m.platform_usage_spend(),
          ]}
          rows={(data.model_breakdown ?? []).map((row) => [
            row.model,
            fmtNumber(row.api_requests),
            fmtCompact(row.tokens),
            fmtSpend(row.spend_usd),
          ])}
        />
      </div>
    </section>
  )
}

function UsageBars({
  points,
  granularity,
  setGranularity,
}: {
  points: DailyUsagePoint[]
  granularity: UsageGranularity
  setGranularity: (value: UsageGranularity) => void
}) {
  const chartPoints = useMemo(
    () => (granularity === 'daily' ? toDailyChartPoints(points) : toWeeklyChartPoints(points)),
    [granularity, points],
  )
  const max = Math.max(
    ...chartPoints.map((point) => Math.max(point.events, point.knowledge_queries, point.failed_requests)),
    1,
  )
  const barHeight = (value: number) => (value === 0 ? '0%' : `${Math.max(4, (value / max) * 100)}%`)
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-3 text-xs text-gray-500">
          <LegendSwatch className="bg-gray-300" label={m.platform_usage_events_legend()} />
          <LegendSwatch className="bg-[var(--color-rl-accent)]" label={m.platform_usage_knowledge_legend()} />
          <LegendSwatch className="bg-[var(--color-destructive)]" label={m.platform_usage_failed_legend()} />
        </div>
        <div className="inline-flex rounded-full border border-gray-200 bg-gray-50 p-0.5">
          <Button
            type="button"
            size="sm"
            variant={granularity === 'daily' ? 'default' : 'ghost'}
            className="h-7 px-3 text-xs"
            onClick={() => setGranularity('daily')}
          >
            {m.platform_usage_granularity_daily()}
          </Button>
          <Button
            type="button"
            size="sm"
            variant={granularity === 'weekly' ? 'default' : 'ghost'}
            className="h-7 px-3 text-xs"
            onClick={() => setGranularity('weekly')}
          >
            {m.platform_usage_granularity_weekly()}
          </Button>
        </div>
      </div>
      <div className="flex h-40 items-end gap-1">
        {chartPoints.map((point) => (
          <Tooltip key={point.key} label={chartTooltipLabel(point)} className="flex min-w-0 flex-1">
            <div className="flex min-w-0 flex-1 cursor-default flex-col items-center gap-2" aria-label={chartTooltipLabel(point)}>
              <div className="flex h-32 w-full items-end justify-center gap-0.5">
                <div
                  className="w-full rounded-t bg-gray-300"
                  style={{ height: barHeight(point.events) }}
                />
                <div
                  className="w-full rounded-t bg-[var(--color-rl-accent)]"
                  style={{ height: barHeight(point.knowledge_queries) }}
                />
                <div
                  className="w-full rounded-t bg-[var(--color-destructive)]"
                  style={{ height: barHeight(point.failed_requests) }}
                />
              </div>
              <span className="max-w-full truncate text-[10px] text-gray-400">
                {point.label}
              </span>
            </div>
          </Tooltip>
        ))}
      </div>
    </div>
  )
}

function LegendSwatch({ className, label }: { className: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span className={`h-2 w-2 rounded-sm ${className}`} />
      {label}
    </span>
  )
}

function BreakdownTable({
  title,
  empty,
  headers,
  rows,
}: {
  title: string
  empty: string
  headers: string[]
  rows: string[][]
}) {
  return (
    <div className="space-y-3">
      <h2 className="text-base font-display-bold text-gray-900">{title}</h2>
      {rows.length === 0 ? (
        <ListEmptyState icon={BarChart3} title={empty} className="rounded-xl border border-gray-200 bg-white" />
      ) : (
        <DataTable>
          <DataTableHeader>
            <DataTableRow>
              {headers.map((header) => (
                <DataTableHead key={header}>{header}</DataTableHead>
              ))}
            </DataTableRow>
          </DataTableHeader>
          <DataTableBody>
            {rows.map((row) => (
              <DataTableRow key={row.join('|')}>
                {row.map((cell, index) => (
                  <DataTableCell key={`${cell}-${index}`} className={index > 0 ? 'tabular-nums' : undefined}>
                    {cell}
                  </DataTableCell>
                ))}
              </DataTableRow>
            ))}
          </DataTableBody>
        </DataTable>
      )}
    </div>
  )
}
