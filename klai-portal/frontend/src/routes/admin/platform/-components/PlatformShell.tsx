import { Loader2 } from 'lucide-react'
import * as m from '@/paraglide/messages'
import type { ReactNode } from 'react'

export function PlatformStatCard({
  label,
  value,
  sub,
  loading,
  alert,
  onClick,
}: {
  label: string
  value: number | string | undefined
  sub?: string
  loading: boolean
  alert?: boolean
  onClick?: () => void
}) {
  const className = [
    'rounded-xl border bg-white px-4 py-4 text-left transition-colors',
    alert
      ? 'border-[var(--color-warning)] bg-[var(--color-warning-bg)]'
      : 'border-gray-200',
    onClick ? 'klai-hover cursor-pointer' : '',
  ].join(' ')
  const content = (
    <>
      <p className="text-[11px] font-semibold uppercase tracking-[0.06em] text-gray-400">
        {label}
      </p>
      <p className="mt-1 text-3xl font-display-bold text-gray-900 tabular-nums">
        {loading ? (
          <Loader2 className="inline h-5 w-5 animate-spin text-gray-400" />
        ) : value === undefined ? (
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
      <button type="button" className={className} onClick={onClick}>
        {content}
      </button>
    )
  }
  return (
    <div className={className}>
      {content}
    </div>
  )
}

export function PlatformTableShell({
  loading,
  empty,
  emptyText,
  children,
}: {
  loading: boolean
  empty: boolean
  emptyText: string
  children: ReactNode
}) {
  if (loading) {
    return (
      <p className="py-8 text-sm text-gray-400">
        <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
        {m.admin_shared_loading()}
      </p>
    )
  }
  if (empty) {
    return <p className="py-8 text-sm text-gray-400">{emptyText}</p>
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm border-t border-b border-gray-200">
        {children}
      </table>
    </div>
  )
}

export function PlatformMiniStat({
  label,
  value,
}: {
  label: string
  value: number | string
}) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white px-4 py-3">
      <p className="text-[11px] font-semibold uppercase tracking-[0.06em] text-gray-400">
        {label}
      </p>
      <p className="mt-1 text-xl font-display-bold text-gray-900 tabular-nums">
        {value}
      </p>
    </div>
  )
}
