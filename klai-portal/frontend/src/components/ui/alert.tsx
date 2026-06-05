import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import {
  AlertTriangle,
  CheckCircle2,
  Info,
  XCircle,
  type LucideIcon,
} from 'lucide-react'
import { cn } from '@/lib/utils'

// Inline semantic callout. Standardizes the hand-rolled
// "icon + message in a soft tinted rounded box" pattern that recurred
// across wizard feedback and form warnings.
//
// Like Badge, the semantic variants derive from the SAME primary tokens as
// the row-action tones. The surface is kept soft via a 5% tint background +
// 30% tint border; the icon/text use the darker `*-text` token variant
// (var(--color-success-text) etc.) so labels clear WCAG AA on the light tint.
// Do not hand-roll status callouts with raw amber/red/green Tailwind literals;
// use this component.
const alertVariants = cva('flex gap-2 rounded-lg border', {
  variants: {
    variant: {
      info: 'border-[var(--color-info)]/30 bg-[var(--color-info)]/5 text-[var(--color-info-text)]',
      success:
        'border-[var(--color-success)]/30 bg-[var(--color-success)]/5 text-[var(--color-success-text)]',
      warning:
        'border-[var(--color-warning)]/30 bg-[var(--color-warning)]/5 text-[var(--color-warning-text)]',
      destructive:
        'border-[var(--color-destructive)]/30 bg-[var(--color-destructive)]/5 text-[var(--color-destructive-text)]',
    },
    size: {
      sm: 'p-3 text-xs',
      default: 'p-3 text-sm',
    },
  },
  defaultVariants: {
    variant: 'info',
    size: 'default',
  },
})

const variantIcon: Record<
  NonNullable<VariantProps<typeof alertVariants>['variant']>,
  LucideIcon
> = {
  info: Info,
  success: CheckCircle2,
  warning: AlertTriangle,
  destructive: XCircle,
}

export interface AlertProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof alertVariants> {
  /**
   * Override the leading icon. Pass `null` to hide it entirely.
   * Defaults to the variant's semantic icon.
   */
  icon?: LucideIcon | null
}

function Alert({ className, variant, size, icon, children, ...props }: AlertProps) {
  const Icon = icon === null ? null : (icon ?? variantIcon[variant ?? 'info'])
  return (
    <div role="alert" className={cn(alertVariants({ variant, size }), className)} {...props}>
      {Icon && (
        <Icon className={cn('mt-0.5 shrink-0', size === 'sm' ? 'h-3.5 w-3.5' : 'h-4 w-4')} />
      )}
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  )
}

export { Alert, alertVariants }
