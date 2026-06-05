/**
 * Action cluster for one Sources-tab row.
 *
 * Owns: drill-down toggle · sync · reauth · docs-editor · connector-edit ·
 * upload-rename · delete. Mutations live in `-sources-hooks.ts`.
 *
 * Layout: a fixed direct action cluster (toggle, sync, more). Lower-frequency
 * actions move into the more menu once the row has more than three controls.
 * When the parent indicates rename mode (`isRenaming=true`), the entire
 * cluster fades out + becomes non-interactive so the Save/Cancel overlay can
 * occupy the same width without layout shift. The actual Save/Cancel controls
 * live in `-sources-row.tsx` because they share state with `<InlineEdit>`.
 *
 * Visible icon affordances use the shared bordered row-action shell so
 * sources match admin divider-list actions.
 */
import { useNavigate } from '@tanstack/react-router'
import { Link as LinkIcon, Loader2, NotebookPen, Pencil, Settings, Trash2 } from 'lucide-react'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import {
  BorderedRowActionIconButton,
  RowActionGroup,
} from '@/components/ui/row-action'
import * as m from '@/paraglide/messages'
import { shouldPollSource } from './-sources-helpers'
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
  const navigate = useNavigate()
  const syncMutation = useSourceSync(kbSlug, source)
  const deleteMutation = useSourceDelete(kbSlug, source)
  const reauth = useSourceReauth(kbSlug, source)

  const isAuthError = source.kind === 'connector' && (source.status ?? '').toLowerCase().includes('auth')
  const isSyncing = syncMutation.isPending || shouldPollSource(source)
  const syncDisabled = isSyncing || isAuthError
  const isDeleting = deleteMutation.isPending

  const syncTooltip = isAuthError
    ? m.kb_sources_row_sync_blocked_auth()
    : source.kind === 'upload'
      ? m.kb_sources_row_reindex_tooltip()
      : m.kb_sources_row_sync_tooltip()

  return (
    <InlineDeleteConfirm
      isConfirming={confirmingDelete}
      isPending={isDeleting}
      label={m.kb_sources_row_delete_confirm({ name: source.name })}
      cancelLabel={m.kb_sources_row_cancel()}
      onConfirm={() => { deleteMutation.mutate(); onSetConfirmingDelete(false) }}
      onCancel={() => onSetConfirmingDelete(false)}
    >
      <RowActionGroup className={isRenaming ? 'opacity-0 pointer-events-none' : undefined}>
        <BorderedRowActionIconButton
          label={expanded ? m.kb_sources_row_hide_content() : m.kb_sources_row_show_content()}
          action={expanded ? 'collapse' : 'expand'}
          onClick={onToggle}
        />

        <BorderedRowActionIconButton
          label={syncTooltip}
          action="sync"
          onClick={() => { if (!syncDisabled) syncMutation.mutate() }}
          disabled={syncDisabled}
          spinner={isSyncing ? <Loader2 className="h-4 w-4 animate-spin" /> : undefined}
        />

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <BorderedRowActionIconButton
              label={m.kb_sources_row_more_actions()}
              action="more"
            />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="min-w-44">
            {isAuthError && (
              <DropdownMenuItem
                disabled={reauth.pending}
                onSelect={() => void reauth.start()}
              >
                {reauth.pending ? <Loader2 className="animate-spin" /> : <LinkIcon />}
                {m.kb_sources_row_reauth_label()}
              </DropdownMenuItem>
            )}

            {editablePageId !== null && (
              <DropdownMenuItem
                onSelect={() =>
                  void navigate({
                    to: '/app/docs/$kbSlug/$pageId',
                    params: { kbSlug, pageId: editablePageId },
                  })
                }
              >
                <NotebookPen />
                {m.kb_sources_row_open_in_editor()}
              </DropdownMenuItem>
            )}

            {source.kind === 'connector' && (
              <DropdownMenuItem
                onSelect={() =>
                  void navigate({
                    to: '/app/knowledge/$kbSlug/edit-connector/$connectorId',
                    params: { kbSlug, connectorId: source.id },
                  })
                }
              >
                <Settings />
                {m.kb_sources_row_edit_connector_tooltip()}
              </DropdownMenuItem>
            )}

            {source.kind === 'upload' && (
              <DropdownMenuItem onSelect={onStartRename}>
                <Pencil />
                {m.kb_sources_row_rename_tooltip()}
              </DropdownMenuItem>
            )}

            <DropdownMenuSeparator />
            <DropdownMenuItem
              disabled={isDeleting}
              onSelect={() => onSetConfirmingDelete(true)}
              className="text-[var(--color-destructive)] focus:text-[var(--color-destructive)]"
            >
              <Trash2 />
              {m.kb_sources_row_delete_tooltip()}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </RowActionGroup>
    </InlineDeleteConfirm>
  )
}
