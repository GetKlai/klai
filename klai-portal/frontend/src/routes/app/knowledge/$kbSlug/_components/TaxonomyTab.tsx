/**
 * TaxonomyTab - orchestrator for the Taxonomy/Insights tab. Composes
 * `<CoverageWidget>` (with `<CoverageNodeRow>`), `<ProposalCard>`,
 * `<TagCloud>`, plus the inline filter bar, add-form, and
 * suggest-flow banners.
 *
 * Lives under `_components/` (TanStack Router ignores the directory)
 * so both the `/taxonomy` and `/insights` routes can consume it
 * without violating the `klai/no-cross-route-import` ESLint rule.
 *
 * Owns the orchestrator state:
 *   - filter (activeNodeId, activeTags)
 *   - suggest-flow state machine (suggestState)
 *   - inline add-form (showAddRoot, addParentId, newNodeName)
 *   - singleton id for proposal edit (per-card input buffers live
 *     inside ProposalCard)
 *   - applyAllMutation + handleApplyAll (loops raw apiFetch + fires
 *     backfill - see SPEC Beslissingen § B5 for why orchestration
 *     stays here and not in a hook)
 *
 * Queries + mutations are extracted to `../-taxonomy-hooks.ts`.
 * History: SPEC-PORTAL-TAXONOMY-EXTRACT-001 moved this out of the
 * route file; SPEC-PORTAL-TAXONOMY-SPLIT-001 split the body further.
 */

import { useParams } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Plus, Loader2, BarChart2,
  X, Tag, Filter, Sparkles,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { taxonomyLogger } from '@/lib/logger'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import type { KnowledgeBase, MembersResponse } from '../-kb-types'
