import { useEffect, useState } from 'react'
import {
  Braces,
  File,
  FileText,
  Globe,
  Image,
  Type,
  Zap,
} from 'lucide-react'
import { SiAirtable, SiConfluence, SiGithub, SiGoogledrive, SiNotion } from '@icons-pack/react-simple-icons'
import { Badge } from '@/components/ui/badge'
import * as m from '@/paraglide/messages'
import type { Source } from './-sources-types'

/**
 * Threshold (minutes) after which a pending upload is considered stuck.
 * Below this we just show "Bezig sinds Xm" as informational; at/above it
 * the badge flips to a warning ("Hangt al Xm") so the user knows to retry.
 *
 * Connectors are excluded because long crawls can legitimately exceed it.
 * This is a frontend-only indicator, not enforcement: the backend reaper
 * (knowledge-ingest `stale_pending_artifact_reaper`) auto-fails artifacts
 * stuck in 'pending' for over 30 minutes, running every 15 minutes. This
 * threshold is intentionally shorter so the badge warns the user well
 * before the backend gives up.
 */
const STUCK_THRESHOLD_MINUTES = 10

export type SourceStatus = 'synced' | 'pending' | 'not_synced'

export function mapSourceStatus(source: Source): SourceStatus {
  const s = (source.status ?? '').toLowerCase()
  if (source.kind === 'upload') {
    const idx = (source.index_status ?? '').toLowerCase()
    if (idx === 'pending' || s === 'processing' || s === 'ingesting') return 'pending'
    if (idx === 'failed' || s === 'failed' || s.includes('error')) return 'not_synced'
    if (idx === 'synced') return 'synced'
    if (source.chunks_count === 0 && !idx) return 'not_synced'
    return 'synced'
  }
  if (s === 'running' || s === 'pending' || s === 'syncing') return 'pending'
  if (s.includes('error') || s.includes('failed') || s === 'auth_error' || s === 'orphan') {
    return 'not_synced'
  }
  if (s === 'success' || s === 'completed' || s === 'ok') return 'synced'
  if (source.items_count > 0 || source.chunks_count > 0) return 'synced'
  return 'not_synced'
}

/**
 * Returns whole minutes since `iso`, or null if `iso` is missing/unparsable.
 * Negative results (clock skew, future timestamp) clamp to 0.
 */
function elapsedMinutes(iso: string | null | undefined): number | null {
  if (!iso) return null
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return null
  const diff = Date.now() - t
  return Math.max(0, Math.floor(diff / 60_000))
}

export function shouldPollSource(source: Source): boolean {
  return mapSourceStatus(source) === 'pending'
}

/**
 * Poll interval (ms) for the sources list, or `false` to stop polling.
 *
 * Polling must never fully stop while a source is 'pending' — a sync that
 * outlives STUCK_THRESHOLD_MINUTES is still a real sync that will eventually
 * complete, and the UI needs to notice. What changes at the threshold is only
 * the cadence: fresh pending sources are polled quickly (4s) so a normal sync
 * feels responsive; sources past the "stuck" threshold are polled slowly
 * (30s) since a poll now is about eventually noticing completion, not
 * immediacy. Sources without a usable timestamp are treated as fresh, since
 * we can't tell how long they've been running.
 */
export function sourcesPollIntervalMs(sources: Source[]): number | false {
  const pending = sources.filter((s) => mapSourceStatus(s) === 'pending')
  if (pending.length === 0) return false
  const anyFresh = pending.some((s) => {
    const minutes = elapsedMinutes(s.last_sync_at ?? s.created_at)
    return minutes === null || minutes < STUCK_THRESHOLD_MINUTES
  })
  return anyFresh ? 4000 : 30_000
}

/**
 * Re-render hook that fires every 30s so elapsed-minute labels stay fresh
 * without the parent having to pass `now` down. One interval per badge
 * instance — acceptable at the scale of a sources list (~10-50 rows).
 */
function useTickEveryHalfMinute(): void {
  const [, setTick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), 30_000)
    return () => clearInterval(id)
  }, [])
}

