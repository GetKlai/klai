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
    <div
      className={cn(
        'grid gap-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-start',
        className,
      )}
      {...props}
    >
      <div className="min-w-0 space-y-1">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {title}
        </h1>
        {description ? (
          <p className="text-sm text-gray-400">{description}</p>
        ) : null}
      </div>
      {actions ? (
        <div className="flex justify-start sm:justify-end sm:pt-1">
          {actions}
        </div>
      ) : null}
    </div>
  )
}

export { PageHeader }
