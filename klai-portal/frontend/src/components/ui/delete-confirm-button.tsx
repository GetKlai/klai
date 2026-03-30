import { useState } from 'react'
import { Trash2, Check, X, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'

interface DeleteConfirmButtonProps {
  onConfirm: () => void | Promise<void>
  isDeleting?: boolean
  disabled?: boolean
  deleteLabel?: string
  confirmLabel?: string
  cancelLabel?: string
  className?: string
}

export function DeleteConfirmButton({
  onConfirm,
  isDeleting = false,
  disabled = false,
  deleteLabel = 'Delete',
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  className,
}: DeleteConfirmButtonProps) {
  const [confirming, setConfirming] = useState(false)

  if (isDeleting) {
    return (
      <div className={cn('flex h-7 w-7 items-center justify-center', className)}>
        <Loader2 className="h-4 w-4 animate-spin text-[var(--color-muted-foreground)]" />
      </div>
    )
  }

  if (confirming) {
    return (
      <div className={cn('flex items-center gap-1 animate-slide-in-from-right', className)}>
        <button
          onClick={async () => {
            setConfirming(false)
            await onConfirm()
          }}
          aria-label={confirmLabel}
          className="flex h-7 w-7 items-center justify-center rounded bg-[var(--color-success)] text-white transition-colors hover:opacity-90"
        >
          <Check className="h-3.5 w-3.5" />
        </button>
        <button
          onClick={() => setConfirming(false)}
          aria-label={cancelLabel}
          className="flex h-7 w-7 items-center justify-center rounded bg-[var(--color-destructive)] text-white transition-colors hover:opacity-90"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    )
  }

  return (
    <button
      onClick={() => setConfirming(true)}
      disabled={disabled}
      aria-label={deleteLabel}
      className={cn(
        'p-1 text-[var(--color-muted-foreground)] transition-colors hover:text-[var(--color-destructive)] disabled:pointer-events-none disabled:opacity-50',
        className,
      )}
    >
      <Trash2 className="h-4 w-4" />
    </button>
  )
}
