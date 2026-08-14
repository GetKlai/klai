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
 * Inline rename overlay uses the `<InlineEdit>` field primitive and the
 * shared `<InlineRowButton>` for Save/Cancel (the single source of truth
 * for inline-row action pills — see ui-standards.md).
 */
import { useState } from 'react'
import { Check, Loader2, X } from 'lucide-react'
import { InlineRowButton } from '@/components/ui/inline-row-button'
import { InlineEdit } from '@/components/ui/inline-edit'
import {
  ListRow,
  ListRowActions,
  ListRowContent,
  ListRowDescription,
  ListRowIcon,
  ListRowTitle,
} from '@/components/ui/list'
import * as m from '@/paraglide/messages'
import { SourceContent } from './-sources-content'
import { SourceRowActions } from './-sources-row-actions'
import { FailedItemsWarning, SourceIcon, StatusBadge } from './-sources-helpers'
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
  // chunk count - the parent_chunks number is unreliable per-row.
  const metaParts: string[] = [source.type_label]
  if (source.kind === 'connector' && source.items_count > 0) {
    metaParts.push(`${source.items_count} items`)
  }
  const meta = metaParts.join(' · ')

  return (
    <div>
      <ListRow
        confirming={confirmingDelete}
        className="grid items-center gap-x-3 gap-y-2 px-4 py-4 klai-hover sm:grid-cols-[2rem_minmax(0,1fr)_8rem_6.5rem]"
      >
        <ListRowIcon className="self-center">
          <SourceIcon source={source} />
        </ListRowIcon>
        <ListRowContent>
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
              <ListRowTitle className="block min-w-0">{source.name}</ListRowTitle>
              <ListRowDescription>{meta}</ListRowDescription>
              <FailedItemsWarning source={source} />
            </button>
          </InlineEdit>
        </ListRowContent>

        <div className="justify-self-start whitespace-nowrap sm:justify-self-end">
          <StatusBadge source={source} />
        </div>

        {/* Actions cell with rename-overlay slot.
            When renaming, SourceRowActions fades out and the Save/Cancel
            buttons sit on top - same width, no layout shift. */}
        <ListRowActions
          className="relative self-center justify-self-end"
          onClick={(e) => e.stopPropagation()}
        >
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
              <InlineRowButton
                tone="success"
                disabled={renameMutation.isPending || !draftName.trim()}
                onClick={saveRename}
              >
                {renameMutation.isPending ? <Loader2 className="animate-spin" /> : <Check />}
                {m.kb_sources_row_save()}
              </InlineRowButton>
              <InlineRowButton onClick={cancelRename}>
                <X />
                {m.kb_sources_row_cancel()}
              </InlineRowButton>
            </div>
          )}
        </ListRowActions>
      </ListRow>
      {expanded && <SourceContent kbSlug={kbSlug} source={source} />}
    </div>
  )
}
