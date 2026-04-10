import { Loader2, Trash2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { ReactNode } from 'react'

interface InlineDeleteConfirmProps {
  isConfirming: boolean
  isPending?: boolean
  label: ReactNode
  cancelLabel: string
  onConfirm: () => void
  onCancel: () => void
  children: ReactNode
}

/**
 * Ghost spacer + absolute overlay pattern for inline delete confirmation in table rows.
 *
 * Keeps the original action icons in the DOM (as invisible spacer) when confirming,
 * preventing layout shift. The confirm/cancel overlay is absolutely positioned and
 * grows leftward with whitespace-nowrap.
 *
 * Usage:
 *   <InlineDeleteConfirm
 *     isConfirming={confirmDeleteId === row.id}
 *     isPending={deleteMutation.isPending}
 *     label={m.some_delete_confirm({ name: row.name })}
 *     cancelLabel={m.cancel()}
 *     onConfirm={() => deleteMutation.mutate(row.id)}
 *     onCancel={() => setConfirmDeleteId(null)}
 *   >
 *     <div className="flex items-center justify-end gap-1">
 *       <button onClick={() => setConfirmDeleteId(row.id)}><Trash2 /></button>
 *     </div>
 *   </InlineDeleteConfirm>
 */
export function InlineDeleteConfirm({
  isConfirming,
  isPending = false,
  label,
  cancelLabel,
  onConfirm,
  onCancel,
  children,
}: InlineDeleteConfirmProps) {
  return (
    <div className="relative">
      <div className={isConfirming ? 'opacity-0 pointer-events-none' : undefined}>
        {children}
      </div>
      {isConfirming && (
        <div className="absolute inset-y-0 right-0 z-10 flex items-center gap-1 whitespace-nowrap">
          <Button
            size="sm"
            className="h-6 text-[10px] px-2 gap-1 [&_svg]:size-2.5 bg-[var(--color-destructive)] text-white hover:opacity-70"
            disabled={isPending}
            onClick={onConfirm}
          >
            {isPending ? <Loader2 className="animate-spin" /> : <Trash2 />}
            {label}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="h-6 text-[10px] px-2 gap-1 [&_svg]:size-2.5"
            onClick={onCancel}
          >
            <X />
            {cancelLabel}
          </Button>
        </div>
      )}
    </div>
  )
}
