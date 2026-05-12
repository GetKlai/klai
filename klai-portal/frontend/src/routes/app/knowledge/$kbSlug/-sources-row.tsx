/**
 * One row in the Sources tab list.
 *
 * Owns: icon · name · meta line · status badge · per-row actions
 * (reauth / sync · delete · rename · open-in-editor) · inline rename
 * overlay · drill-down toggle. Mutations live in `-sources-hooks.ts`.
 *
 * Phase 4 will replace the hand-rolled inline rename with the canonical
 * `<InlineEdit>` primitive and disambiguate the pencil icon between
 * connector-config edit (Settings icon) and docs-editor open
 * (NotebookPen icon). The current contents preserve the post-PR-#574
 * behaviour 1:1 so the extraction is purely structural.
 */
import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import {
  Check,
  ChevronRight,
  Link as LinkIcon,
  Loader2,
  Pencil,
  RefreshCw,
  Trash2,
  X,
} from 'lucide-react'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
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
            {isRenaming ? (
              <div className="flex items-center gap-1 min-w-[220px] max-w-full">
                <input
                  value={draftName}
                  autoFocus
                  onChange={(e) => setDraftName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Escape') {
                      setDraftName(source.name)
                      setIsRenaming(false)
                    }
                    if (e.key === 'Enter' && draftName.trim()) {
                      renameMutation.mutate(draftName.trim())
                    }
                  }}
                  className="h-8 min-w-0 flex-1 rounded-md border border-gray-200 bg-white px-2 text-sm text-gray-900 outline-none focus:border-gray-400"
                />
                <button
                  type="button"
                  aria-label="Naam opslaan"
                  disabled={!draftName.trim() || renameMutation.isPending}
                  onClick={() => {
                    if (draftName.trim()) renameMutation.mutate(draftName.trim())
                  }}
                  className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-400 hover:text-gray-900 hover:bg-gray-100 disabled:opacity-50"
                >
                  {renameMutation.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Check className="h-4 w-4" />
                  )}
                </button>
                <button
                  type="button"
                  aria-label="Naam bewerken annuleren"
                  onClick={() => {
                    setDraftName(source.name)
                    setIsRenaming(false)
                  }}
                  className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-400 hover:text-gray-900 hover:bg-gray-100"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            ) : (
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
            )}
          </div>
        </div>

        <StatusBadge status={status} />

        {/* "Verbind opnieuw" — only for connectors in auth_error state. */}
        {isAuthError && (
          <div className="flex flex-col items-end gap-0.5">
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
            {reauth.error && (
              <span className="text-[10px] text-[var(--color-destructive)] px-2">
                Verbinden mislukt
              </span>
            )}
          </div>
        )}

        {/* Sync / reindex — both kinds. Auth_error blocks the connector branch. */}
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

        {/* Delete — inline-confirm pattern, always visible. */}
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

        {source.kind === 'connector' && (
          <Tooltip label="Bewerk bron">
            <Link
              to="/app/knowledge/$kbSlug/edit-connector/$connectorId"
              params={{ kbSlug, connectorId: source.id }}
              aria-label="Bewerk bron"
              className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-colors"
            >
              <Pencil className="h-4 w-4" />
            </Link>
          </Tooltip>
        )}

        {source.kind === 'upload' && (
          <Tooltip label="Naam aanpassen">
            <button
              type="button"
              onClick={() => {
                setDraftName(source.name)
                setIsRenaming(true)
              }}
              aria-label="Naam aanpassen"
              className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-colors"
            >
              <Pencil className="h-4 w-4" />
            </button>
          </Tooltip>
        )}

        {/* Per-row "Bewerken in editor" — only rendered when the source name
            actually maps to a Gitea page slug in the KB's page-index. The
            mapping is computed once at KB level (see SourcesTab) so each row
            only renders when there's a confirmed click target. */}
        {editablePageId !== null && (
          <Tooltip label="Bewerken in editor">
            <Link
              to="/app/docs/$kbSlug/$pageId"
              params={{ kbSlug, pageId: editablePageId }}
              aria-label="Bewerken in editor"
              className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-colors"
            >
              <Pencil className="h-4 w-4" />
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
      {expanded && <SourceContent kbSlug={kbSlug} source={source} />}
    </div>
  )
}
