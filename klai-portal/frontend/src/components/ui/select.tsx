import * as React from 'react'
import { ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {}

// Portal v1 spine (SPEC-PORTAL-REDESIGN-002):
// - rounded-lg (was rounded-md)
// - border-gray-200 (Tailwind literal)
// - focus ring amber (preserved via --color-ring)
//
// The native dropdown arrow is replaced by a custom chevron so its right
// padding is symmetric with the left text padding (both 0.75rem / px-3),
// instead of the browser-default arrow inset which reads as misaligned.
const Select = React.forwardRef<HTMLSelectElement, SelectProps>(
  ({ className, children, ...props }, ref) => {
    return (
      <div className="relative w-full">
        <select
          ref={ref}
          className={cn(
            'w-full appearance-none rounded-lg border border-gray-200 bg-transparent pl-3 pr-9 py-2 text-sm text-gray-900 outline-none transition-colors',
            'focus:ring-2 focus:ring-[var(--color-ring)]',
            'disabled:cursor-not-allowed disabled:opacity-50',
            className,
          )}
          {...props}
        >
          {children}
        </select>
        <ChevronDown
          className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400"
          aria-hidden="true"
        />
      </div>
    )
  },
)
Select.displayName = 'Select'

export { Select }
