import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex w-fit items-center rounded-full border font-medium transition-colors',
  {
    variants: {
      variant: {
        default:
          'border-transparent bg-[var(--color-primary)] text-[var(--color-primary-foreground)]',
        secondary:
          'border-transparent bg-[var(--color-secondary)] text-[var(--color-secondary-foreground)]',
        accent:
          'border-transparent bg-[var(--color-accent)] text-white',
        outline:
          'border-[var(--color-border)] text-[var(--color-foreground)]',
        success:
          'border-transparent bg-[var(--color-success-bg)] text-[var(--color-success-text)]',
        warning:
          'border-transparent bg-[var(--color-warning-subtle)] text-[var(--color-warning-text)]',
        destructive:
          'border-transparent bg-[var(--color-destructive-bg)] text-[var(--color-destructive-text)]',
      },
      size: {
        default: 'px-2.5 py-0.5 text-xs',
        sm: 'px-1.5 py-0.5 text-sm',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  }
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, size, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant, size }), className)} {...props} />
}

export { Badge, badgeVariants }
