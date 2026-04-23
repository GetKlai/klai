import * as React from 'react'
import { cn } from '@/lib/utils'

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {}

// Portal v1 spine (SPEC-PORTAL-REDESIGN-002):
// - rounded-lg (was rounded-md)
// - border-gray-200 (Tailwind literal per grayscale rule)
// - focus ring amber (preserved via --color-ring)
const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, ...props }, ref) => {
    return (
      <input
        type={type}
        className={cn(
          'w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm text-gray-900 outline-none transition-colors',
          'placeholder:text-gray-400',
          'focus:ring-2 focus:ring-[var(--color-ring)]',
          'disabled:cursor-not-allowed disabled:opacity-50',
          className
        )}
        ref={ref}
        {...props}
      />
    )
  }
)
Input.displayName = 'Input'

export { Input }
