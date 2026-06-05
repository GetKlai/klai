import * as React from 'react'
import { cn } from '@/lib/utils'

export type TabCountTone = 'success' | 'warning' | 'destructive' | 'info'

const tabCountToneClass: Record<TabCountTone, string> = {
  success: 'bg-[var(--color-success)]',
  warning: 'bg-[var(--color-warning)]',
  destructive: 'bg-[var(--color-destructive)]',
  info: 'bg-[var(--color-info)]',
}

export interface TabItem<T extends string = string> {
  id: T
  label: React.ReactNode
  /** Optional leading icon. Use sparingly — text-only is the default. */
  icon?: React.ElementType
  /** Optional trailing count badge. Only shown when > 0. */
  count?: number
  /** Tone of the count badge. Defaults to `success`. */
  countTone?: TabCountTone
}

interface TabsProps<T extends string> extends Omit<React.HTMLAttributes<HTMLDivElement>, 'onChange'> {
  tabs: TabItem<T>[]
  value: T
  onValueChange: (id: T) => void
}

/**
 * Underline tabs for authenticated app/admin surfaces — the single source for
 * the portal tab pattern (was hand-rolled in 6 places).
 *
 * Controlled and presentational: it owns no active state. Wire `value` /
 * `onValueChange` to local state or to URL search state when the tab must
 * survive navigation (see ui-standards.md "Tabs").
 *
 * The active tab gets a near-black underline (`border-gray-900`) so the
 * active state is unmistakable against the gray-200 container divider. Icons
 * and counts are optional; text-only is the default, on-brand look.
 */
function Tabs<T extends string>({ tabs, value, onValueChange, className, ...props }: TabsProps<T>) {
  return (
    <div role="tablist" className={cn('flex gap-6 border-b border-gray-200', className)} {...props}>
      {tabs.map((tab) => {
        const isActive = tab.id === value
        const Icon = tab.icon
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={isActive}
            onClick={() => onValueChange(tab.id)}
            className={cn(
              'flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 pb-2 text-sm font-medium transition-colors',
              isActive
                ? 'border-gray-900 text-gray-900'
                : 'border-transparent text-gray-400 hover:text-gray-900',
            )}
          >
            {Icon && <Icon className="h-4 w-4" />}
            {tab.label}
            {typeof tab.count === 'number' && tab.count > 0 && (
              <span
                className={cn(
                  'ml-0.5 inline-flex min-w-5 items-center justify-center rounded-full px-1.5 text-[11px] font-medium leading-5 text-white',
                  tabCountToneClass[tab.countTone ?? 'success'],
                )}
              >
                {tab.count}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}

export { Tabs }
