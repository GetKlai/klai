/**
 * TaxonomyTab — extracted from `../taxonomy.tsx` route file by
 * SPEC-PORTAL-TAXONOMY-EXTRACT-001 so that `../insights.tsx` can
 * consume it without violating the `klai/no-cross-route-import`
 * ESLint rule (routes must not import from each other).
 *
 * This file currently contains the entire 720-line TaxonomyTab body
 * + two private helper components (CoverageWidget, TagCloud) + one
 * private constant (MAX_HEALTHY_NODE_COUNT) — all moved verbatim. The
 * internal split into focused sub-components and extracted hooks is
 * tracked under SPEC-PORTAL-TAXONOMY-SPLIT-001.
 */

import { useParams } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useEffect } from 'react'
import {
  Plus, Pencil, Trash2, Loader2, BarChart2,
  X, Tag, Filter, Sparkles,
} from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { taxonomyLogger } from '@/lib/logger'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import type {
  KnowledgeBase, MembersResponse, TaxonomyCoverage,
} from '../-kb-types'
import { kbQueryKeys } from '@/lib/kb-query-keys'
import {
  useApproveProposal,
  useBackfillTaxonomy,
  useBootstrapTaxonomy,
  useCreateNode,
  useDeleteNode,
  useRejectProposal,
  useRenameNode,
  useTaxonomyCoverage,
  useTaxonomyNodes,
  useTaxonomyProposals,
  useTopTags,
  type SuggestState,
} from '../-taxonomy-hooks'

// SPEC-TAXONOMY-REVIEW-FLOW-001 follow-up: cap on healthy taxonomy size.
// Mirrors the backend's ``taxonomy_consolidate_target_max`` (default 9).
// When the KB already has this many root taxonomy nodes, hide the
// "Suggest categories" affordance — Miller's Law makes more categories
// counter-productive (see SPEC-TAXONOMY-MERGE-DETECT-001 motivation).
// If the backend value drifts, revisit this constant.
const MAX_HEALTHY_NODE_COUNT = 9

// -- Coverage widget ----------------------------------------------------------

