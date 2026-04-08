import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-full text-xs font-normal uppercase tracking-[0.04em] transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        default:
          'bg-[var(--color-rl-accent)] text-[var(--color-foreground)] hover:bg-[var(--color-rl-accent-hover)]',
        secondary:
          'bg-[var(--color-secondary)] text-[var(--color-secondary-foreground)] hover:bg-[var(--color-rl-border)]',
        ghost:
          'border border-[var(--color-rl-dark-10)] bg-transparent hover:border-[var(--color-foreground)]',
        outline:
          'border border-[var(--color-border)] bg-transparent hover:bg-[var(--color-secondary)]',
        destructive:
          'bg-destructive text-white hover:opacity-90',
        link:
          'text-[var(--color-rl-accent-dark)] underline-offset-4 hover:underline normal-case tracking-normal',
      },
      size: {
        default: 'h-10 px-5 py-2',
        sm: 'h-8 px-3',
        lg: 'h-12 px-8 text-sm',
        icon: 'h-10 w-10',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  }
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button'
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    )
  }
)
Button.displayName = 'Button'

export { Button, buttonVariants }
