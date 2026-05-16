/**
 * One row in the Sources tab list.
 *
 * Composition:
 *   - This file owns header (icon + InlineEdit name + meta) + status
 *     badge + drill-down content + rename overlay.
 *   - `-sources-row-actions.tsx` owns the action cluster (reauth, sync,
 *     delete, connector-config, upload-rename trigger, docs-editor link,
 *     chevron).
 *
 * Pencil-icon disambiguation (SPEC-PORTAL-SOURCES-RENAME-001 REQ-5/6/7):
 *   - Pencil → upload inline rename
 *   - Settings → connector edit-config route
 *   - NotebookPen → open in docs editor
 *
 * Inline rename overlay uses the `<InlineEdit>` primitive and the
 * documented `h-6 text-[10px] [&_svg]:size-2.5` Save/Cancel Button
 * pattern from portal-frontend.md.
 */
import { useState } from 'react'
import { Check, Loader2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { InlineEdit } from '@/components/ui/inline-edit'
import * as m from '@/paraglide/messages'
import { SourceContent } from './-sources-content'
import { SourceRowActions } from './-sources-row-actions'
import { mapSourceStatus, SourceIcon, StatusBadge } from './-sources-helpers'
import { useSourceRename } from './-sources-hooks'
import type { Source } from './-sources-types'

interface SourceRowProps {
  source: Source
  expanded: boolean
  onToggle: () => void
  kbSlug: string
  /** Gitea page slug for this source, when one exists. Null = no editor link. */
  editablePageId: string | null
}

export function SourceRow({ source, expanded, onToggle, kbSlug, editablePageId }: SourceRowProps) {
  const status = mapSourceStatus(source)
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [isRenaming, setIsRenaming] = useState(false)
  const [draftName, setDraftName] = useState(source.name)

  // `useSourceRename` closes the overlay via its onSuccess callback.
  // Failures keep the overlay open so the user can retry without re-typing
  // (mirrors the pre-rename behaviour from PR #574).
  const renameMutation = useSourceRename(kbSlug, source, () => setIsRenaming(false))

  function startRename() {
    setDraftName(source.name)
    setIsRenaming(true)
  }
  function cancelRename() {
    setDraftName(source.name)
    setIsRenaming(false)
  }
  function saveRename() {
    const trimmed = draftName.trim()
    if (trimmed && trimmed !== source.name) renameMutation.mutate(trimmed)
    else cancelRename()
  }

  // Meta line: type label, optional item count for connectors. Drop the
  // chunk count — the parent_chunks number is unreliable per-row.
  const metaParts: string[] = [source.type_label]
  if (source.kind === 'connector' && source.items_count > 0) {
    metaParts.push(`${source.items_count} items`)
  }
  const meta = metaParts.join(' · ')

  return (
    <div>
      <div
        className={[
          'group flex items-center gap-2 pr-2 transition-colors',
          // Keep the row in its hovered state while the delete-confirm
          // pill is open — otherwise the pill (cream) would float
          // on a white row and the contrast looks like a bug.
          confirmingDelete ? 'bg-[var(--color-rl-cream)]' : 'hover:bg-[var(--color-rl-cream)]',
        ].join(' ')}
      >
        <div className="flex flex-1 min-w-0 items-center gap-3 px-2 py-3.5">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center text-gray-400">
            <SourceIcon source={source} />
          </div>
          <div className="min-w-0 flex-1">
            <InlineEdit
              isEditing={isRenaming}
              value={draftName}
              onValueChange={setDraftName}
              onSave={saveRename}
              onCancel={cancelRename}
              isSaving={renameMutation.isPending}
              inputClassName="text-[15px] font-display"
            >
              <button
                type="button"
                onClick={onToggle}
                className="min-w-0 w-full text-left"
                aria-expanded={expanded}
              >
                <div className="flex items-baseline gap-2 min-w-0">
                  <span className="text-[15px] font-display text-gray-900 truncate min-w-0 flex-1">{source.name}</span>
                  <span className="text-xs text-gray-400 shrink-0">{meta}</span>
                </div>
              </button>
            </InlineEdit>
          </div>
        </div>

        <StatusBadge status={status} />

        {/* Actions cell with rename-overlay slot.
            When renaming, SourceRowActions fades out and the Save/Cancel
            buttons sit on top — same width, no layout shift. */}
        <div className="relative flex items-center">
          <SourceRowActions
            source={source}
            kbSlug={kbSlug}
            editablePageId={editablePageId}
            isRenaming={isRenaming}
            onStartRename={startRename}
            expanded={expanded}
            onToggle={onToggle}
            confirmingDelete={confirmingDelete}
            onSetConfirmingDelete={setConfirmingDelete}
          />
          {isRenaming && (
            <div className="absolute inset-y-0 right-0 z-10 flex items-center gap-1 whitespace-nowrap">
              <Button
                size="sm"
                className="h-6 text-[10px] px-2 gap-1 [&_svg]:size-2.5 bg-[var(--color-success)] text-white hover:opacity-70"
                disabled={renameMutation.isPending || !draftName.trim()}
                onClick={saveRename}
              >
                {renameMutation.isPending ? <Loader2 className="animate-spin" /> : <Check />}
                {m.kb_sources_row_save()}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-6 text-[10px] px-2 gap-1 [&_svg]:size-2.5"
                onClick={cancelRename}
              >
                <X />
                {m.kb_sources_row_cancel()}
              </Button>
            </div>
          )}
        </div>
      </div>
      {expanded && <SourceContent kbSlug={kbSlug} source={source} />}
    </div>
  )
}