function CoverageWidget({
  coverage,
  activeNodeId,
  onNodeClick,
  onSuggest,
  isSuggesting,
  isBackfilling,
  canEdit,
  onRename,
  onDelete,
}: {
  coverage: TaxonomyCoverage
  activeNodeId: number | null
  onNodeClick: (nodeId: number) => void
  onSuggest?: () => void
  isSuggesting?: boolean
  // SPEC-TAXONOMY-REVIEW-FLOW-001 follow-up: when backfill is running we hide
  // the "Suggest categories" button and show an inline status indicator
  // so the user knows documents are being auto-categorised and tagged.
  isBackfilling?: boolean
  canEdit?: boolean
  onRename?: (nodeId: number, newName: string, description?: string) => void
  onDelete?: (nodeId: number) => void
}) {
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

// -- Tag cloud ----------------------------------------------------------------

function TagCloud({
  tags,
  activeTags,
  onTagClick,
}: {
  tags: { tag: string; count: number }[]
  activeTags: Set<string>
  onTagClick: (tag: string) => void
}) {
  const maxCount = tags[0]?.count ?? 1

  return (
    <div className="flex flex-wrap gap-1.5">
      {tags.map(({ tag, count }) => {
        const isActive = activeTags.has(tag)
        // Scale font size from 0.75rem (min count) to 1rem (max count)
        const scale = maxCount > 1 ? (count - 1) / (maxCount - 1) : 0
        const fontSize = 0.75 + scale * 0.25

        return (
          <button
            key={tag}
            type="button"
            onClick={() => onTagClick(tag)}
            className={[
              'inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 transition-colors',
              isActive
                ? 'border-gray-900 bg-gray-900 text-white'
                : 'border-gray-200 bg-gray-50 text-gray-900 hover:bg-gray-100',
            ].join(' ')}
            style={{ fontSize: `${fontSize}rem` }}
          >
            <span>{tag}</span>
            <span className="text-xs opacity-60 tabular-nums">{count}</span>
          </button>
        )
      })}
    </div>
  )
}

// -- Main taxonomy tab --------------------------------------------------------

export function TaxonomyTab({ kbSlug: kbSlugProp }: { kbSlug?: string } = {}) {
  // TaxonomyTab can be rendered standalone (via the /taxonomy route) or
  // embedded under /insights. `useParams({ strict: false })` reads the
  // current match generically so this works in both contexts.
  const routeParams = useParams({ strict: false })
  const kbSlug = kbSlugProp ?? routeParams.kbSlug ?? ''
  const auth = useAuth()
  const queryClient = useQueryClient()
  const { user } = useCurrentUser()

  // Filter state
  const [activeNodeId, setActiveNodeId] = useState<number | null>(null)
  const [activeTags, setActiveTags] = useState<Set<string>>(new Set())

  const hasFilter = activeNodeId !== null || activeTags.size > 0

  function toggleNode(nodeId: number) {
    setActiveNodeId((prev) => (prev === nodeId ? null : nodeId))
  }

  function toggleTag(tag: string) {
    setActiveTags((prev) => {
      const next = new Set(prev)
      if (next.has(tag)) next.delete(tag)
      else next.add(tag)
      return next
    })
  }

  function clearAllFilters() {
    setActiveNodeId(null)
    setActiveTags(new Set())
  }

  // Derive permissions from cached queries
  const { data: kb } = useQuery<KnowledgeBase>({
    queryKey: kbQueryKeys.knowledgeBase(kbSlug),
    queryFn: async () => apiFetch<KnowledgeBase>(`/api/app/knowledge-bases/${kbSlug}`),
    enabled: auth.isAuthenticated,
  })

  const { data: members } = useQuery<MembersResponse>({
    queryKey: ['kb-members', kbSlug],
    queryFn: async () => apiFetch<MembersResponse>(`/api/app/knowledge-bases/${kbSlug}/members`),
    enabled: auth.isAuthenticated && !!kb,
  })

  const myUserId = auth.user?.profile?.sub
  const isCreator = !!(myUserId && kb?.created_by === myUserId)
  const isContributor = isCreator || !!(myUserId && members?.users.some((u) => u.user_id === myUserId && (u.role === 'owner' || u.role === 'contributor')))
  const isAdmin = user?.isAdmin === true

  const [showAddRoot, setShowAddRoot] = useState(false)
  const [addParentId, setAddParentId] = useState<number | null>(null)
  const [newNodeName, setNewNodeName] = useState('')
  const [rejectingProposalId, setRejectingProposalId] = useState<number | null>(null)
  const [rejectReason, setRejectReason] = useState('')
  // SPEC-TAXONOMY-REVIEW-FLOW-001 Issue 5: edit-before-approve state
  const [editingProposalId, setEditingProposalId] = useState<number | null>(null)
  const [editingProposalTitle, setEditingProposalTitle] = useState('')
  const [editingProposalDescription, setEditingProposalDescription] = useState('')

  const nodesQuery = useTaxonomyNodes(kbSlug, auth.isAuthenticated)
  // SPEC-TAXONOMY-REVIEW-FLOW-001 Issue 3: fetches ALL statuses so approved
  // proposals stay visible after the approve click. Backend sorts by
  // most-recent activity so a freshly-approved proposal moves to the top.
  const proposalsQuery = useTaxonomyProposals(kbSlug, auth.isAuthenticated)
  const coverageQuery = useTaxonomyCoverage(kbSlug, auth.isAuthenticated && isAdmin)
  const topTagsQuery = useTopTags(kbSlug, activeNodeId, auth.isAuthenticated)

  const createNodeMutation = useCreateNode(kbSlug, () => {
    setNewNodeName('')
    setShowAddRoot(false)
    setAddParentId(null)
  })

  const renameNodeMutation = useRenameNode(kbSlug)
  const deleteNodeMutation = useDeleteNode(kbSlug)

  // SPEC-TAXONOMY-REVIEW-FLOW-001 Issue 5: approve accepts optional title +
  // description overrides (edit-before-approve). Issue 4: approve accepts
  // ?auto_categorise=false so the batch "Apply to KB" flow can defer
  // classification to a single backfill at the end.
  const approveMutation = useApproveProposal(kbSlug)

  const rejectMutation = useRejectProposal(kbSlug, () => {
    setRejectingProposalId(null)
    setRejectReason('')
  })

  // -- Suggest categories flow --
  const [suggestState, setSuggestState] = useState<SuggestState>('idle')

  // Sync suggestState with server data so the banner survives a page refresh.
  useEffect(() => {
    if (!proposalsQuery.isSuccess) return
    const pending = (proposalsQuery.data?.proposals ?? []).filter((p) => p.status === 'pending').length
    if (pending > 0) {
      setSuggestState((prev) => (prev === 'idle' ? 'proposals_ready' : prev))
    }
  }, [proposalsQuery.isSuccess, proposalsQuery.data])

  const bootstrapMutation = useBootstrapTaxonomy(kbSlug, setSuggestState)

  const backfillMutation = useBackfillTaxonomy(kbSlug, setSuggestState, {
    proposalsForFallback: () => proposalsQuery.data?.proposals ?? [],
  })

  async function handleApplyAll() {
    const pendingProposals = proposals.filter((p) => p.status === 'pending')
    // SPEC-TAXONOMY-REVIEW-FLOW-001 Issue 4: pass auto_categorise=false on each
    // approve so the backend skips per-approve classification jobs. The single
    // backfillMutation.mutate() at the bottom does the full re-classification
    // once. Saves N-1 wasted classification runs (was: N+1, now: 1).
    for (const proposal of pendingProposals) {
      try {
        await apiFetch(
          `/api/app/knowledge-bases/${kbSlug}/taxonomy/proposals/${proposal.id}/approve?auto_categorise=false`,
          { method: 'POST' },
        )
      } catch (err) {
        taxonomyLogger.warn('Failed to approve proposal during apply-all', { proposalId: proposal.id, error: err })
      }
    }
    void queryClient.invalidateQueries({ queryKey: ['taxonomy-proposals', kbSlug] })
    void queryClient.invalidateQueries({ queryKey: ['taxonomy-nodes', kbSlug] })
    void queryClient.invalidateQueries({ queryKey: ['taxonomy-coverage', kbSlug] })
    // Then trigger backfill — this is the single classification pass that
    // tags all chunks against the now-complete taxonomy.
    backfillMutation.mutate()
  }

  const applyAllMutation = useMutation({
    mutationFn: handleApplyAll,
  })

  const canEdit = isContributor || isAdmin
  const nodes = nodesQuery.data?.nodes ?? []
  const proposals = proposalsQuery.data?.proposals ?? []

  const isAddingChild = addParentId !== null

  // Resolve active node name for filter chips
  const activeNode = activeNodeId !== null ? nodes.find((n) => n.id === activeNodeId) : null

  const proposalTypeBadge: Record<string, { label: () => string; variant: 'accent' | 'success' | 'secondary' | 'destructive' }> = {
    new_node: { label: m.knowledge_taxonomy_proposals_type_new_node, variant: 'accent' },
    merge: { label: m.knowledge_taxonomy_proposals_type_merge, variant: 'secondary' },
    split: { label: m.knowledge_taxonomy_proposals_type_split, variant: 'secondary' },
    rename: { label: m.knowledge_taxonomy_proposals_type_rename, variant: 'accent' },
  }

  return (
    <div className="space-y-8">
      {/* Active filters bar */}
      {hasFilter && (
        <div className="flex items-center flex-wrap gap-2">
          <Filter className="h-3.5 w-3.5 text-gray-400 shrink-0" />
          <span className="text-xs text-gray-400">{m.knowledge_taxonomy_filter_heading()}:</span>
          {activeNode && (
            <button
              type="button"
              onClick={() => setActiveNodeId(null)}
              className="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-gray-100 px-2 py-0.5 text-xs text-gray-900 hover:bg-gray-200 transition-colors"
            >
              {m.knowledge_taxonomy_filter_node({ name: activeNode.name })}
              <X className="h-3 w-3" />
            </button>
          )}
          {Array.from(activeTags).map((tag) => (
            <button
              key={tag}
              type="button"
              onClick={() => toggleTag(tag)}
              className="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-gray-100 px-2 py-0.5 text-xs text-gray-900 hover:bg-gray-200 transition-colors"
            >
              {m.knowledge_taxonomy_filter_tag({ name: tag })}
              <X className="h-3 w-3" />
            </button>
          ))}
          <button
            type="button"
            onClick={clearAllFilters}
            className="text-xs text-gray-400 hover:text-gray-900 transition-colors ml-1"
          >
            {m.knowledge_taxonomy_filter_clear_all()}
          </button>
        </div>
      )}

      {/* Coverage widget — admin only */}
      {isAdmin && (
        <div>
          <div className="flex items-center gap-2 mb-3">
            <BarChart2 className="h-4 w-4 text-gray-900" />
            <h2 className="text-sm font-semibold text-gray-900">
              {m.knowledge_taxonomy_categories_coverage_heading()}
            </h2>
            {activeNodeId !== null && (
              <button
                type="button"
                onClick={() => setActiveNodeId(null)}
                className="text-xs text-gray-400 hover:text-gray-900 transition-colors"
              >
                {m.knowledge_taxonomy_coverage_filter_clear()}
              </button>
            )}
            <div className="flex items-center gap-2 ml-auto">
              {canEdit && !showAddRoot && !isAddingChild && (
                <Button size="sm" variant="outline" className="h-6 text-xs px-2" onClick={() => { setShowAddRoot(true); setAddParentId(null) }}>
                  <Plus className="h-3 w-3 mr-1" />
                  {m.knowledge_taxonomy_node_add_root()}
                </Button>
              )}
              {canEdit && nodes.length > 0 && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-6 text-xs px-2 text-gray-400"
                  onClick={() => backfillMutation.mutate()}
                  disabled={backfillMutation.isPending || suggestState === 'applying'}
                  title={backfillMutation.isPending || suggestState === 'applying'
                    ? m.knowledge_taxonomy_retag_running()
                    : m.knowledge_taxonomy_retag()}
                >
                  {backfillMutation.isPending || suggestState === 'applying'
                    ? <Loader2 className="h-3 w-3 animate-spin" />
                    : <Sparkles className="h-3 w-3" />
                  }
                  {m.knowledge_taxonomy_retag()}
                </Button>
              )}
            </div>
          </div>
          {coverageQuery.isLoading && (
            <p className="py-3 text-sm text-gray-400">
              <Loader2 className="inline h-4 w-4 animate-spin mr-1" />
              {m.knowledge_taxonomy_coverage_loading()}
            </p>
          )}
          {coverageQuery.data && (
            <CoverageWidget
              coverage={coverageQuery.data}
              activeNodeId={activeNodeId}
              onNodeClick={toggleNode}
              onSuggest={canEdit && (suggestState === 'idle' || suggestState === 'generating') ? () => bootstrapMutation.mutate() : undefined}
              isSuggesting={bootstrapMutation.isPending}
              isBackfilling={backfillMutation.isPending || applyAllMutation.isPending}
              canEdit={canEdit}
              onRename={(nodeId, newName, description) => renameNodeMutation.mutate({ nodeId, name: newName, description })}
              onDelete={(nodeId) => deleteNodeMutation.mutate(nodeId)}
            />
          )}

          {/* Inline add form (root or child) */}
          {(showAddRoot || isAddingChild) && (
            <form
              className="mt-2 flex items-center gap-2"
              onSubmit={(e) => {
                e.preventDefault()
                if (newNodeName.trim()) {
                  createNodeMutation.mutate({ name: newNodeName.trim(), parentId: addParentId })
                }
              }}
            >
              <Input
                value={newNodeName}
                onChange={(e) => setNewNodeName(e.target.value)}
                placeholder={m.knowledge_taxonomy_node_name_placeholder()}
                className="h-8 text-sm max-w-xs"
                autoFocus
              />
              <Button type="submit" size="sm" disabled={createNodeMutation.isPending || !newNodeName.trim()}>
                {m.knowledge_taxonomy_node_add_submit()}
              </Button>
              <Button type="button" size="sm" variant="ghost" onClick={() => { setShowAddRoot(false); setAddParentId(null); setNewNodeName('') }}>
                {m.knowledge_taxonomy_node_add_cancel()}
              </Button>
            </form>
          )}

          {createNodeMutation.error && (
            <p className="text-sm text-[var(--color-destructive)] mt-1">
              {createNodeMutation.error instanceof Error ? createNodeMutation.error.message : m.knowledge_taxonomy_error_create()}
            </p>
          )}
        </div>
      )}

      {/* Review queue — shown directly after coverage for visibility */}
      {/* SPEC-TAXONOMY-REVIEW-FLOW-001 Issues 3+5+6: pending/approved/rejected
          all visible with status badges; pending rows have edit-before-approve;
          approved rows persist (don't disappear) so the operator can keep
          reviewing without page refresh. */}
      {proposals.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-3">
            <BarChart2 className="h-4 w-4 text-gray-900" />
            <h2 className="text-sm font-semibold text-gray-900">{m.knowledge_taxonomy_proposals_heading()}</h2>
            <Badge variant="accent">{String(proposals.filter((p) => p.status === 'pending').length)}</Badge>
          </div>
          <div className="space-y-3">
            {proposals.map((proposal) => {
              const typeInfo = proposalTypeBadge[proposal.proposal_type] ?? { label: () => proposal.proposal_type, variant: 'secondary' as const }
              const isEditing = editingProposalId === proposal.id
              const isApproved = proposal.status === 'approved'
              const isRejected = proposal.status === 'rejected'
              const isPending = proposal.status === 'pending'
              // status badge (separate from proposal_type badge): "Nieuw" / "Goedgekeurd" / "Afgewezen"
              const statusBadge: { label: string; variant: 'accent' | 'success' | 'secondary' | 'destructive' } =
                isApproved
                  ? { label: m.knowledge_taxonomy_proposals_status_approved(), variant: 'success' }
                  : isRejected
                    ? { label: m.knowledge_taxonomy_proposals_status_rejected(), variant: 'destructive' }
                    : { label: m.knowledge_taxonomy_proposals_status_pending(), variant: 'accent' }
              return (
                <Card
                  key={proposal.id}
                  className={isRejected ? 'opacity-60' : isApproved ? 'bg-[var(--color-success)]/5' : undefined}
                >
                  <CardContent className="pt-4 pb-4">
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <Badge variant={statusBadge.variant}>{statusBadge.label}</Badge>
                          <Badge variant={typeInfo.variant}>{typeInfo.label()}</Badge>
                          {proposal.confidence_score != null && (
                            <span className="text-xs text-gray-400">
                              {m.knowledge_taxonomy_proposals_col_confidence()}: {Math.round(proposal.confidence_score * 100)}%
                            </span>
                          )}
                        </div>
                        {isEditing ? (
                          <form
                            className="space-y-2 mt-2"
                            onSubmit={(e) => {
                              e.preventDefault()
                              approveMutation.mutate({
                                proposalId: proposal.id,
                                title: editingProposalTitle.trim() || undefined,
                                description: editingProposalDescription,
                              })
                              setEditingProposalId(null)
                              setEditingProposalTitle('')
                              setEditingProposalDescription('')
                            }}
                          >
                            <Input
                              value={editingProposalTitle}
                              onChange={(e) => setEditingProposalTitle(e.target.value)}
                              placeholder={m.knowledge_taxonomy_proposals_edit_title_placeholder()}
                              className="h-7 text-sm font-medium"
                              autoFocus
                            />
                            <textarea
                              value={editingProposalDescription}
                              onChange={(e) => setEditingProposalDescription(e.target.value)}
                              placeholder={m.knowledge_taxonomy_proposals_edit_description_placeholder()}
                              rows={2}
                              className="w-full rounded-lg border border-gray-200 px-2.5 py-1.5 text-xs text-gray-900 resize-y"
                            />
                            <div className="flex items-center gap-2">
                              <Button
                                type="submit"
                                size="sm"
                                className="h-7 text-xs px-2.5 bg-[var(--color-success)] text-white hover:opacity-90"
                                disabled={approveMutation.isPending}
                              >
                                {m.knowledge_taxonomy_proposals_save_and_approve()}
                              </Button>
                              <Button
                                type="button"
                                size="sm"
                                variant="ghost"
                                className="h-7 text-xs px-2.5"
                                onClick={() => {
                                  setEditingProposalId(null)
                                  setEditingProposalTitle('')
                                  setEditingProposalDescription('')
                                }}
                              >
                                {m.knowledge_taxonomy_proposals_cancel()}
                              </Button>
                            </div>
                          </form>
                        ) : (
                          <>
                            <p className="text-sm font-medium text-gray-900">{proposal.title}</p>
                            {typeof proposal.payload?.description === 'string' && (
                              <p className="text-xs text-gray-400 mt-0.5">
                                {proposal.payload.description}
                              </p>
                            )}
                            <p className="text-xs text-gray-400 mt-0.5">
                              {new Date(proposal.created_at).toLocaleDateString()}
                              {proposal.rejection_reason && (
                                <span className="ml-2">— {proposal.rejection_reason}</span>
                              )}
                            </p>
                          </>
                        )}
                      </div>
                      {canEdit && isPending && !isEditing && (
                        <div className="flex items-center gap-1.5 shrink-0">
                          {rejectingProposalId === proposal.id ? (
                            <form
                              className="flex items-center gap-1.5"
                              onSubmit={(e) => {
                                e.preventDefault()
                                rejectMutation.mutate({ proposalId: proposal.id, reason: rejectReason })
                              }}
                            >
                              <Input
                                value={rejectReason}
                                onChange={(e) => setRejectReason(e.target.value)}
                                placeholder={m.knowledge_taxonomy_proposals_reject_reason_placeholder()}
                                className="h-7 text-xs w-48"
                                autoFocus
                              />
                              <Button type="submit" size="sm" variant="outline" className="h-7 text-xs px-2" disabled={rejectMutation.isPending}>
                                {m.knowledge_taxonomy_proposals_reject()}
                              </Button>
                              <Button type="button" size="sm" variant="ghost" className="h-7 text-xs px-2" onClick={() => { setRejectingProposalId(null); setRejectReason('') }}>
                                <X className="h-3 w-3" />
                              </Button>
                            </form>
                          ) : (
                            <>
                              <Button
                                size="sm"
                                className="h-7 text-xs px-2.5 bg-[var(--color-success)] text-white hover:opacity-90"
                                onClick={() => approveMutation.mutate({ proposalId: proposal.id })}
                                disabled={approveMutation.isPending}
                              >
                                {m.knowledge_taxonomy_proposals_approve()}
                              </Button>
                              <Button
                                size="sm"
                                variant="outline"
                                className="h-7 text-xs px-2.5"
                                onClick={() => {
                                  setEditingProposalId(proposal.id)
                                  setEditingProposalTitle(proposal.title)
                                  setEditingProposalDescription(
                                    typeof proposal.payload?.description === 'string'
                                      ? proposal.payload.description
                                      : '',
                                  )
                                }}
                              >
                                {m.knowledge_taxonomy_proposals_edit()}
                              </Button>
                              <Button
                                size="sm"
                                variant="outline"
                                className="h-7 text-xs px-2.5 text-[var(--color-destructive)] border-[var(--color-destructive)]/30 hover:bg-[var(--color-destructive)]/5"
                                onClick={() => setRejectingProposalId(proposal.id)}
                              >
                                {m.knowledge_taxonomy_proposals_reject()}
                              </Button>
                            </>
                          )}
                        </div>
                      )}
                    </div>
                  </CardContent>
                </Card>
              )
            })}
            {/* Apply All only shows when there are pending proposals to apply */}
            {canEdit && suggestState === 'proposals_ready' && proposals.some((p) => p.status === 'pending') && (
              <div className="pt-3">
                <Button
                  size="sm"
                  onClick={() => applyAllMutation.mutate()}
                  disabled={applyAllMutation.isPending || backfillMutation.isPending}
                  className="bg-gray-900 text-white hover:bg-gray-800"
                >
                  {applyAllMutation.isPending ? (
                    <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
                  ) : (
                    <Sparkles className="h-3.5 w-3.5 mr-1" />
                  )}
                  {m.knowledge_taxonomy_suggest_apply_all()}
                </Button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Tag cloud */}
      <div>
        <div className="flex items-center gap-2 mb-3">
          <Tag className="h-4 w-4 text-gray-900" />
          <h2 className="text-sm font-semibold text-gray-900">
            {m.knowledge_taxonomy_tags_heading()}
          </h2>
          {activeNodeId !== null && activeNode && (
            <span className="text-xs text-gray-400">
              {m.knowledge_taxonomy_coverage_filter_active({ name: activeNode.name })}
            </span>
          )}
        </div>
        {topTagsQuery.isLoading && (
          <p className="py-3 text-sm text-gray-400">
            <Loader2 className="inline h-4 w-4 animate-spin mr-1" />
            {m.knowledge_taxonomy_tags_loading()}
          </p>
        )}
        {topTagsQuery.data && topTagsQuery.data.tags.length === 0 && (
          <p className="text-sm text-gray-400">
            {m.knowledge_taxonomy_tags_empty()}
          </p>
        )}
        {topTagsQuery.data && topTagsQuery.data.tags.length > 0 && (
          <TagCloud
            tags={topTagsQuery.data.tags}
            activeTags={activeTags}
            onTagClick={toggleTag}
          />
        )}
      </div>

      {/* Suggest flow banners */}
      {suggestState === 'proposals_ready' && proposals.length > 0 && (
        <div className="rounded-lg border border-gray-200 bg-black/[0.06] p-4">
          <p className="text-sm font-medium text-gray-900">
            {m.knowledge_taxonomy_suggest_ready({ count: String(proposals.length) })}
          </p>
        </div>
      )}

      {suggestState === 'applying' && (
        <div className="rounded-lg border border-gray-200 bg-[var(--color-secondary)] p-4 flex items-center gap-2">
          <Loader2 className="h-4 w-4 animate-spin text-gray-400" />
          <p className="text-sm text-gray-900">{m.knowledge_taxonomy_suggest_applying()}</p>
        </div>
      )}

      {suggestState === 'done' && (
        <div className="rounded-lg border border-[var(--color-success)] bg-[var(--color-success)]/5 p-4">
          <p className="text-sm font-medium text-gray-900">
            {m.knowledge_taxonomy_suggest_done()}
          </p>
        </div>
      )}

      {/* Review queue removed — moved to after Coverage */}

    </div>
  )
}
