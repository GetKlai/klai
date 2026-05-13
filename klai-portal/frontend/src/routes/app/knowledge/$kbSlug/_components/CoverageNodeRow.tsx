/**
 * One coverage row in the taxonomy widget: node name, percentage bar,
 * inline rename form, inline delete confirmation.
 *
 * Extracted from CoverageWidget.tsx by SPEC-PORTAL-TAXONOMY-SPLIT-001
 * polish round. Same singleton pattern as ProposalCard:
 *   - Parent (CoverageWidget) owns `editingNodeId` + `confirmDeleteId`
 *     so only ONE row may be in edit OR delete-confirm at any time.
 *   - This component owns the per-row buffer state (`editingName`,
 *     `editingDescription`), initialised when `isEditing` flips from
 *     false to true via a useRef transition guard. The guard prevents
 *     TanStack Query refetches (window-focus, mutation invalidation)
 *     from wiping the user's typed input — same regression class as
 *     ProposalCard's bug-fixed in v0.2.1.
 */
import { useEffect, useRef, useState } from 'react'
import { Pencil, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'
import type { TaxonomyCoverageNode } from '../-kb-types'

/**
 * Coverage percentage threshold for the "healthy" green bar. Below this
 * the bar renders amber to flag categories with too little data. Mirrors
 * the backend's coverage-health threshold (5% of total chunks).
 */
const HEALTH_PERCENTAGE_THRESHOLD = 5

function barColor(pct: number): string {
  return pct >= HEALTH_PERCENTAGE_THRESHOLD
    ? 'bg-[var(--color-success)]'
    : 'bg-amber-400'
}

export interface CoverageNodeRowProps {
  node: TaxonomyCoverageNode
  /** Total chunks across all nodes — denominator for the percentage. */
  totalChunks: number
  isActive: boolean
  isEditing: boolean
  isConfirmingDelete: boolean
  canEdit: boolean
  onNodeClick: () => void
  onStartEdit: () => void
  onSubmitEdit: (name: string, description: string) => void
  onCancelEdit: () => void
  onStartDelete: () => void
  onConfirmDelete: () => void
  onCancelDelete: () => void
}

export function CoverageNodeRow({
  node,
  totalChunks,
  isActive,
  isEditing,
  isConfirmingDelete,
  canEdit,
  onNodeClick,
  onStartEdit,
  onSubmitEdit,
  onCancelEdit,
  onStartDelete,
  onConfirmDelete,
  onCancelDelete,
}: CoverageNodeRowProps) {
  const [editingName, setEditingName] = useState(node.taxonomy_node_name)
  const [editingDescription, setEditingDescription] = useState(node.description ?? '')

  // Initialise edit buffers ONLY on the false → true transition for
  // isEditing — not on every re-render while editing is active. Same
  // pattern as ProposalCard: prevents query-refetch object-ref changes
  // from overwriting typed input.
  const prevIsEditing = useRef(false)
  useEffect(() => {
    if (isEditing && !prevIsEditing.current) {
      setEditingName(node.taxonomy_node_name)
      setEditingDescription(node.description ?? '')
    }
    prevIsEditing.current = isEditing
    // Deliberately omit node.* from deps — see comment above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isEditing])

  const pct = totalChunks > 0 ? Math.round((node.chunk_count / totalChunks) * 100) : 0

  function handleSubmit() {
    const trimmedName = editingName.trim()
    if (trimmedName) {
      onSubmitEdit(trimmedName, editingDescription.trim())
    }
  }

  return (
    <div
      className={[
        'group/row w-full text-left rounded-lg border p-3 transition-colors cursor-pointer',
        isActive
          ? 'border-gray-200 bg-black/[0.06]'
          : 'border-gray-200 hover:bg-gray-50',
      ].join(' ')}
      onClick={() => { if (!isEditing && !isConfirmingDelete) onNodeClick() }}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === 'Enter' && !isEditing) onNodeClick() }}
    >
      <form
        onSubmit={(e) => { e.preventDefault(); if (isEditing) handleSubmit() }}
        onClick={(e) => { if (isEditing) e.stopPropagation() }}
      >
        <div className="flex items-center justify-between mb-1.5 gap-2">
          {isEditing ? (
            <input
              value={editingName}
              onChange={(e) => setEditingName(e.target.value)}
              className="text-sm font-medium text-gray-900 bg-[var(--color-card)] border border-gray-200 focus:border-gray-200 ring-0 focus:ring-1 focus:ring-[var(--color-accent)] rounded-md py-0.5 px-1.5 flex-1 min-w-0 outline-none"
              autoFocus
              onKeyDown={(e) => { if (e.key === 'Escape') onCancelEdit() }}
            />
          ) : (
            <span className="text-sm font-medium text-gray-900 truncate">
              {node.taxonomy_node_name}
            </span>
          )}
          <div className="flex items-center gap-1.5 shrink-0">
            {canEdit && !isEditing && !isConfirmingDelete && (
              <span className="inline-flex items-center gap-0.5">
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); onStartEdit() }}
                  className="flex h-5 w-5 items-center justify-center text-[var(--color-warning)] hover:opacity-70 transition-opacity"
                  aria-label={m.knowledge_taxonomy_node_rename()}
                >
                  <Pencil className="h-3 w-3" />
                </button>
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); onStartDelete() }}
                  className="flex h-5 w-5 items-center justify-center text-[var(--color-destructive)] hover:opacity-70 transition-opacity"
                  aria-label={m.knowledge_taxonomy_node_delete()}
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              </span>
            )}
            {isConfirmingDelete && (
              <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                <Button
                  size="sm"
                  className="h-6 text-[10px] px-2 gap-1 [&_svg]:size-2.5 bg-[var(--color-destructive)] text-white hover:opacity-70"
                  onClick={onConfirmDelete}
                >
                  {m.knowledge_taxonomy_node_delete()}
                </Button>
                <Button size="sm" variant="ghost" className="h-6 text-[10px] px-2" onClick={onCancelDelete}>
                  {m.knowledge_taxonomy_node_add_cancel()}
                </Button>
              </div>
            )}
            {isEditing && (
              <span className="inline-flex items-center gap-1">
                <Button type="submit" size="sm" className="h-6 text-xs px-2" disabled={!editingName.trim()}>
                  {m.knowledge_taxonomy_node_edit_submit()}
                </Button>
                <Button type="button" size="sm" variant="ghost" className="h-6 text-xs px-2" onClick={onCancelEdit}>
                  {m.knowledge_taxonomy_node_add_cancel()}
                </Button>
              </span>
            )}
            {!isConfirmingDelete && !isEditing && (
              <span className="text-xs text-gray-400 tabular-nums">
                {pct}%
              </span>
            )}
          </div>
        </div>
        {isEditing ? (
          <textarea
            value={editingDescription}
            onChange={(e) => setEditingDescription(e.target.value)}
            className="text-xs text-gray-400 bg-[var(--color-card)] border border-gray-200 focus:border-gray-200 ring-0 focus:ring-1 focus:ring-[var(--color-accent)] rounded-md py-1 px-1.5 mb-1 w-full outline-none resize-none"
            rows={2}
            placeholder={m.knowledge_taxonomy_node_description_placeholder()}
            onKeyDown={(e) => { if (e.key === 'Escape') onCancelEdit() }}
          />
        ) : node.description ? (
          <p className="text-xs text-gray-400 mb-1 line-clamp-2">
            {node.description}
          </p>
        ) : null}
      </form>
      <div className="h-1.5 w-full rounded-full bg-gray-200 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${barColor(pct)}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="flex items-center gap-3 mt-1.5">
        <span className="text-xs text-gray-400">
          {m.knowledge_taxonomy_coverage_chunks({ count: String(node.chunk_count) })}
        </span>
        {node.gap_count > 0 && (
          <span className="text-xs text-amber-600">
            {m.knowledge_taxonomy_coverage_gaps({ count: String(node.gap_count) })}
          </span>
        )}
      </div>
    </div>
  )
}
