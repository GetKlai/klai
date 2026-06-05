import type { HTMLAttributes, ReactNode } from 'react'
import { cn } from '@/lib/utils'

// Omit the native HTML `title` attribute (string) so we can redefine it as a
// ReactNode heading without a TS2430 "incorrectly extends" collision.
interface PageHeaderProps extends Omit<HTMLAttributes<HTMLDivElement>, 'title'> {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
}

function PageHeader({ title, description, actions, className, ...props }: PageHeaderProps) {
  return (
    <div className={cn('space-y-1', className)} {...props}>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {title}
        </h1>
        {actions ? (
          <div className="flex shrink-0 items-center justify-start sm:ml-auto sm:justify-end">
            {actions}
          </div>
        ) : null}
      </div>
      {description ? (
        // When a primary action sits on the title row, cap the description so
        // it never runs under the action. ~60% of the header width clears the
        // right-aligned action with margin to spare. Full width when no action.
        <p className={cn('text-sm text-gray-400', actions ? 'sm:max-w-[60%]' : undefined)}>
          {description}
        </p>
      ) : null}
    </div>
  )
}

export { PageHeader }
