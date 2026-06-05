import type { ElementType, HTMLAttributes, ReactNode } from 'react'
import { Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'

interface ListStateProps extends HTMLAttributes<HTMLDivElement> {}

function ListState({ className, ...props }: ListStateProps) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center py-12 text-center text-sm text-gray-400',
        className,
      )}
      {...props}
    />
  )
}

interface ListLoadingStateProps extends ListStateProps {
  label: ReactNode
}

function ListLoadingState({ label, className, ...props }: ListLoadingStateProps) {
  return (
    <ListState
      role="status"
      aria-live="polite"
      className={cn('py-8', className)}
      {...props}
    >
      <span className="inline-flex items-center gap-2">
        <Loader2 className="h-4 w-4 animate-spin" />
        {label}
      </span>
    </ListState>
  )
}

// Omit the native HTML `title` attribute (string) so we can redefine it as a
// ReactNode heading without a TS2430 "incorrectly extends" collision.
interface ListEmptyStateProps extends Omit<ListStateProps, 'title'> {
  /** Optional illustrative icon shown above the title (muted, decorative). */
  icon?: ElementType
  title: ReactNode
  description?: ReactNode
}

function ListEmptyState({ icon: Icon, title, description, className, ...props }: ListEmptyStateProps) {
  return (
    <ListState className={className} {...props}>
      {Icon && <Icon className="mb-3 h-8 w-8 text-gray-400" aria-hidden="true" />}
      <p>{title}</p>
      {description && (
        <p className="mt-1 max-w-sm text-xs text-[var(--color-muted-foreground)]">
          {description}
        </p>
      )}
    </ListState>
  )
}

export { ListState, ListLoadingState, ListEmptyState }
