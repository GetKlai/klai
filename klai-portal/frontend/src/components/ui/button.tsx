import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

// Portal v1 spine (SPEC-PORTAL-REDESIGN-002):
// - rounded-full (pill), sentence-case (no uppercase, no tracking-wider)
// - gray-on-white primary (polish-1 reintroduces amber)
// - focus ring uses --color-ring (amber) — amber is reserved for focus + logo
const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-full text-sm font-medium transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)] disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        default:
          'bg-gray-900 text-white hover:bg-gray-800',
        secondary:
          'bg-white text-gray-900 border border-gray-200 hover:bg-gray-50',
        ghost:
          'border border-gray-200 bg-transparent text-gray-700 hover:bg-gray-50',
        outline:
          'border border-gray-200 bg-transparent text-gray-700 hover:bg-gray-50',
        destructive:
          'bg-[var(--color-destructive)] text-white hover:opacity-90',
        link:
          'text-gray-700 underline-offset-4 hover:underline',
      },
      size: {
        default: 'h-10 px-5 py-2',
        sm: 'h-8 px-4',
        lg: 'h-12 px-8',
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
