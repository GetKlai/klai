import { type KeyboardEvent, type ReactNode } from 'react'

interface InlineEditProps {
  isEditing: boolean
  value: string
  onValueChange: (value: string) => void
  onSave: () => void
  onCancel: () => void
  isSaving?: boolean
  placeholder?: string
  /**
   * Extra Tailwind classes for the input element.
   * Use to match the text style of the view-mode content (e.g. "font-medium text-sm").
   */
  inputClassName?: string
  /**
   * View-mode content. Always rendered as an invisible spacer when editing,
   * so the cell height never changes on edit/cancel.
   */
  children: ReactNode
}

/**
 * Inline edit with amber ring and zero layout shift.
 *
 * How it works:
 * - `children` stays in the DOM at all times — `invisible` when editing to preserve height.
 * - The `<input>` is absolutely positioned on top, so it fills the exact same space.
 * - `ring-1 ring-[var(--color-accent)]` provides the amber focus indicator without adding border width.
 * - `rounded-none` gives square forms per portal standard.
 *
 * Save: Enter key or external trigger.
 * Cancel: Escape key or external trigger.
 *
 * Usage:
 * ```tsx
 * <InlineEdit
 *   isEditing={editingId === item.id}
 *   value={editName}
 *   onValueChange={setEditName}
 *   onSave={() => save(item.id)}
 *   onCancel={cancelEdit}
 *   isSaving={isSaving}
 *   inputClassName="font-medium text-sm"
 * >
 *   <span className="font-medium text-sm">{item.name}</span>
 * </InlineEdit>
 * ```
 */
export function InlineEdit({
  isEditing,
  value,
  onValueChange,
  onSave,
  onCancel,
  isSaving = false,
  placeholder,
  inputClassName = '',
  children,
}: InlineEditProps) {
  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') onSave()
    if (e.key === 'Escape') onCancel()
  }

  return (
    <div className="relative">
      {/* Spacer — always in DOM to hold layout height */}
      <div className={isEditing ? 'invisible pointer-events-none' : undefined}>
        {children}
      </div>
      {/* Amber-ring input overlay — does not affect layout */}
      {isEditing && (
        <input
          autoFocus
          value={value}
          onChange={(e) => onValueChange(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isSaving}
          placeholder={placeholder}
          className={[
            'absolute inset-0 w-full',
            'bg-[var(--color-card)]',
            'text-[var(--color-foreground)]',
            'border-0 rounded-none p-0 px-1',
            'ring-1 ring-[var(--color-accent)]',
            'outline-none',
            'disabled:opacity-50',
            inputClassName,
          ]
            .filter(Boolean)
            .join(' ')}
        />
      )}
    </div>
  )
}
