/**
 * Action cluster for one Sources-tab row.
 *
 * Owns: reauth · sync · delete · connector-edit · upload-rename ·
 * docs-editor · drill-down chevron. Mutations live in `-sources-hooks.ts`.
 *
 * Layout: a single horizontal flex row. When the parent indicates that
 * the row is in rename mode (`isRenaming=true`), the entire cluster
 * fades out + becomes non-interactive so the Save/Cancel overlay can
 * occupy the same width without layout shift. The actual Save/Cancel
 * controls live in `-sources-row.tsx` because they share state with
 * the `<InlineEdit>` overlay.
 */
import { Link } from '@tanstack/react-router'
import {
  ChevronRight,
  Link as LinkIcon,
  Loader2,
  NotebookPen,
  Pencil,
  RefreshCw,
  Settings,
  Trash2,
} from 'lucide-react'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import { Tooltip } from '@/components/ui/tooltip'
import * as m from '@/paraglide/messages'
import { mapSourceStatus } from './-sources-helpers'
import {
  useSourceDelete,
  useSourceReauth,
  useSourceSync,
} from './-sources-hooks'
import type { Source } from './-sources-types'

interface SourceRowActionsProps {
  source: Source
  kbSlug: string
  editablePageId: string | null
  isRenaming: boolean
  onStartRename: () => void
  expanded: boolean
  onToggle: () => void
  confirmingDelete: boolean
  onSetConfirmingDelete: (v: boolean) => void
}

export function SourceRowActions({
  source,
  kbSlug,
  editablePageId,
  isRenaming,
  onStartRename,
  expanded,
  onToggle,
  confirmingDelete,
  onSetConfirmingDelete,
}: SourceRowActionsProps) {
  const syncMutation = useSourceSync(kbSlug, source)
  const deleteMutation = useSourceDelete(kbSlug, source)
  const reauth = useSourceReauth(kbSlug, source)

  const isAuthError = source.kind === 'connector' && (source.status ?? '').toLowerCase().includes('auth')
  const isSyncing = syncMutation.isPending || mapSourceStatus(source) === 'pending'
  const syncDisabled = isSyncing || isAuthError
  const isDeleting = deleteMutation.isPending

  const syncTooltip = isAuthError
    ? m.kb_sources_row_sync_blocked_auth()
    : source.kind === 'upload'
      ? m.kb_sources_row_reindex_tooltip()
      : m.kb_sources_row_sync_tooltip()

  return (
    <div className={`flex items-center ${isRenaming ? 'opacity-0 pointer-events-none' : ''}`}>
      {isAuthError && (
        <Tooltip label={m.kb_sources_row_reauth_tooltip()}>
          <button
            type="button"
            onClick={() => void reauth.start()}
            disabled={reauth.pending}
            aria-label={m.kb_sources_row_reauth_label()}
            className="inline-flex h-8 items-center gap-1.5 px-2 rounded-md text-xs font-medium text-[var(--color-rl-accent-dark)] hover:bg-gray-100 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {reauth.pending
              ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
              : <LinkIcon className="h-3.5 w-3.5" />}
            {m.kb_sources_row_reauth_label()}
          </button>
        </Tooltip>
      )}

      <Tooltip label={syncTooltip}>
        <button
          type="button"
          onClick={() => { if (!syncDisabled) syncMutation.mutate() }}
          disabled={syncDisabled}
          aria-label={syncTooltip}
          className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {isSyncing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
        </button>
      </Tooltip>

      <InlineDeleteConfirm
        isConfirming={confirmingDelete}
        isPending={isDeleting}
        label={m.kb_sources_row_delete_confirm({ name: source.name })}
        cancelLabel={m.kb_sources_row_cancel()}
        onConfirm={() => { deleteMutation.mutate(); onSetConfirmingDelete(false) }}
        onCancel={() => onSetConfirmingDelete(false)}
      >
        <Tooltip label={m.kb_sources_row_delete_tooltip()}>
          <button
            type="button"
            onClick={() => onSetConfirmingDelete(true)}
            disabled={isDeleting}
            aria-label={m.kb_sources_row_delete_tooltip()}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-400 hover:text-[var(--color-destructive)] hover:bg-gray-100 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </Tooltip>
      </InlineDeleteConfirm>

      {source.kind === 'connector' && (
        <Tooltip label={m.kb_sources_row_edit_connector_tooltip()}>
          <Link
            to="/app/knowledge/$kbSlug/edit-connector/$connectorId"
            params={{ kbSlug, connectorId: source.id }}
            aria-label={m.kb_sources_row_edit_connector_tooltip()}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-colors"
          >
            <Settings className="h-4 w-4" />
          </Link>
        </Tooltip>
      )}

      {source.kind === 'upload' && (
        <Tooltip label={m.kb_sources_row_rename_tooltip()}>
          <button
            type="button"
            onClick={onStartRename}
            aria-label={m.kb_sources_row_rename_tooltip()}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-colors"
          >
            <Pencil className="h-4 w-4" />
          </button>
        </Tooltip>
      )}

      {editablePageId !== null && (
        <Tooltip label={m.kb_sources_row_open_in_editor()}>
          <Link
            to="/app/docs/$kbSlug/$pageId"
            params={{ kbSlug, pageId: editablePageId }}
            aria-label={m.kb_sources_row_open_in_editor()}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-colors"
          >
            <NotebookPen className="h-4 w-4" />
          </Link>
        </Tooltip>
      )}

      <button
        type="button"
        onClick={onToggle}
        aria-label={expanded ? m.kb_sources_row_hide_content() : m.kb_sources_row_show_content()}
        className="inline-flex h-8 w-8 items-center justify-center text-gray-300 hover:text-gray-500 transition-colors"
      >
        <ChevronRight className={`h-4 w-4 shrink-0 transition-transform ${expanded ? 'rotate-90' : ''}`} />
      </button>
    </div>
  )
}