export function StatusBadge({ source }: { source: Source }) {
  useTickEveryHalfMinute()
  const status = mapSourceStatus(source)
  const rawStatus = (source.status ?? '').toLowerCase()

  if (
    source.kind === 'connector'
    && (rawStatus.includes('error') || rawStatus.includes('failed') || rawStatus === 'auth_error' || rawStatus === 'orphan')
  ) {
    return (
      <Badge variant="destructive" title={source.status ?? undefined}>
        {m.kb_status_probleem()}
      </Badge>
    )
  }

  // Failed uploads (index_status === 'failed') previously fell through to the
  // neutral "Leeg" badge, so a permanently failed source looked merely
  // un-synced — the intermedia.com failure went unnoticed for 8 days. Show
  // the same destructive treatment connectors get, with a retry hint.
  if (source.kind === 'upload' && (source.index_status ?? '').toLowerCase() === 'failed') {
    return (
      <Badge variant="destructive" title={m.kb_status_failed_tooltip()}>
        {m.kb_status_probleem()}
      </Badge>
    )
  }

  // Only uploads have a backend reaper that turns stale pending work into a
  // failure. Website crawls can legitimately run longer than this threshold,
  // so elapsed time alone must not present them as stuck.
  if (status === 'pending') {
    const minutes = elapsedMinutes(source.last_sync_at ?? source.created_at)
    if (
      source.kind === 'upload'
      && minutes !== null
      && minutes >= STUCK_THRESHOLD_MINUTES
    ) {
      return (
        <Badge
          variant="destructive"
          title={m.kb_status_stuck_tooltip()}
        >
          {m.kb_status_stuck({ minutes: String(minutes) })}
        </Badge>
      )
    }
    if (minutes !== null && minutes >= 1) {
      return (
        <span className="inline-flex items-center gap-1.5">
          <Badge variant="secondary">{m.kb_status_bezig()}</Badge>
          <span className="text-xs text-gray-600">
            {m.kb_status_bezig_elapsed({ minutes: String(minutes) })}
          </span>
        </span>
      )
    }
    return <Badge variant="secondary">{m.kb_status_bezig()}</Badge>
  }

  const labelMap = {
    synced: m.kb_status_klaar(),
    pending: m.kb_status_bezig(),
    not_synced: m.kb_status_leeg(),
  } as const
  const variantMap = {
    synced: 'success' as const,
    pending: 'secondary' as const,
    not_synced: 'secondary' as const,
  }
  return <Badge variant={variantMap[status]}>{labelMap[status]}</Badge>
}

/**
 * Partial-failure indicator for connector rows. The connector as a whole
 * can be "Gesynct" while individual pages under it silently failed — this
 * surfaces that instead of letting the aggregate status hide it. Deliberately
 * NOT the destructive/red treatment: the connector itself is healthy, this
 * is a sub-count warning, not a broken-connector signal.
 */
export function FailedItemsWarning({ source }: { source: Source }) {
  if (source.kind !== 'connector') return null
  const failedCount = source.items_failed_count ?? 0
  if (failedCount <= 0) return null
  return (
    <span className="text-xs text-[var(--color-warning-text)]">
      {m.kb_connector_failed_items({ count: String(failedCount) })}
    </span>
  )
}

export function SourceIcon({ source }: { source: Source }) {
  if (source.kind === 'connector') {
    const t = source.connector_type ?? ''
    if (t === 'github') return <SiGithub className="h-4 w-4" />
    if (t === 'notion') return <SiNotion className="h-4 w-4" />
    if (t === 'google_drive') return <SiGoogledrive className="h-4 w-4" />
    if (t === 'airtable') return <SiAirtable className="h-4 w-4" />
    if (t === 'confluence') return <SiConfluence className="h-4 w-4" />
    if (t === 'json_feed') return <Braces className="h-4 w-4" />
    if (t === 'web_crawler') return <Globe className="h-4 w-4" />
    if (t === 'ms_docs') return <FileText className="h-4 w-4" />
    return <Zap className="h-4 w-4" />
  }
  const ct = (source.type_label ?? '').toLowerCase()
  const path = source.name.toLowerCase()
  if (path.endsWith('.pdf') || ct === 'pdf') return <FileText className="h-4 w-4" />
  if (
    path.startsWith('http')
    || source.source_url
    || ct === 'website'
    || ct === 'websitepagina'
    || ct === "website (pagina's)"
    || ct === 'link'
  ) {
    return <Globe className="h-4 w-4" />
  }
  if (ct.startsWith('afbeelding') || /\.(png|jpe?g|gif|webp|svg)$/i.test(path)) return <Image className="h-4 w-4" />
  if (ct === 'tekst') return <Type className="h-4 w-4" />
  return <File className="h-4 w-4" />
}

export function editablePageIdForSource(
  source: Source,
  slugToPageId: Map<string, string>,
): string | null {
  if (source.kind !== 'upload') return null
  const stripped = source.name.replace(/\.md$/i, '')
  return slugToPageId.get(stripped) ?? slugToPageId.get(source.name) ?? null
}
