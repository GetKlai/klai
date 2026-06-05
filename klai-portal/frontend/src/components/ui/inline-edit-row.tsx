import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { Check, Loader2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export interface InlineEditRowSubmit {
  name: string
  description: string
}

export interface InlineEditRowProps {
  /** True while this row is in edit mode. Parent owns the singleton so only one row edits at a time. */
  isEditing: boolean
  /**
   * Current name. Seeds the edit buffer on the false → true edit transition
   * and is shown as the title in view mode.
   */
  value: string
  /**
   * Current description. Seeds the description buffer; rendered under the
   * title (and made editable when `withDescription`).
   */
  description?: string
  /** Make the second (description) field editable — the "subtest" case. */
  withDescription?: boolean
  /** Disables inputs + save while a mutation is in flight, shows a spinner on save. */
  isSaving?: boolean
  namePlaceholder?: string
  descriptionPlaceholder?: string
  saveLabel: string
  cancelLabel: string
  onSubmit: (next: InlineEditRowSubmit) => void
  onCancel: () => void
  /**
   * Right-side cluster rendered in VIEW mode only (edit/delete icons, a badge,
   * a delete-confirm overlay, …). Hidden automatically while editing, where it
   * is replaced by the Save/Cancel cluster.
   */
  actions?: ReactNode
}

/**
 * Canonical inline edit for list rows — multi-field, zero layout shift.
 *
 * Layout contract:
 *   - The row is a single flex line, `items-center`, so the action cluster
 *     (icons in view / Save+Cancel in edit) is always vertically centred
 *     against the FULL content block, regardless of how many lines it has.
 *   - Each editable field (name, optional description) uses the ghost +
 *     absolute-overlay technique: the view text stays in the DOM (turned
 *     `invisible` while editing) so it always defines the box height, and
 *     the `<input>` is painted `absolute inset-0` on top. Toggling edit can
 *     therefore NEVER change the row height — zero vertical shift, which is
 *     the property the old single-field overlay had and the naive flex
 *     rewrite lost.
 *   - The content block is `flex-1` and the action cluster `shrink-0`, so the
 *     wider Save/Cancel pair (vs the two icons) makes the inputs shrink
 *     horizontally instead of overlapping the buttons.
 *
 * Buffer state (`name`, `description`) is seeded ONLY on the false → true
 * `isEditing` transition via a `useRef` guard, so a TanStack Query refetch
 * (window-focus, mutation invalidation) cannot wipe the user's typed input
 * mid-edit. Ported from the production `CoverageNodeRow`.
 *
 * Enter submits, Escape cancels.
 */
export function InlineEditRow({
  isEditing,
  value,
  description,
  withDescription = false,
  isSaving = false,
  namePlaceholder,
  descriptionPlaceholder,
  saveLabel,
  cancelLabel,
  onSubmit,
  onCancel,
  actions,
}: InlineEditRowProps) {
  const [name, setName] = useState(value)
  const [desc, setDesc] = useState(description ?? '')

  // Seed buffers on the false → true edit transition only — not on every
  // re-render while editing. Prevents query-refetch object-ref churn from
  // overwriting typed input (same regression class fixed in CoverageNodeRow).
  const prevIsEditing = useRef(false)
  useEffect(() => {
    if (isEditing && !prevIsEditing.current) {
      setName(value)
      setDesc(description ?? '')
    }
    prevIsEditing.current = isEditing
    // Deliberately omit value/description from deps — see comment above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isEditing])

  function submit() {
    const trimmed = name.trim()
    if (trimmed) onSubmit({ name: trimmed, description: desc.trim() })
  }

  function handleKeyDown(e: KeyboardEvent) {
    if (e.key === 'Enter') {
      e.preventDefault()
      submit()
    }
    if (e.key === 'Escape') onCancel()
  }

  // Amber rounded affordance via `ring` (not `border`) so it adds no box
  // width — the overlay stays exactly the size of its ghost text.
  const overlayInput = cn(
    'absolute inset-0 w-full rounded-md px-1',
    'bg-[var(--color-card)] text-[var(--color-foreground)]',
    'border-0 outline-none ring-1 ring-[var(--color-accent)]',
    'disabled:opacity-50',
  )

  const showDescriptionField = withDescription || Boolean(description)

  return (
    <div className="flex min-w-0 flex-1 items-center gap-2">
      <div className="min-w-0 flex-1">
        {/* Name field — ghost holds height, input overlays exactly. */}
        <div className="relative">
          <span
            className={cn(
              'block truncate text-[15px] font-display text-gray-900',
              isEditing && 'invisible',
            )}
          >
            {value}
          </span>
          {isEditing ? (
            <input
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={isSaving}
              placeholder={namePlaceholder}
              aria-label={namePlaceholder ?? value}
              className={cn(overlayInput, 'text-[15px] font-display')}
            />
          ) : null}
        </div>

        {/* Description field — same ghost+overlay so the second line never
            disappears (the layout-shift the user flagged). */}
        {showDescriptionField ? (
          <div className="relative mt-1">
            <p
              className={cn(
                'block truncate text-sm text-gray-400',
                isEditing && withDescription && 'invisible',
              )}
            >
              {description || ' '}
            </p>
            {isEditing && withDescription ? (
              <input
                value={desc}
                onChange={(e) => setDesc(e.target.value)}
                onKeyDown={handleKeyDown}
                disabled={isSaving}
                placeholder={descriptionPlaceholder}
                aria-label={descriptionPlaceholder ?? 'Beschrijving'}
                className={cn(overlayInput, 'text-sm')}
              />
            ) : null}
          </div>
        ) : null}
      </div>

      {/* Action cluster: centred against the whole block via the row's
          items-center. Save/Cancel use the app-standard inline-row button
          size (matches InlineDeleteConfirm exactly). */}
      <div className="flex shrink-0 items-center gap-1">
        {isEditing ? (
          <>
            <Button
              type="button"
              size="sm"
              disabled={isSaving || !name.trim()}
              onClick={submit}
              className="h-6 gap-1 px-2 text-[10px] [&_svg]:size-2.5 bg-[var(--color-success)] text-white hover:opacity-70"
            >
              {isSaving ? <Loader2 className="animate-spin" /> : <Check />}
              {saveLabel}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={onCancel}
              className="h-6 gap-1 px-2 text-[10px] [&_svg]:size-2.5"
            >
              <X />
              {cancelLabel}
            </Button>
          </>
        ) : (
          actions
        )}
      </div>
    </div>
  )
}
