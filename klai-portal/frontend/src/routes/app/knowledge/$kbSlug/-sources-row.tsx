/**
 * One row in the Sources tab list.
 *
 * Owns: icon · name · meta · status · actions · drill-down toggle.
 * Mutations live in `-sources-hooks.ts`; the inline rename uses the
 * shared `<InlineEdit>` primitive per portal-frontend.md.
 *
 * Pencil-icon disambiguation (SPEC-PORTAL-SOURCES-RENAME-001 REQ-5/6/7):
 *   - Pencil → upload inline rename ONLY
 *   - Settings → connector edit-config route
 *   - NotebookPen → open in docs editor
 *
 * Save/cancel for the rename overlay live in the actions cell using the
 * documented `h-6 text-[10px] [&_svg]:size-2.5` Button pattern. The
 * action icons stay in the DOM (opacity-0 + pointer-events-none) so
 * the cell width never changes between view and edit modes.
 */
import { Link } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import {
  Check,
  ChevronRight,
  Link as LinkIcon,
  Loader2,
  NotebookPen,
  Pencil,
  RefreshCw,
  Settings,
  Trash2,
  X,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import { InlineEdit } from '@/components/ui/inline-edit'
import { Tooltip } from '@/components/ui/tooltip'
import { SourceContent } from './-sources-content'
import { mapSourceStatus, SourceIcon, StatusBadge } from './-sources-helpers'
import {
  useSourceDelete,
  useSourceRename,
  useSourceReauth,
  useSourceSync,
} from './-sources-hooks'
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

  const syncMutation = useSourceSync(kbSlug, source)
  const deleteMutation = useSourceDelete(kbSlug, source)
  const renameMutation = useSourceRename(kbSlug, source, () => setIsRenaming(false))
  const reauth = useSourceReauth(kbSlug, source)

  // Close edit mode when rename mutation finishes (success OR error).
  // Pattern from portal-frontend.md: useRef + useEffect, never setIsRenaming
  // inside onSuccess (that races with InlineEdit's blur handler).
  const wasRenaming = useRef(false)
  useEffect(() => {
    if (wasRenaming.current && !renameMutation.isPending) {
      setIsRenaming(false)
    }
    wasRenaming.current = renameMutation.isPending
  }, [renameMutation.isPending])

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

  const isAuthError = source.kind === 'connector' && (source.status ?? '').toLowerCase().includes('auth')
  const isSyncing = syncMutation.isPending || status === 'pending'
  const syncDisabled = isSyncing || isAuthError
  const isDeleting = deleteMutation.isPending

  // Meta line: type label, optional item count for connectors. Drop the
  // chunk count — the parent_chunks number is unreliable per-row.
  const metaParts: string[] = [source.type_label]
  if (source.kind === 'connector' && source.items_count > 0) {
    metaParts.push(`${source.items_count} items`)
  }
  const meta = metaParts.join(' · ')

  return (
    <div>
      <div className="group flex items-center gap-2 pr-2 hover:bg-black/[0.03] transition-colors">
        <div className="flex flex-1 min-w-0 items-center gap-3 px-2 py-3.5">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gray-50 text-gray-400">
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

        {/* Actions cell. When renaming, the default icons fade out and the
            Save/Cancel buttons overlay — same width, no layout shift. */}
        <div className="relative flex items-center">
          <div className={`flex items-center ${isRenaming ? 'opacity-0 pointer-events-none' : ''}`}>
            {/* "Verbind opnieuw" — only for connectors in auth_error state. */}
            {isAuthError && (
              <Tooltip label="Verbind opnieuw met de externe dienst">
                <button
                  type="button"
                  onClick={() => void reauth.start()}
                  disabled={reauth.pending}
                  aria-label="Verbind opnieuw"
                  className="inline-flex h-8 items-center gap-1.5 px-2 rounded-md text-xs font-medium text-[var(--color-rl-accent-dark)] hover:bg-gray-100 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {reauth.pending ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <LinkIcon className="h-3.5 w-3.5" />
                  )}
                  Verbind opnieuw
                </button>
              </Tooltip>
            )}

            {/* Sync / reindex. Auth_error blocks the connector branch. */}
            <Tooltip
              label={
                isAuthError
                  ? 'Eerst opnieuw verbinden'
                  : source.kind === 'upload'
                    ? 'Herindexeer bron'
                    : 'Synchroniseer bron'
              }
            >
              <button
                type="button"
                onClick={() => { if (!syncDisabled) syncMutation.mutate() }}
                disabled={syncDisabled}
                aria-label={
                  isAuthError
                    ? 'Eerst opnieuw verbinden'
                    : source.kind === 'upload'
                      ? 'Herindexeer bron'
                      : 'Synchroniseer bron'
                }
                className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isSyncing ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <RefreshCw className="h-4 w-4" />
                )}
              </button>
            </Tooltip>

            {/* Delete — inline-confirm pattern. */}
            <InlineDeleteConfirm
              isConfirming={confirmingDelete}
              isPending={isDeleting}
              label={`Verwijder '${source.name}'?`}
              cancelLabel="Annuleren"
              onConfirm={() => { deleteMutation.mutate(); setConfirmingDelete(false) }}
              onCancel={() => setConfirmingDelete(false)}
            >
              <Tooltip label="Verwijder bron">
                <button
                  type="button"
                  onClick={() => setConfirmingDelete(true)}
                  disabled={isDeleting}
                  aria-label="Verwijder bron"
                  className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-400 hover:text-[var(--color-destructive)] hover:bg-gray-100 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </Tooltip>
            </InlineDeleteConfirm>

            {/* REQ-6 — Connector edit uses Settings icon, not Pencil. */}
            {source.kind === 'connector' && (
              <Tooltip label="Bewerk koppeling">
                <Link
                  to="/app/knowledge/$kbSlug/edit-connector/$connectorId"
                  params={{ kbSlug, connectorId: source.id }}
                  aria-label="Bewerk koppeling"
                  className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-colors"
                >
                  <Settings className="h-4 w-4" />
                </Link>
              </Tooltip>
            )}

            {/* REQ-5 — Upload rename uses Pencil. */}
            {source.kind === 'upload' && (
              <Tooltip label="Naam aanpassen">
                <button
                  type="button"
                  onClick={startRename}
                  aria-label="Naam aanpassen"
                  className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-colors"
                >
                  <Pencil className="h-4 w-4" />
                </button>
              </Tooltip>
            )}

            {/* REQ-7 — Docs-editor link uses NotebookPen, not Pencil. */}
            {editablePageId !== null && (
              <Tooltip label="Bewerken in editor">
                <Link
                  to="/app/docs/$kbSlug/$pageId"
                  params={{ kbSlug, pageId: editablePageId }}
                  aria-label="Bewerken in editor"
                  className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-colors"
                >
                  <NotebookPen className="h-4 w-4" />
                </Link>
              </Tooltip>
            )}

            <button
              type="button"
              onClick={onToggle}
              aria-label={expanded ? 'Inhoud verbergen' : 'Inhoud tonen'}
              className="inline-flex h-8 w-8 items-center justify-center text-gray-300 hover:text-gray-500 transition-colors"
            >
              <ChevronRight
                className={`h-4 w-4 shrink-0 transition-transform ${expanded ? 'rotate-90' : ''}`}
              />
            </button>
          </div>

          {/* Save / Cancel overlay, only when renaming. */}
          {isRenaming && (
            <div className="absolute inset-y-0 right-0 z-10 flex items-center gap-1 whitespace-nowrap">
              <Button
                size="sm"
                className="h-6 text-[10px] px-2 gap-1 [&_svg]:size-2.5 bg-[var(--color-success)] text-white hover:opacity-70"
                disabled={renameMutation.isPending || !draftName.trim()}
                onClick={saveRename}
              >
                {renameMutation.isPending ? <Loader2 className="animate-spin" /> : <Check />}
                Opslaan
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-6 text-[10px] px-2 gap-1 [&_svg]:size-2.5"
                onClick={cancelRename}
              >
                <X />
                Annuleren
              </Button>
            </div>
          )}
        </div>
      </div>
      {expanded && <SourceContent kbSlug={kbSlug} source={source} />}
    </div>
  )
}
