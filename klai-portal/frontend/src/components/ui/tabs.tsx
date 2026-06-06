import * as React from 'react'
import { cn } from '@/lib/utils'

export type TabCountTone = 'neutral' | 'success' | 'warning' | 'destructive' | 'info'

const tabCountToneClass: Record<TabCountTone, string> = {
  neutral: 'bg-gray-100 text-gray-600',
  success: 'bg-[var(--color-success)]',
  warning: 'bg-[var(--color-warning)]',
  destructive: 'bg-[var(--color-destructive)]',
  info: 'bg-[var(--color-info)]',
}

interface TabItemBase<T extends string = string> {
  id: T
  label: React.ReactNode
  /** Optional leading icon. Use sparingly — text-only is the default. */
  icon?: React.ElementType
}

type TabItemPlainCount = {
  /** Optional trailing entity count. Only shown when > 0. */
  count?: number
  /** Accessible label for the entity count badge (e.g. "3 users"). */
  countLabel?: string
  notificationCount?: never
  notificationTone?: never
  notificationLabel?: never
}

type TabItemNotificationCount = {
  count?: never
  countLabel?: never
  /** Optional trailing notification count. Only shown when > 0. */
  notificationCount?: number
  /** Tone of the notification badge. Defaults to success for new/unread items. */
  notificationTone?: Exclude<TabCountTone, 'neutral'>
  /** Accessible label for the notification badge (e.g. "3 unread"). */
  notificationLabel?: string
}

export type TabItem<T extends string = string> = TabItemBase<T> & (
  | TabItemPlainCount
  | TabItemNotificationCount
)

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
 * and badges are optional; text-only is the default, on-brand look. Use
 * `count` for neutral entity totals and `notificationCount` for new/unread
 * items that need semantic color.
 *
 * Implements the WAI-ARIA tablist keyboard pattern: roving tabindex (only the
 * active tab is in the tab order) with Arrow/Home/End moving focus and
 * activating the focused tab (selection follows focus).
 */
function Tabs<T extends string>({ tabs, value, onValueChange, className, ...props }: TabsProps<T>) {
  const tabRefs = React.useRef<(HTMLButtonElement | null)[]>([])

  function handleKeyDown(event: React.KeyboardEvent<HTMLButtonElement>, index: number) {
    const last = tabs.length - 1
    let next: number
    switch (event.key) {
      case 'ArrowRight':
      case 'ArrowDown':
        next = index === last ? 0 : index + 1
        break
      case 'ArrowLeft':
      case 'ArrowUp':
        next = index === 0 ? last : index - 1
        break
      case 'Home':
        next = 0
        break
      case 'End':
        next = last
        break
      default:
        return
    }
    event.preventDefault()
    onValueChange(tabs[next].id)
    tabRefs.current[next]?.focus()
  }

  return (
    <div role="tablist" className={cn('flex gap-6 border-b border-gray-200', className)} {...props}>
      {tabs.map((tab, index) => {
        const isActive = tab.id === value
        const Icon = tab.icon
        const badge =
          typeof tab.notificationCount === 'number'
            ? {
                value: tab.notificationCount,
                label: tab.notificationLabel,
                tone: tab.notificationTone ?? 'success',
              }
            : typeof tab.count === 'number'
              ? {
                  value: tab.count,
                  label: tab.countLabel,
                  tone: 'neutral' as const,
                }
              : null
        return (
          <button
            key={tab.id}
            ref={(el) => {
              tabRefs.current[index] = el
            }}
            type="button"
            role="tab"
            aria-selected={isActive}
            tabIndex={isActive ? 0 : -1}
            onClick={() => onValueChange(tab.id)}
            onKeyDown={(e) => handleKeyDown(e, index)}
            className={cn(
              'flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 pb-2 text-sm font-medium transition-colors',
              isActive
                ? 'border-gray-900 text-gray-900'
                : 'border-transparent text-gray-400 hover:text-gray-900',
            )}
          >
            {Icon && <Icon className="h-4 w-4" />}
            {tab.label}
            {badge && badge.value > 0 && (
              <span
                aria-label={badge.label}
                className={cn(
                  'ml-0.5 inline-flex min-w-5 items-center justify-center rounded-full px-1.5 text-[11px] font-medium leading-5',
                  badge.tone !== 'neutral' ? 'text-white' : null,
                  tabCountToneClass[badge.tone],
                )}
              >
                {badge.value}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}

export { Tabs }
