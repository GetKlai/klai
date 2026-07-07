import { useMemo, useState, type ReactNode } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { ArrowDownUp } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
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
import * as m from '@/paraglide/messages'
import { datetime } from '@/paraglide/registry'
import { getLocale } from '@/paraglide/runtime'
import { usePlatformUsageOverview, usePlatformUsageTenants } from '../../-hooks'
import type { PlatformUsageRange, PlatformUsageTenantRow } from '../../-types'

type SortKey =
  | 'knowledge_queries'
  | 'active_users'
  | 'api_requests'
  | 'successful_requests'
  | 'failed_requests'
  | 'total_tokens'
  | 'spend_usd'
  | 'last_activity_at'

const RANGE_OPTIONS: PlatformUsageRange[] = ['7d', '30d', '90d']

const numberFmt = new Intl.NumberFormat(undefined)
const compactFmt = new Intl.NumberFormat(undefined, {
  notation: 'compact',
  maximumFractionDigits: 1,
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

function rangeLabel(range: PlatformUsageRange) {
  if (range === '7d') return m.platform_usage_range_7d()
  if (range === '90d') return m.platform_usage_range_90d()
  return m.platform_usage_range_30d()
}

function fmtWindowDate(iso: string, exclusiveEnd = false) {
  const date = new Date(iso)
  if (exclusiveEnd) {
    date.setUTCDate(date.getUTCDate() - 1)
  }
  return datetime(getLocale(), date.toISOString(), {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

function sortValue(row: PlatformUsageTenantRow, key: SortKey): number {
  if (key === 'last_activity_at') {
    return row.last_activity_at ? new Date(row.last_activity_at).getTime() : 0
  }
  return row[key] ?? -1
}

export function StatsTab({
  search,
  range,
  onRangeChange,
  fmtDate,
}: {
  search: string
  range: PlatformUsageRange
  onRangeChange: (range: PlatformUsageRange) => void
  fmtDate: (s: string | null) => string
}) {
  const navigate = useNavigate()
  const overview = usePlatformUsageOverview(range)
  const tenants = usePlatformUsageTenants(range)
  const [sortKey, setSortKey] = useState<SortKey>('knowledge_queries')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase()
    const filtered = (tenants.data ?? []).filter((row) =>
      q ? `${row.name} ${row.slug}`.toLowerCase().includes(q) : true,
    )
    return [...filtered].sort((a, b) => {
      const delta = sortValue(a, sortKey) - sortValue(b, sortKey)
      return sortDir === 'asc' ? delta : -delta
    })
  }, [search, sortDir, sortKey, tenants.data])

  function setSort(next: SortKey) {
    if (next === sortKey) {
      setSortDir((current) => (current === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(next)
      setSortDir('desc')
    }
  }

  const data = overview.data
  const litellmSub = data?.litellm_available
    ? undefined
    : data?.litellm_configured
      ? m.platform_usage_litellm_error()
      : m.platform_usage_litellm_unconfigured()

  return (
    <section className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="inline-flex rounded-lg border border-gray-200 bg-white p-1">
          {RANGE_OPTIONS.map((option) => (
            <Button
              key={option}
              type="button"
              size="sm"
              variant={option === range ? 'default' : 'ghost'}
              onClick={() => onRangeChange(option)}
            >
              {rangeLabel(option)}
            </Button>
          ))}
        </div>
        {data && (
          <p className="text-xs text-gray-400">
            {m.platform_usage_window({
              start: fmtWindowDate(data.start),
              end: fmtWindowDate(data.end, true),
            })}
          </p>
        )}
      </div>

      {overview.error ? (
        <QueryErrorState
          error={overview.error instanceof Error ? overview.error : new Error(String(overview.error))}
          onRetry={() => void overview.refetch()}
        />
      ) : (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <StatCard label={m.platform_usage_total_events()} value={data?.total_events} loading={overview.isLoading} />
          <StatCard label={m.platform_usage_knowledge_queries()} value={data?.knowledge_queries} loading={overview.isLoading} />
          <StatCard label={m.platform_usage_active_users()} value={data?.active_users} loading={overview.isLoading} />
          <StatCard label={m.platform_usage_active_tenants()} value={data?.active_tenants} loading={overview.isLoading} />
          <StatCard label={m.platform_usage_api_requests()} value={fmtNumber(data?.api_requests)} sub={litellmSub} loading={overview.isLoading} />
          <StatCard label={m.platform_usage_successful_requests()} value={fmtNumber(data?.successful_requests)} sub={litellmSub} loading={overview.isLoading} />
          <StatCard label={m.platform_usage_failed_requests()} value={fmtNumber(data?.failed_requests)} sub={litellmSub} tone={data?.failed_requests ? 'destructive' : 'default'} loading={overview.isLoading} />
          <StatCard label={m.platform_usage_tokens()} value={fmtCompact(data?.total_tokens)} sub={litellmSub} loading={overview.isLoading} />
          <StatCard label={m.platform_usage_spend()} value={fmtSpend(data?.spend_usd)} sub={litellmSub ?? m.platform_usage_spend_estimated()} loading={overview.isLoading} />
        </div>
      )}

      {tenants.error ? (
        <QueryErrorState
          error={tenants.error instanceof Error ? tenants.error : new Error(String(tenants.error))}
          onRetry={() => void tenants.refetch()}
        />
      ) : tenants.isLoading ? (
        <ListLoadingState label={m.admin_shared_loading()} />
      ) : rows.length === 0 ? (
        <ListEmptyState title={m.platform_usage_empty_tenants()} />
      ) : (
        <DataTable>
          <DataTableHeader>
            <DataTableRow>
              <DataTableHead>{m.platform_col_organization()}</DataTableHead>
              <SortableHead onClick={() => setSort('knowledge_queries')}>{m.platform_usage_knowledge_queries()}</SortableHead>
              <SortableHead onClick={() => setSort('active_users')}>{m.platform_usage_active_users()}</SortableHead>
              <SortableHead onClick={() => setSort('api_requests')}>{m.platform_usage_api_requests()}</SortableHead>
              <SortableHead onClick={() => setSort('successful_requests')}>{m.platform_usage_successful_requests()}</SortableHead>
              <SortableHead onClick={() => setSort('failed_requests')}>{m.platform_usage_failed_requests()}</SortableHead>
              <SortableHead onClick={() => setSort('total_tokens')}>{m.platform_usage_tokens()}</SortableHead>
              <SortableHead onClick={() => setSort('spend_usd')}>{m.platform_usage_spend()}</SortableHead>
              <SortableHead onClick={() => setSort('last_activity_at')}>{m.platform_usage_last_activity()}</SortableHead>
            </DataTableRow>
          </DataTableHeader>
          <DataTableBody>
            {rows.map((row) => (
              <DataTableRow
                key={row.org_id}
                interactive
                onClick={() =>
                  void navigate({
                    to: '/admin/platform/orgs/$orgId',
                    params: { orgId: String(row.org_id) },
                    search: { tab: 'usage', range },
                  })
                }
              >
                <DataTableCell>
                  <span className="font-medium">{row.name}</span>
                  <p className="font-mono text-xs text-gray-400">{row.slug}</p>
                  <div className="mt-1 flex gap-1">
                    <Badge variant="outline">{row.plan}</Badge>
                    <Badge variant="outline">{row.billing_status}</Badge>
                  </div>
                </DataTableCell>
                <DataTableCell className="tabular-nums">{fmtNumber(row.knowledge_queries)}</DataTableCell>
                <DataTableCell className="tabular-nums">{fmtNumber(row.active_users)}</DataTableCell>
                <DataTableCell className="tabular-nums">{fmtNumber(row.api_requests)}</DataTableCell>
                <DataTableCell className="tabular-nums">{fmtNumber(row.successful_requests)}</DataTableCell>
                <DataTableCell className="tabular-nums text-[var(--color-destructive)]">{fmtNumber(row.failed_requests)}</DataTableCell>
                <DataTableCell className="tabular-nums">{fmtCompact(row.total_tokens)}</DataTableCell>
                <DataTableCell className="tabular-nums">{fmtSpend(row.spend_usd)}</DataTableCell>
                <DataTableCell className="whitespace-nowrap tabular-nums text-gray-400">
                  {fmtDate(row.last_activity_at)}
                </DataTableCell>
              </DataTableRow>
            ))}
          </DataTableBody>
        </DataTable>
      )}
    </section>
  )
}

function SortableHead({
  children,
  onClick,
}: {
  children: ReactNode
  onClick: () => void
}) {
  return (
    <DataTableHead>
      <button type="button" className="inline-flex items-center gap-1 hover:text-gray-900" onClick={onClick}>
        {children}
        <ArrowDownUp className="h-3 w-3" />
      </button>
    </DataTableHead>
  )
}
