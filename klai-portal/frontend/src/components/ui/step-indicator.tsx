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
 * Pill-style wizard step indicator with gray accent.
 *
 * - Active step: solid dark pill with number.
 * - Completed steps: gray-tinted pill with check icon; clickable if `onClick` is provided.
 * - Future steps: muted pill, not clickable.
 * - Steps are joined by short horizontal connectors that turn dark once reached.
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
                    ? 'bg-gray-900'
                    : 'bg-gray-200',
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
                  ? 'bg-gray-900 text-white'
                  : isCompleted
                    ? 'bg-gray-100 text-gray-700 cursor-pointer hover:bg-gray-200'
                    : 'bg-gray-50 text-gray-400 cursor-default',
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