import { kbQueryKeys } from '@/lib/kb-query-keys'
import {
  useApproveProposal,
  useActiveTaxonomyBackfill,
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
import { TagCloud } from './TagCloud'
import { CoverageWidget } from './CoverageWidget'
import { ProposalCard } from './ProposalCard'


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
  const canEdit = isContributor || isAdmin

  const [showAddRoot, setShowAddRoot] = useState(false)
  const [addParentId, setAddParentId] = useState<number | null>(null)
  const [newNodeName, setNewNodeName] = useState('')
  // Singleton id - only one proposal may be in edit mode at any time.
  // Per-card edit buffers live inside ProposalCard.
  // SPEC-TAXONOMY-REVIEW-FLOW-001 Issue 5: edit-before-approve.
  const [editingProposalId, setEditingProposalId] = useState<number | null>(null)

  const nodesQuery = useTaxonomyNodes(kbSlug, auth.isAuthenticated)
  // SPEC-TAXONOMY-REVIEW-FLOW-001 Issue 3: fetches ALL statuses so approved
  // proposals stay visible after the approve click. Backend sorts by
  // most-recent activity so a freshly-approved proposal moves to the top.
  const proposalsQuery = useTaxonomyProposals(kbSlug, auth.isAuthenticated)
  const coverageQuery = useTaxonomyCoverage(kbSlug, auth.isAuthenticated && isAdmin)
  const topTagsQuery = useTopTags(kbSlug, activeNodeId, auth.isAuthenticated)
  const activeBackfillQuery = useActiveTaxonomyBackfill(kbSlug, auth.isAuthenticated && canEdit)

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

  const rejectMutation = useRejectProposal(kbSlug, () => undefined)

  // -- Suggest categories flow --
  const [suggestState, setSuggestState] = useState<SuggestState>('idle')

  // Stable empty-array fallbacks - TanStack Query returns undefined
  // until the first response, and a fresh `[]` per render would defeat
  // the useMemo'd derivations below.
  const nodes = useMemo(() => nodesQuery.data?.nodes ?? [], [nodesQuery.data?.nodes])
  const proposals = useMemo(
    () => proposalsQuery.data?.proposals ?? [],
    [proposalsQuery.data?.proposals],
  )

  // One memoised "pending" view used by:
  //   - the suggestState useEffect below (count > 0 → banner)
  //   - handleApplyAll (loop body)
  //   - the Apply All button visibility
  // Recomputes only when the proposals array changes - query refetches
  // that return identical content hand back a new ref but the equality
  // check inside useMemo still sees a new ref, so this is mostly a
  // readability + DRY win, not a perf one.
  const pendingProposals = useMemo(
    () => proposals.filter((p) => p.status === 'pending'),
    [proposals],
  )

  // Sync suggestState with server data so the banner survives a page
  // refresh. Depend on the derived count (a primitive) rather than on
  // proposalsQuery.data - that object's ref changes on every refetch,
  // which would re-fire the effect unnecessarily.
  useEffect(() => {
    if (!proposalsQuery.isSuccess) return
    if (pendingProposals.length > 0) {
      setSuggestState((prev) => (prev === 'idle' ? 'proposals_ready' : prev))
    }
  }, [proposalsQuery.isSuccess, pendingProposals.length])

  const bootstrapMutation = useBootstrapTaxonomy(kbSlug, setSuggestState)

  const backfillMutation = useBackfillTaxonomy(kbSlug, setSuggestState, {
    proposalsForFallback: () => proposalsQuery.data?.proposals ?? [],
  })

  /**
   * Apply-all orchestrator: approve every pending proposal with
   * `auto_categorise=false` (skip per-approve classification), then
   * trigger a single backfill to re-classify everything against the
   * now-complete taxonomy. Calls `apiFetch` directly rather than via
   * `useApproveProposal` - see SPEC Beslissingen § B5.
   *
   * SPEC-TAXONOMY-REVIEW-FLOW-001 Issue 4: per-approve auto_categorise
   * is suppressed so the single backfill at the end does all the
   * classification work in one pass. Saves N-1 wasted runs.
   */
  async function handleApplyAll() {
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
    backfillMutation.mutate()
  }

  const applyAllMutation = useMutation({
    mutationFn: handleApplyAll,
  })

  const isAddingChild = addParentId !== null
  const activeBackfillStatus = activeBackfillQuery.data?.status
  const isServerBackfillRunning = activeBackfillStatus === 'queued' || activeBackfillStatus === 'running'
  const sawServerBackfillRef = useRef(false)
  const isRetagging = backfillMutation.isPending || applyAllMutation.isPending || isServerBackfillRunning
  const canRequestCategorySuggestions = canEdit && pendingProposals.length === 0

  useEffect(() => {
    if (isServerBackfillRunning) {
      sawServerBackfillRef.current = true
      setSuggestState('applying')
      return
    }
    if (!activeBackfillQuery.isSuccess || !sawServerBackfillRef.current) return

    sawServerBackfillRef.current = false
    setSuggestState('done')
    void queryClient.invalidateQueries({ queryKey: ['taxonomy-nodes', kbSlug] })
    void queryClient.invalidateQueries({ queryKey: ['taxonomy-proposals', kbSlug] })
    void queryClient.invalidateQueries({ queryKey: ['taxonomy-coverage', kbSlug] })
    void queryClient.invalidateQueries({ queryKey: ['taxonomy-top-tags', kbSlug] })
  }, [activeBackfillQuery.isSuccess, isServerBackfillRunning, kbSlug, queryClient])

  // Resolve active node name for filter chips.
  const activeNode = useMemo(
    () => (activeNodeId !== null ? nodes.find((n) => n.id === activeNodeId) ?? null : null),
    [activeNodeId, nodes],
  )

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

      {/* Coverage widget - admin only */}
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
              onCategorizeMissing={canEdit && nodes.length > 0 && !isRetagging
                ? () => backfillMutation.mutate()
                : undefined}
              onSuggest={canRequestCategorySuggestions
                ? () => bootstrapMutation.mutate()
                : undefined}
              isCategorizingMissing={backfillMutation.isPending || isServerBackfillRunning}
              isSuggesting={bootstrapMutation.isPending}
              isBackfilling={isRetagging}
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

      {/* Review queue - shown directly after coverage for visibility */}
      {/* SPEC-TAXONOMY-REVIEW-FLOW-001 Issues 3+5+6: pending/approved/rejected
          all visible with status badges; pending rows have edit-before-approve;
          approved rows persist (don't disappear) so the operator can keep
          reviewing without page refresh. */}
      {proposals.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-3">
            <BarChart2 className="h-4 w-4 text-gray-900" />
            <h2 className="text-sm font-semibold text-gray-900">{m.knowledge_taxonomy_proposals_heading()}</h2>
            <Badge variant="accent">{String(pendingProposals.length)}</Badge>
          </div>
          <div className="space-y-3">
            {proposals.map((proposal) => (
              <ProposalCard
                key={proposal.id}
                proposal={proposal}
                canEdit={canEdit}
                isEditing={editingProposalId === proposal.id}
                approvePending={approveMutation.isPending}
                rejectPending={rejectMutation.isPending}
                onStartEdit={() => setEditingProposalId(proposal.id)}
                onSubmitEdit={(title, description) => {
                  approveMutation.mutate({
                    proposalId: proposal.id,
                    title: title || undefined,
                    description,
                  })
                  setEditingProposalId(null)
                }}
                onCancelEdit={() => setEditingProposalId(null)}
                onReject={() => rejectMutation.mutate({ proposalId: proposal.id })}
                onApprove={() => approveMutation.mutate({ proposalId: proposal.id })}
              />
            ))}
            {/* Apply All only shows when there are pending proposals to apply */}
            {canEdit && suggestState === 'proposals_ready' && pendingProposals.length > 0 && (
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

      {/* Review queue removed - moved to after Coverage */}

    </div>
  )
}
