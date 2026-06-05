import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cn } from '@/lib/utils'

interface ListFrameProps extends React.HTMLAttributes<HTMLDivElement> {}

function ListFrame({ className, ...props }: ListFrameProps) {
  return (
    <div
      className={cn('divide-y divide-gray-200 border-y border-gray-200', className)}
      {...props}
    />
  )
}

interface ListRowProps extends React.HTMLAttributes<HTMLDivElement> {
  asChild?: boolean
  interactive?: boolean
  confirming?: boolean
}

const ListRow = React.forwardRef<HTMLDivElement, ListRowProps>(
  ({ asChild = false, interactive = false, confirming = false, className, ...props }, ref) => {
    const Comp = asChild ? Slot : 'div'
    return (
      <Comp
        ref={ref}
        className={cn(
          'group flex items-start gap-4 px-2 py-3.5 transition-colors',
          interactive && 'cursor-pointer klai-hover',
          confirming && 'bg-[var(--color-hover)]',
          className,
        )}
        {...props}
      />
    )
  },
)
ListRow.displayName = 'ListRow'

interface ListRowIconProps extends React.HTMLAttributes<HTMLDivElement> {}

function ListRowIcon({ className, ...props }: ListRowIconProps) {
  return (
    <div
      className={cn('flex h-8 w-8 shrink-0 items-center justify-center text-gray-400', className)}
      {...props}
    />
  )
}

interface ListRowContentProps extends React.HTMLAttributes<HTMLDivElement> {}

function ListRowContent({ className, ...props }: ListRowContentProps) {
  return <div className={cn('min-w-0 flex-1', className)} {...props} />
}

interface ListRowTitleProps extends React.HTMLAttributes<HTMLSpanElement> {}

function ListRowTitle({ className, ...props }: ListRowTitleProps) {
  return (
    <span
      className={cn('truncate text-[15px] font-display text-gray-900', className)}
      {...props}
    />
  )
}

interface ListRowDescriptionProps extends React.HTMLAttributes<HTMLParagraphElement> {}

function ListRowDescription({ className, ...props }: ListRowDescriptionProps) {
  return <p className={cn('mt-1 truncate text-sm text-gray-400', className)} {...props} />
}

interface ListRowActionsProps extends React.HTMLAttributes<HTMLDivElement> {}

function ListRowActions({ className, ...props }: ListRowActionsProps) {
  return (
    <div
      className={cn('flex shrink-0 items-center justify-end gap-1', className)}
      {...props}
    />
  )
}

export {
  ListFrame,
  ListRow,
  ListRowIcon,
  ListRowContent,
  ListRowTitle,
  ListRowDescription,
  ListRowActions,
}
