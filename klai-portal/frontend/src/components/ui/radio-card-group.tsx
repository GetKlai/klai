import { useId } from 'react'
import { cn } from '@/lib/utils'

export interface RadioCardOption {
  value: string
  label: string
  description?: string
  disabled?: boolean
}

interface RadioCardGroupProps {
  options: RadioCardOption[]
  value: string
  onChange: (value: string) => void
  /** Group-wide disable. Individual options can also set `disabled`. */
  disabled?: boolean
  /** Compact rendering: tighter padding, no description. */
  compact?: boolean
  'aria-label'?: string
  className?: string
}

/**
 * Selectable radio cards: a vertical stack of bordered option cards with a
 * native radio, a title and an optional description. The selected card gets a
 * dark border + subtle fill (no amber on active states per the v1 spine).
 *
 * Generic version of the profile picker pattern. Controlled — the caller owns
 * `value` and persistence.
 */
export function RadioCardGroup({
  options,
  value,
  onChange,
  disabled = false,
  compact = false,
  'aria-label': ariaLabel,
  className,
}: RadioCardGroupProps) {
  // Unique radio-group name so multiple groups on one page never share state.
  const name = useId()

  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className={cn(compact ? 'space-y-1.5' : 'space-y-2', className)}
    >
      {options.map((option) => {
        const isSelected = value === option.value
        const isDisabled = disabled || option.disabled
        return (
          <label
            key={option.value}
            className={cn(
              'flex items-start gap-3 rounded-lg border cursor-pointer transition-colors',
              compact ? 'p-2' : 'p-3',
              isSelected ? 'border-gray-900 bg-black/[0.06]' : 'border-gray-200 klai-hover',
              isDisabled && 'opacity-50 cursor-not-allowed',
            )}
          >
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={isSelected}
              disabled={isDisabled}
              onChange={() => {
                if (!isDisabled) onChange(option.value)
              }}
              className="mt-0.5 accent-gray-900"
            />
            <div className="space-y-0.5">
              <p className="text-sm font-medium text-gray-900">{option.label}</p>
              {!compact && option.description && (
                <p className="text-xs text-gray-400">{option.description}</p>
              )}
            </div>
          </label>
        )
      })}
    </div>
  )
}
