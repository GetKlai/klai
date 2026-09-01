/**
 * @purpose Form controls
 * @guideline KLAI-UI-007 must-not Pages are built from
 * `src/components/ui/`; a raw `input`, `select`, list row or delete
 * confirmation with inline Tailwind is a defect
 */
import * as React from 'react'
import { cn } from '@/lib/utils'

export interface TextareaProps
  extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {}

const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, ...props }, ref) => {
    return (
      <textarea
        className={cn(
          'w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm text-gray-900 outline-none transition-colors',
          'placeholder:text-gray-600',
          'focus:ring-2 focus:ring-[var(--color-ring)]',
          'disabled:cursor-not-allowed disabled:opacity-50',
          className,
        )}
        ref={ref}
        {...props}
      />
    )
  },
)
Textarea.displayName = 'Textarea'

export { Textarea }
