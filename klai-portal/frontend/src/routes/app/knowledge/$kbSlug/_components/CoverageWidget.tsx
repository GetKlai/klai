/**
 * Coverage widget — per-node coverage bars + inline rename/delete
 * actions + a contextual "Suggest categories" CTA.
 *
 * Extracted from TaxonomyTab.tsx by SPEC-PORTAL-TAXONOMY-SPLIT-001
 * commit 3. Same prop signature; same internal state machine for
 * editing and delete-confirmation singletons (one node may be in
 * edit-mode OR delete-confirm at any time, never both, never two).
 *
 * The Suggest button has three gates baked in:
 *   1. Empty KB: shown when total chunks >= 10.
 *   2. Populated KB at IA target: hidden once node count reaches
 *      MAX_HEALTHY_NODE_COUNT (Miller's Law 5-9 — more categories
 *      makes the taxonomy worse).
 *   3. Populated KB below target: shown when untagged_count >= 10
 *      AND untagged percentage > 5%.
 */
import { useState } from 'react'
import { Loader2, Pencil, Sparkles, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'
import type { TaxonomyCoverage } from '../-kb-types'

// SPEC-TAXONOMY-REVIEW-FLOW-001 follow-up: cap on healthy taxonomy size.
// Mirrors the backend's ``taxonomy_consolidate_target_max`` (default 9).
// When the KB already has this many root taxonomy nodes, hide the
// "Suggest categories" affordance — Miller's Law makes more categories
// counter-productive (see SPEC-TAXONOMY-MERGE-DETECT-001 motivation).
// If the backend value drifts, revisit this constant.
const MAX_HEALTHY_NODE_COUNT = 9

export interface CoverageWidgetProps {
  coverage: TaxonomyCoverage
  activeNodeId: number | null
  onNodeClick: (nodeId: number) => void
  onSuggest?: () => void
  isSuggesting?: boolean
  /**
   * SPEC-TAXONOMY-REVIEW-FLOW-001 follow-up: while backfill is running
   * the Suggest button is hidden and an inline categorising indicator
   * is shown instead.
   */
  isBackfilling?: boolean
  canEdit?: boolean
  onRename?: (nodeId: number, newName: string, description?: string) => void
  onDelete?: (nodeId: number) => void
}

export function CoverageWidget({
  coverage,
  activeNodeId,
  onNodeClick,
  onSuggest,
  isSuggesting,
  isBackfilling,
  canEdit,
  onRename,
  onDelete,
}: CoverageWidgetProps) {
  const total = coverage.total_chunks
  const [editingNodeId, setEditingNodeId] = useState<number | null>(null)
  const [editingName, setEditingName] = useState('')
  const [editingDescription, setEditingDescription] = useState('')
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null)

  const barColor = (pct: number) => {
    if (pct >= 5) return 'bg-[var(--color-success)]'
    return 'bg-amber-400'
  }

  function startEdit(nodeId: number, currentName: string, currentDescription: string): void {
    setEditingNodeId(nodeId)
    setEditingName(currentName)
    setEditingDescription(currentDescription)
    setConfirmDeleteId(null)
  }

  function submitEdit(): void {
    if (editingNodeId !== null && editingName.trim() && onRename) {
      onRename(editingNodeId, editingName.trim(), editingDescription.trim())
    }
    setEditingNodeId(null)
    setEditingName('')
    setEditingDescription('')
  }

  function cancelEdit(): void {
    setEditingNodeId(null)
    setEditingName('')
    setEditingDescription('')
  }

  if (coverage.nodes.length === 0) {
    // Empty-state: KB has no taxonomy nodes yet. When chunks exist (>= 10) we
    // surface the Suggest CTA here too — without it the user faces a catch-22:
    // no Suggest button until nodes exist, no nodes until Suggest is clicked.
    // Threshold mirrors the populated-coverage Suggest gate below (>= 10
    // untagged chunks); for an empty KB every chunk counts as untagged.
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-400">
          {m.knowledge_taxonomy_coverage_empty()}
        </p>
        {isBackfilling ? (
          <div className="inline-flex items-center gap-1.5 text-xs text-gray-400">
            <Loader2 className="h-3 w-3 animate-spin" />
            <span>{m.knowledge_taxonomy_categorising_status()}</span>
          </div>
        ) : (
          onSuggest && total >= 10 && (
            <div className="space-y-1.5">
              <button
                type="button"
                onClick={onSuggest}
                disabled={isSuggesting}
                className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full font-medium bg-gray-900 text-white hover:opacity-90 transition-opacity disabled:opacity-50"
              >
                {isSuggesting
                  ? <><Loader2 className="h-3 w-3 animate-spin" />{m.knowledge_taxonomy_suggest_generating()}</>
                  : <><Sparkles className="h-3 w-3" />{m.knowledge_taxonomy_suggest_categories()}</>
                }
              </button>
              {isSuggesting && (
                <p className="text-xs text-gray-400">
                  {m.knowledge_taxonomy_suggest_loading_hint()}
                </p>
              )}
            </div>
          )
        )}
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {coverage.nodes.map((node) => {
        const pct = total > 0 ? Math.round((node.chunk_count / total) * 100) : 0
        const isActive = activeNodeId === node.taxonomy_node_id
        const isEditing = editingNodeId === node.taxonomy_node_id
        const isConfirmingDelete = confirmDeleteId === node.taxonomy_node_id
        return (
          <div
            key={node.taxonomy_node_id}
            className={[
              'group/row w-full text-left rounded-lg border p-3 transition-colors cursor-pointer',
              isActive
                ? 'border-gray-200 bg-black/[0.06]'
                : 'border-gray-200 hover:bg-gray-50',
            ].join(' ')}
            onClick={() => { if (!isEditing && !isConfirmingDelete) onNodeClick(node.taxonomy_node_id) }}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => { if (e.key === 'Enter' && !isEditing) onNodeClick(node.taxonomy_node_id) }}
          >
            <form
              onSubmit={(e) => { e.preventDefault(); if (isEditing) submitEdit() }}
              onClick={(e) => { if (isEditing) e.stopPropagation() }}
            >
              <div className="flex items-center justify-between mb-1.5 gap-2">
                {isEditing ? (
                  <input
                    value={editingName}
                    onChange={(e) => setEditingName(e.target.value)}
                    className="text-sm font-medium text-gray-900 bg-[var(--color-card)] border border-gray-200 focus:border-gray-200 ring-0 focus:ring-1 focus:ring-[var(--color-accent)] rounded-md py-0.5 px-1.5 flex-1 min-w-0 outline-none"
                    autoFocus
                    onKeyDown={(e) => { if (e.key === 'Escape') cancelEdit() }}
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
                        onClick={(e) => { e.stopPropagation(); startEdit(node.taxonomy_node_id, node.taxonomy_node_name, node.description ?? '') }}
                        className="flex h-5 w-5 items-center justify-center text-[var(--color-warning)] hover:opacity-70 transition-opacity"
                        aria-label={m.knowledge_taxonomy_node_rename()}
                      >
                        <Pencil className="h-3 w-3" />
                      </button>
                      <button
                        type="button"
                        onClick={(e) => { e.stopPropagation(); setConfirmDeleteId(node.taxonomy_node_id) }}
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
                        onClick={() => { onDelete?.(node.taxonomy_node_id); setConfirmDeleteId(null) }}
                      >
                        {m.knowledge_taxonomy_node_delete()}
                      </Button>
                      <Button size="sm" variant="ghost" className="h-6 text-[10px] px-2" onClick={() => setConfirmDeleteId(null)}>
                        {m.knowledge_taxonomy_node_add_cancel()}
                      </Button>
                    </div>
                  )}
                  {isEditing && (
                    <span className="inline-flex items-center gap-1">
                      <Button type="submit" size="sm" className="h-6 text-xs px-2" disabled={!editingName.trim()}>
                        {m.knowledge_taxonomy_node_edit_submit()}
                      </Button>
                      <Button type="button" size="sm" variant="ghost" className="h-6 text-xs px-2" onClick={cancelEdit}>
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
                  onKeyDown={(e) => { if (e.key === 'Escape') cancelEdit() }}
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
      })}

      {coverage.untagged_count > 0 && (
        <div className="rounded-lg border border-dashed border-gray-200 p-3">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-sm text-gray-400">
              {m.knowledge_taxonomy_coverage_untagged()}
            </span>
            <div className="flex items-center gap-2 shrink-0">
              <span className="text-xs text-gray-400 tabular-nums">
                {total > 0 ? Math.round((coverage.untagged_count / total) * 100) : 0}%
              </span>
              {isBackfilling ? (
                <div className="inline-flex items-center gap-1 text-xs text-gray-400">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  <span>{m.knowledge_taxonomy_categorising_status()}</span>
                </div>
              ) : coverage.nodes.length >= MAX_HEALTHY_NODE_COUNT ? (
                // SPEC-TAXONOMY-REVIEW-FLOW-001 follow-up: with the IA target
                // already met (Miller's Law 5-9), suggesting more categories
                // makes the taxonomy worse, not better. Hide the Suggest
                // button and explain why so operators don't pile on duplicates.
                <span className="text-xs text-gray-400 italic">
                  {m.knowledge_taxonomy_enough_categories_hint()}
                </span>
              ) : (
                onSuggest && coverage.untagged_count >= 10 && total > 0 && Math.round((coverage.untagged_count / total) * 100) > 5 && (
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); onSuggest() }}
                    disabled={isSuggesting}
                    className="inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded-full font-medium bg-gray-900 text-white hover:opacity-90 transition-opacity disabled:opacity-50"
                  >
                    {isSuggesting
                      ? <Loader2 className="h-3 w-3 animate-spin" />
                      : <Sparkles className="h-3 w-3" />
                    }
                    {m.knowledge_taxonomy_suggest_categories()}
                  </button>
                )
              )}
            </div>
          </div>
          <div className="h-1.5 w-full rounded-full bg-gray-200 overflow-hidden">
            <div
              className="h-full rounded-full bg-gray-200"
              style={{ width: `${total > 0 ? Math.round((coverage.untagged_count / total) * 100) : 0}%` }}
            />
          </div>
          <span className="text-xs text-gray-400 mt-1.5 block">
            {m.knowledge_taxonomy_coverage_chunks({ count: String(coverage.untagged_count) })}
          </span>
        </div>
      )}
    </div>
  )
}
