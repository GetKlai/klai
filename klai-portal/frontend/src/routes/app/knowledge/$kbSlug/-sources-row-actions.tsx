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
 *
 * The six icon-only affordances all share the same h-8/w-8 rounded
 * button shell with hover + disabled states; extracted into the
 * `RowActionIconButton` component below so each individual action site
 * only owns its label, icon, and click handler.
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
import type { ComponentType, ReactNode } from 'react'
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

interface RowActionIconButtonProps {
  label: string
  icon: ComponentType<{ className?: string }>
  onClick?: () => void
  disabled?: boolean
  /** Optional element to replace the icon while loading (e.g. spinner). */
  spinner?: ReactNode
  /** Style variant — `destructive` paints hover red. */
  variant?: 'default' | 'destructive'
}

/** Shared icon-only h-8/w-8 button + tooltip + aria-label, the canonical
 *  affordance for any per-row action in the Sources tab. */
function RowActionIconButton({
  label,
  icon: Icon,
  onClick,
  disabled,
  spinner,
  variant = 'default',
}: RowActionIconButtonProps) {
  const hoverClass = variant === 'destructive'
    ? 'hover:text-[var(--color-destructive)]'
    : 'hover:text-gray-900'
  return (
    <Tooltip label={label}>
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        aria-label={label}
        className={`inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-400 ${hoverClass} hover:bg-gray-100 transition-colors disabled:opacity-50 disabled:cursor-not-allowed`}
      >
        {spinner ?? <Icon className="h-4 w-4" />}
      </button>
    </Tooltip>
  )
}

/** Link-shaped sibling of `RowActionIconButton` — same shell, but the
 *  target is a TanStack Router `<Link>` rather than a click handler. */
function RowActionIconLink({
  label,
  icon: Icon,
  to,
  params,
}: {
  label: string
  icon: ComponentType<{ className?: string }>
  to: string
  // TanStack params are route-strict; we accept the runtime shape and let
  // the caller pin its own typing.
  params: Record<string, string>
}) {
  return (
    <Tooltip label={label}>
      <Link
        to={to}
        params={params}
        aria-label={label}
        className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-colors"
      >
        <Icon className="h-4 w-4" />
      </Link>
    </Tooltip>
  )
}

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
      {/* Reauth is the only non-icon-only action — it carries a label so
          the affordance is unambiguous when an auth_error appears. */}
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

      <RowActionIconButton
        label={syncTooltip}
        icon={RefreshCw}
        onClick={() => { if (!syncDisabled) syncMutation.mutate() }}
        disabled={syncDisabled}
        spinner={isSyncing ? <Loader2 className="h-4 w-4 animate-spin" /> : undefined}
      />

      <InlineDeleteConfirm
        isConfirming={confirmingDelete}
        isPending={isDeleting}
        label={m.kb_sources_row_delete_confirm({ name: source.name })}
        cancelLabel={m.kb_sources_row_cancel()}
        onConfirm={() => { deleteMutation.mutate(); onSetConfirmingDelete(false) }}
        onCancel={() => onSetConfirmingDelete(false)}
      >
        <RowActionIconButton
          label={m.kb_sources_row_delete_tooltip()}
          icon={Trash2}
          onClick={() => onSetConfirmingDelete(true)}
          disabled={isDeleting}
          variant="destructive"
        />
      </InlineDeleteConfirm>

      {source.kind === 'connector' && (
        <RowActionIconLink
          label={m.kb_sources_row_edit_connector_tooltip()}
          icon={Settings}
          to="/app/knowledge/$kbSlug/edit-connector/$connectorId"
          params={{ kbSlug, connectorId: source.id }}
        />
      )}

      {source.kind === 'upload' && (
        <RowActionIconButton
          label={m.kb_sources_row_rename_tooltip()}
          icon={Pencil}
          onClick={onStartRename}
        />
      )}

      {editablePageId !== null && (
        <RowActionIconLink
          label={m.kb_sources_row_open_in_editor()}
          icon={NotebookPen}
          to="/app/docs/$kbSlug/$pageId"
          params={{ kbSlug, pageId: editablePageId }}
        />
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
