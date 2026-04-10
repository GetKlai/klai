import { useState } from 'react'
import { Trash2, Loader2 } from 'lucide-react'
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface DeleteConfirmButtonProps {
  onConfirm: () => void | Promise<void>
  isDeleting?: boolean
  disabled?: boolean
  /** Short description shown in the popover, e.g. "Delete group Admins" */
  label?: string
  confirmLabel?: string
  cancelLabel?: string
  className?: string
}

export function DeleteConfirmButton({
  onConfirm,
  isDeleting = false,
  disabled = false,
  label,
  confirmLabel = 'Delete',
  cancelLabel = 'Cancel',
  className,
}: DeleteConfirmButtonProps) {
  const [open, setOpen] = useState(false)

  if (isDeleting) {
    return (
      <div className={cn('flex h-7 w-7 items-center justify-center', className)}>
        <Loader2 className="h-4 w-4 animate-spin text-[var(--color-muted-foreground)]" />
      </div>
    )
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          disabled={disabled}
          className={cn(
            'flex h-7 w-7 items-center justify-center text-[var(--color-destructive)] transition-opacity hover:opacity-70 disabled:pointer-events-none disabled:opacity-50',
            className,
          )}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        side="bottom"
        className="w-auto max-w-56 p-3"
      >
        {label && (
          <p className="mb-2 text-sm text-[var(--color-foreground)]">{label}</p>
        )}
        <div className="flex items-center gap-1.5">
          <Button
            size="sm"
            className="h-7 bg-[var(--color-destructive)] px-2.5 text-xs text-white hover:opacity-90"
            onClick={() => {
              setOpen(false)
              void onConfirm()
            }}
          >
            {confirmLabel}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="h-7 px-2.5 text-xs"
            onClick={() => setOpen(false)}
          >
            {cancelLabel}
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}
