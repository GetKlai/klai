// Shared helpers for knowledge base detail routes (KB tabs).
// Connector-wizard-only helpers live in
// `klai-portal/frontend/src/routes/app/knowledge/-connector-constants.ts`.

import { Badge } from '@/components/ui/badge'
import { Tooltip } from '@/components/ui/tooltip'
import * as m from '@/paraglide/messages'

function formatRelativeTime(dateStr: string): string {
  const diffMs = new Date(dateStr).getTime() - Date.now()
  const diffMin = Math.round(diffMs / 60_000)
  const diffHour = Math.round(diffMs / 3_600_000)
  const diffDay = Math.round(diffMs / 86_400_000)
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
  if (Math.abs(diffMin) < 60) return rtf.format(diffMin, 'minute')
  if (Math.abs(diffHour) < 24) return rtf.format(diffHour, 'hour')
  return rtf.format(diffDay, 'day')
}

export function SyncStatusBadge({
  status,
  lastSyncAt,
  pagesDone = null,
  pagesTotal = null,
  liveResolutionFailed = false,
}: {
  status: string | null
  lastSyncAt?: string | null
  // SPEC-CRAWLER-006 REQ-08: live progress for delegated web_crawler runs.
  // Crawler has two phases - discovery (pages_total = NULL) and processing
  // (pages_total = N). Other connector types pass null/null and render
  // the plain "Bezig" label.
  pagesDone?: number | null
  pagesTotal?: number | null
  liveResolutionFailed?: boolean
}) {
  switch (status?.toUpperCase()) {
    case 'RUNNING': {
      if (liveResolutionFailed) {
        return <Badge variant="accent">{m.admin_connectors_status_running_unknown()}</Badge>
      }
      // Phase 2 (processing) - pages_total known and > 0. Crawler has
      // finished discovery and is iterating per-page ingest.
      if (pagesTotal && pagesTotal > 0 && pagesDone !== null && pagesDone !== undefined) {
        return (
          <Badge variant="accent">
            {m.admin_connectors_status_running_processing({
              done: String(pagesDone),
              total: String(pagesTotal),
            })}
          </Badge>
        )
      }
      // Phase 1 (discovery) - knowledge.crawl_jobs.pages_total defaults to 0
      // and is updated to len(results) only AFTER crawl_site() returns.
      // While the row carries pages_total = 0 and pages_done = 0 we know
      // the crawl is still gathering URLs, not iterating per-page.
      // Treat the live live-progress payload as "phase 1" iff at least one
      // of pages_done / pages_total has been observed (so we don't
      // misrender a non-crawler RUNNING run that has no live payload).
      if (
        (pagesDone !== null && pagesDone !== undefined)
        || (pagesTotal !== null && pagesTotal !== undefined)
      ) {
        return <Badge variant="accent">{m.admin_connectors_status_running_collecting()}</Badge>
      }
      return <Badge variant="accent">{m.admin_connectors_status_running()}</Badge>
    }
    case 'COMPLETED': {
      if (!lastSyncAt) return <Badge variant="success">{m.admin_connectors_status_completed()}</Badge>
      const exact = new Date(lastSyncAt).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
      return (
        <Tooltip label={exact}>
          <div className="inline-flex flex-col items-start gap-1">
            <Badge variant="success" className="whitespace-nowrap">{m.admin_connectors_status_completed()}</Badge>
            <span className="text-[11px] text-gray-400 whitespace-nowrap">{formatRelativeTime(lastSyncAt)}</span>
          </div>
        </Tooltip>
      )
    }
    case 'FAILED': return <Badge variant="destructive">{m.admin_connectors_status_failed()}</Badge>
    case 'AUTH_ERROR': return <Badge variant="destructive">{m.admin_connectors_status_auth_error()}</Badge>
    case 'PENDING': return <Badge variant="accent">{m.admin_connectors_status_running()}</Badge>
    default: return <Badge variant="secondary">{m.admin_connectors_status_never()}</Badge>
  }
}

export function DashboardSection({
  icon: Icon,
  title,
  children,
}: {
  icon: React.ElementType
  title: string
  children: React.ReactNode
}) {
  return (
    <div>
      <div className="flex items-center gap-2 mb-3">
        <Icon className="h-4 w-4 text-gray-900" />
        <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
      </div>
      {children}
    </div>
  )
}

// `ASSERTION_MODE_OPTIONS` and `joinSeedUrl` were moved to
// `klai-portal/frontend/src/routes/app/knowledge/-connector-constants.ts`
// - wizard-only, smallest-shared scope is the parent route dir.
//
// `roleBadge` was removed (dead code - no consumers).
//
// `parseCookieString` was removed earlier: the wizard now collects cookies
// as structured {name, value} rows via CookieRowsInput, matching the shape
// the backend persists and the cron-sync consumes. No parser layer means
// no chance of cookie-name guessing. See components/knowledge/CookieRowsInput.tsx.
