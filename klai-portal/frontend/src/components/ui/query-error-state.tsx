import { AlertCircle, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'

interface QueryErrorStateProps {
  error: Error
  onRetry?: () => void
}

export function QueryErrorState({ error, onRetry }: QueryErrorStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-12 text-center">
      <AlertCircle className="h-10 w-10 text-[var(--color-destructive)] mb-3" />
      <p className="text-sm text-[var(--color-muted-foreground)] mb-4">
        {error.message || m.error_generic()}
      </p>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RefreshCw className="h-4 w-4 mr-2" />
          {m.error_retry()}
        </Button>
      )}
    </div>
  )
}
