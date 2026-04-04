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

export function SyncStatusBadge({ status, lastSyncAt }: { status: string | null; lastSyncAt?: string | null }) {
  switch (status?.toUpperCase()) {
    case 'RUNNING': return <Badge variant="accent">{m.admin_connectors_status_running()}</Badge>
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
        <Icon className="h-4 w-4 text-[var(--color-purple-deep)]" />
        <h2 className="text-sm font-semibold text-[var(--color-purple-deep)]">{title}</h2>
      </div>
      {children}
    </div>
  )
}

export const ASSERTION_MODE_OPTIONS: MultiSelectOption[] = [
  { value: 'fact',        label: 'Fact',        description: 'Established fact, documentation, specs' },
  { value: 'procedural',  label: 'Procedure',   description: "Step-by-step instructions, how-to's" },
  { value: 'claim',       label: 'Claim',       description: 'Not conclusively proven claim' },
  { value: 'quoted',      label: 'Quote',       description: 'Literal source material' },
  { value: 'speculation', label: 'Speculation', description: 'Hypotheses, brainstorm' },
  { value: 'unknown',     label: 'Unknown',     description: 'Type not specified' },
]
