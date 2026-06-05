import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const actionTagVariants = cva(
  'inline-flex w-fit items-center rounded-full border px-2 py-0.5 text-xs font-medium',
  {
    variants: {
      state: {
        open: 'border-green-500 bg-transparent text-green-700',
        closed: 'border-gray-200 bg-gray-100 text-gray-600',
      },
    },
    defaultVariants: {
      state: 'open',
    },
  },
)

export interface ActionTagProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof actionTagVariants> {}

function ActionTag({ className, state, ...props }: ActionTagProps) {
  return <span className={cn(actionTagVariants({ state }), className)} {...props} />
}

export { ActionTag, actionTagVariants }
