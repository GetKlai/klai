import * as React from 'react'
import { Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'

export type StatCardTone = 'default' | 'warning' | 'destructive'

const toneTextClass: Record<StatCardTone, string> = {
  default: 'text-gray-900',
  warning: 'text-[var(--color-warning)]',
  destructive: 'text-[var(--color-destructive)]',
}

export interface StatCardProps {
  /** Small uppercase caption above the value. */
  label: string
  /** The primary value (number, formatted string, or node). */
  value: React.ReactNode
  /** Optional muted sub-line below the value. */
  sub?: string
  /** `default` = dashboard tile (text-3xl); `sm` = compact inline stat (text-xl). */
  size?: 'default' | 'sm'
  /** Color the value for a warning/destructive metric. */
  tone?: StatCardTone
  /** Highlight the whole card as needing attention (warning frame + tint). */
  alert?: boolean
  /** Show a spinner in place of the value while data loads. */
  loading?: boolean
  /** When provided the card becomes a button (hover + pointer). */
  onClick?: () => void
  className?: string
}

/**
 * A metric tile: uppercase label + large tabular value + optional sub-line.
 * The single source for the portal's stat cards (was the platform-only
 * `PlatformStatCard` / `PlatformMiniStat`). Use `size="sm"` for compact inline
 * stats. Clickable cards that navigate to a matching tab pass `onClick`.
 */
function StatCard({
  label,
  value,
  sub,
  size = 'default',
  tone = 'default',
  alert = false,
  loading = false,
  onClick,
  className,
}: StatCardProps) {
  const isSm = size === 'sm'
  const frameClass = cn(
    'rounded-xl border bg-white text-left transition-colors',
    isSm ? 'px-4 py-3' : 'px-4 py-4',
    alert ? 'border-[var(--color-warning)] bg-[var(--color-warning-bg)]' : 'border-gray-200',
    onClick && 'klai-hover cursor-pointer',
    className,
  )
  const body = (
    <>
      <p className="text-[11px] font-semibold uppercase tracking-[0.06em] text-gray-400">
        {label}
      </p>
      <p
        className={cn(
          'mt-1 font-display-bold tabular-nums',
          isSm ? 'text-xl' : 'text-3xl',
          toneTextClass[tone],
        )}
      >
        {loading ? (
          <Loader2 className="inline h-5 w-5 animate-spin text-gray-400" />
        ) : value === undefined || value === null ? (
          '-'
        ) : (
          value
        )}
      </p>
      {sub && <p className="mt-1 text-xs text-gray-400">{sub}</p>}
    </>
  )
  if (onClick) {
    return (
      <button type="button" className={frameClass} onClick={onClick}>
        {body}
      </button>
    )
  }
  return <div className={frameClass}>{body}</div>
}

export { StatCard }
