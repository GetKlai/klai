import { Loader2, Trash2, X } from 'lucide-react'
import { InlineRowButton } from '@/components/ui/inline-row-button'
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
 *     <RowActionGroup>
 *       <RowActionIconButton label="Verwijderen" action="delete"
 *         onClick={() => setConfirmDeleteId(row.id)} />
 *     </RowActionGroup>
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
        // Opaque overlay covers any meta text that would otherwise show
        // through the gaps between the buttons. Matches the row's
        // hover bg (gray-50) so it blends with the row's confirming
        // state (see sources-row.tsx ``confirmingDelete`` branch).
        <div className="absolute inset-y-0 right-0 z-10 flex items-center gap-1 whitespace-nowrap bg-[var(--color-hover)] pl-4">
          <InlineRowButton tone="destructive" disabled={isPending} onClick={onConfirm}>
            {isPending ? <Loader2 className="animate-spin" /> : <Trash2 />}
            {label}
          </InlineRowButton>
          <InlineRowButton onClick={onCancel}>
            <X />
            {cancelLabel}
          </InlineRowButton>
        </div>
      )}
    </div>
  )
}
