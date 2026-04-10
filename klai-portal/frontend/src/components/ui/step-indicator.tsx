import { Check } from 'lucide-react'

export interface StepItem {
  /** Visible label for the step. */
  label: string
  /**
   * Optional click handler. If provided AND the step is completed
   * (before currentIndex), the pill becomes clickable to jump back.
   */
  onClick?: () => void
}

interface StepIndicatorProps {
  /** Ordered list of steps to display. */
  steps: StepItem[]
  /** Zero-based index of the currently active step. */
  currentIndex: number
}

/**
 * Pill-style wizard step indicator with amber accent.
 *
 * - Active step: solid amber pill with number.
 * - Completed steps: amber-tinted pill with check icon; clickable if `onClick` is provided.
 * - Future steps: muted pill, not clickable.
 * - Steps are joined by short horizontal connectors that turn amber once reached.
 *
 * Shared between the knowledge-base creation wizard (`/app/knowledge/new`)
 * and the add-connector wizard (`/app/knowledge/$kbSlug/add-connector`).
 */
export function StepIndicator({ steps, currentIndex }: StepIndicatorProps) {
  return (
    <div className="flex items-center gap-2">
      {steps.map((step, i) => {
        const isActive = i === currentIndex
        const isCompleted = i < currentIndex
        const isClickable = isCompleted && !!step.onClick

        return (
          <div key={i} className="flex items-center gap-2">
            {i > 0 && (
              <div
                className={[
                  'h-px w-6',
                  isCompleted || isActive
                    ? 'bg-[var(--color-accent)]'
                    : 'bg-[var(--color-border)]',
                ].join(' ')}
              />
            )}
            <button
              type="button"
              onClick={() => isClickable && step.onClick!()}
              disabled={!isClickable}
              className={[
                'flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition-colors',
                isActive
                  ? 'bg-[var(--color-accent)] text-white'
                  : isCompleted
                    ? 'bg-[var(--color-accent)]/10 text-[var(--color-accent)] cursor-pointer hover:bg-[var(--color-accent)]/20'
                    : 'bg-[var(--color-secondary)] text-[var(--color-muted-foreground)] cursor-default',
              ].join(' ')}
            >
              {isCompleted && !isActive ? (
                <Check className="h-3 w-3" />
              ) : (
                <span>{i + 1}</span>
              )}
              {step.label}
            </button>
          </div>
        )
      })}
    </div>
  )
}
