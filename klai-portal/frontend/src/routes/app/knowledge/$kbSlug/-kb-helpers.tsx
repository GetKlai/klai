// Shared helpers for knowledge base detail routes

import { Badge } from '@/components/ui/badge'
import { Tooltip } from '@/components/ui/tooltip'
import { type MultiSelectOption } from '@/components/ui/multi-select'
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

export function roleBadge(role: string) {
  const labels: Record<string, () => string> = {
    viewer: m.knowledge_members_role_viewer,
    contributor: m.knowledge_members_role_contributor,
    owner: m.knowledge_members_role_owner,
  }
  return <Badge variant="secondary">{(labels[role] ?? (() => role))()}</Badge>
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
  // Crawler has two phases — discovery (pages_total = NULL) and processing
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
      // Phase 2 (processing) — pages_total known and > 0. Crawler has
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
      // Phase 1 (discovery) — knowledge.crawl_jobs.pages_total defaults to 0
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
          <Badge variant="success">{m.admin_connectors_status_completed()} · {formatRelativeTime(lastSyncAt)}</Badge>
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
        <Icon className="h-4 w-4 text-[var(--color-foreground)]" />
        <h2 className="text-sm font-semibold text-[var(--color-foreground)]">{title}</h2>
      </div>
      {children}
    </div>
  )
}

export const ASSERTION_MODE_OPTIONS: MultiSelectOption[] = [
  { value: 'factual',    label: 'Fact',        description: 'Established fact, documentation, specs' },
  { value: 'procedural', label: 'Procedure',   description: "Step-by-step instructions, how-to's" },
  { value: 'belief',     label: 'Claim',       description: 'Not conclusively proven claim' },
  { value: 'quoted',     label: 'Quote',       description: 'Literal source material' },
  { value: 'hypothesis', label: 'Speculation', description: 'Hypotheses, brainstorm' },
  { value: 'unknown',    label: 'Unknown',     description: 'Type not specified' },
]

/**
 * SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-1 — shared cookie parser.
 * Accepts a raw cookie string (either JSON array or header string format)
 * and a base URL for domain extraction.
 * Used by both add-connector and edit-connector wizard flows.
 */
export function parseCookieString(raw: string, baseUrl: string): unknown[] | undefined {
  const trimmed = raw.trim()
  if (!trimmed) return undefined
  // JSON array format: [{"name": "...", "value": "..."}]
  if (trimmed.startsWith('[')) {
    try {
      const parsed = JSON.parse(trimmed)
      return Array.isArray(parsed) ? parsed : undefined
    } catch {
      return undefined
    }
  }
  // Raw cookie header format: name1=value1; name2=value2
  const domain = (() => {
    try { return new URL(baseUrl).hostname } catch { return '' }
  })()
  return trimmed.split(';').map((pair) => {
    const [cookieName, ...rest] = pair.trim().split('=')
    return { name: cookieName.trim(), value: rest.join('='), domain, path: '/' }
  }).filter((c) => c.name && c.value)
}
