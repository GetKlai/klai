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
  Plus, Loader2, BarChart2,
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
import type { KnowledgeBase, MembersResponse } from '../-kb-types'
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
import { TagCloud } from './TagCloud'
import { CoverageWidget } from './CoverageWidget'


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
