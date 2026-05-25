/**
 * Checkbox component - styled native checkbox matching the design system.
 * No Radix dependency needed; wraps a standard <input type="checkbox">.
 */
import type { InputHTMLAttributes } from 'react'
import { forwardRef } from 'react'

interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  label?: string
}

export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(
  ({ label, className = '', ...props }, ref) => {
    return (
      <label className="flex items-start gap-3 cursor-pointer select-none">
        <input
          type="checkbox"
          ref={ref}
          className={`mt-0.5 h-4 w-4 rounded border-[var(--color-border)] accent-[var(--color-accent)] cursor-pointer ${className}`}
          {...props}
        />
        {label && (
          <span className="text-sm text-[var(--color-foreground)]">{label}</span>
        )}
      </label>
    )
  },
)
Checkbox.displayName = 'Checkbox'
