import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

// Portal v1 spine (SPEC-PORTAL-REDESIGN-002):
// - rounded-full (same as buttons)
// - default/accent use neutral gray (polish-1 decides amber reintroduction)
// - semantic states (success/warning/destructive) use CSS tokens
const badgeVariants = cva(
  'inline-flex w-fit items-center rounded-full border px-2 py-0.5 text-xs font-medium transition-colors',
  {
    variants: {
      variant: {
        default:
          'border-transparent bg-gray-900 text-white',
        secondary:
          'border-transparent bg-gray-100 text-gray-700',
        accent:
          'border-transparent bg-gray-900 text-white',
        outline:
          'border-gray-200 text-gray-700',
        success:
          'border-transparent bg-[var(--color-success-bg)] text-[var(--color-success-text)]',
        warning:
          'border-transparent bg-[var(--color-warning-subtle)] text-[var(--color-warning-text)]',
        destructive:
          'border-transparent bg-[var(--color-destructive-bg)] text-[var(--color-destructive-text)]',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  }
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { Badge, badgeVariants }
