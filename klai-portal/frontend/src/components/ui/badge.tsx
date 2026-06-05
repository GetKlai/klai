import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

// Portal v1 spine (SPEC-PORTAL-REDESIGN-002):
// - rounded-full (same as buttons)
// - default/accent use neutral gray (polish-1 decides amber reintroduction)
// - Semantic states derive from the SAME primary tokens as the row-action
//   tones (var(--color-success|warning|destructive|info)), kept soft via a
//   10% tint background — same hue and meaning as the solid action icons,
//   just softer. This is the single source of the semantic badge palette;
//   do not hand-roll status pills with ad-hoc /10 tints.
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
          'border-transparent bg-[var(--color-success)]/10 text-[var(--color-success)]',
        warning:
          'border-transparent bg-[var(--color-warning)]/10 text-[var(--color-warning)]',
        destructive:
          'border-transparent bg-[var(--color-destructive)]/10 text-[var(--color-destructive)]',
        info:
          'border-transparent bg-[var(--color-info)]/10 text-[var(--color-info)]',
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
