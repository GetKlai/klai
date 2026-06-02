/**
 * Coverage widget - a list of `<CoverageNodeRow>` plus the untagged
 * tail section + a contextual taxonomy action CTA.
 *
 * Owns the singleton state for which row is being edited / asked-to-
 * confirm-delete. Per-row buffers live in the row component itself.
 *
 * The taxonomy action button has two gates baked in:
 *   1. Empty KB: shown when total chunks >= SUGGEST_MIN_CHUNKS.
 *   2. Populated KB: shown when untagged_count >=
 *      SUGGEST_MIN_CHUNKS AND untagged percentage > SUGGEST_MIN_PCT.
 *      This categorizes missing chunks against existing nodes, not category
 *      proposal generation or full reclassification.
 */
import { useState } from 'react'
import { Loader2, Sparkles } from 'lucide-react'
import * as m from '@/paraglide/messages'
import type { TaxonomyCoverage } from '../-kb-types'
import { CoverageNodeRow } from './CoverageNodeRow'

/** Minimum chunks before the Suggest CTA is offered at all. */
const SUGGEST_MIN_CHUNKS = 10
/** Minimum untagged percentage (exclusive) before the Suggest CTA is offered. */
const SUGGEST_MIN_PCT = 5

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
  canEdit = false,
  onRename,
  onDelete,
}: CoverageWidgetProps) {
  const total = coverage.total_chunks
  const [editingNodeId, setEditingNodeId] = useState<number | null>(null)
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null)

  if (coverage.nodes.length === 0) {
    // Empty-state: KB has no taxonomy nodes yet. When chunks exist
    // (>= SUGGEST_MIN_CHUNKS) we surface the Suggest CTA here too -
    // without it the user faces a catch-22: no Suggest button until
    // nodes exist, no nodes until Suggest is clicked. Threshold mirrors
    // the populated-coverage Suggest gate below; for an empty KB every
    // chunk counts as untagged.
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
          onSuggest && total >= SUGGEST_MIN_CHUNKS && (
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

  const untaggedPct = total > 0 ? Math.round((coverage.untagged_count / total) * 100) : 0
  const showSuggestInUntagged =
    !!onSuggest &&
    coverage.untagged_count >= SUGGEST_MIN_CHUNKS &&
    total > 0 &&
    untaggedPct > SUGGEST_MIN_PCT

  return (
    <div className="space-y-2">
      {coverage.nodes.map((node) => (
        <CoverageNodeRow
          key={node.taxonomy_node_id}
          node={node}
          totalChunks={total}
          isActive={activeNodeId === node.taxonomy_node_id}
          isEditing={editingNodeId === node.taxonomy_node_id}
          isConfirmingDelete={confirmDeleteId === node.taxonomy_node_id}
          canEdit={canEdit}
          onNodeClick={() => onNodeClick(node.taxonomy_node_id)}
          onStartEdit={() => {
            setEditingNodeId(node.taxonomy_node_id)
            setConfirmDeleteId(null)
          }}
          onSubmitEdit={(name, description) => {
            onRename?.(node.taxonomy_node_id, name, description)
            setEditingNodeId(null)
          }}
          onCancelEdit={() => setEditingNodeId(null)}
          onStartDelete={() => setConfirmDeleteId(node.taxonomy_node_id)}
          onConfirmDelete={() => {
            onDelete?.(node.taxonomy_node_id)
            setConfirmDeleteId(null)
          }}
          onCancelDelete={() => setConfirmDeleteId(null)}
        />
      ))}

      {coverage.untagged_count > 0 && (
        <div className="rounded-lg border border-dashed border-gray-200 p-3">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-sm text-gray-400">
              {m.knowledge_taxonomy_coverage_untagged()}
            </span>
            <div className="flex items-center gap-2 shrink-0">
              <span className="text-xs text-gray-400 tabular-nums">
                {untaggedPct}%
              </span>
              {isBackfilling ? (
                <div className="inline-flex items-center gap-1 text-xs text-gray-400">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  <span>{m.knowledge_taxonomy_categorising_status()}</span>
                </div>
              ) : (
                showSuggestInUntagged && (
                  <button
                    type="button"
                    onClick={() => onSuggest?.()}
                    disabled={isSuggesting}
                    className="inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded-full font-medium bg-gray-900 text-white hover:opacity-90 transition-opacity disabled:opacity-50"
                  >
                    {isSuggesting
                      ? <Loader2 className="h-3 w-3 animate-spin" />
                      : <Sparkles className="h-3 w-3" />
                    }
                    {m.knowledge_taxonomy_retag()}
                  </button>
                )
              )}
            </div>
          </div>
          <div className="h-1.5 w-full rounded-full bg-gray-200 overflow-hidden">
            <div
              className="h-full rounded-full bg-gray-200"
              style={{ width: `${untaggedPct}%` }}
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
