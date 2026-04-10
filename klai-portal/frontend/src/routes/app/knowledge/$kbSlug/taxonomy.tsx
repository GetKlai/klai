import { createFileRoute } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
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
import { toast } from 'sonner'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import type {
  KnowledgeBase, MembersResponse, TaxonomyNode, TaxonomyProposal,
  TaxonomyCoverage, TopTagsResponse,
} from './-kb-types'

export const Route = createFileRoute('/app/knowledge/$kbSlug/taxonomy')({
  component: TaxonomyTab,
})

// -- Coverage widget ----------------------------------------------------------

function CoverageWidget({
  coverage,
  activeNodeId,
  onNodeClick,
  onSuggest,
  isSuggesting,
  canEdit,
  onRename,
  onDelete,
}: {
  coverage: TaxonomyCoverage
  activeNodeId: number | null
  onNodeClick: (nodeId: number) => void
  onSuggest?: () => void
  isSuggesting?: boolean
  canEdit?: boolean
  onRename?: (nodeId: number, newName: string) => void
  onDelete?: (nodeId: number) => void
}) {
  const total = coverage.total_chunks
  const [editingNodeId, setEditingNodeId] = useState<number | null>(null)
  const [editingName, setEditingName] = useState('')
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null)

  const barColor = (pct: number) => {
    if (pct >= 5) return 'bg-[var(--color-success)]'
    return 'bg-amber-400'
  }

  function startRename(nodeId: number, currentName: string): void {
    setEditingNodeId(nodeId)
    setEditingName(currentName)
    setConfirmDeleteId(null)
  }

  function submitRename(): void {
    if (editingNodeId !== null && editingName.trim() && onRename) {
      onRename(editingNodeId, editingName.trim())
    }
    setEditingNodeId(null)
    setEditingName('')
  }

  if (coverage.nodes.length === 0) {
    return (
      <p className="text-sm text-[var(--color-muted-foreground)]">
        {m.knowledge_taxonomy_coverage_empty()}
      </p>
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
                ? 'border-[var(--color-accent)] bg-[var(--color-accent)]/5'
                : 'border-[var(--color-border)] hover:bg-[var(--color-secondary)]',
            ].join(' ')}
            onClick={() => { if (!isEditing && !isConfirmingDelete) onNodeClick(node.taxonomy_node_id) }}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => { if (e.key === 'Enter' && !isEditing) onNodeClick(node.taxonomy_node_id) }}
          >
            <div className="flex items-center justify-between mb-1.5 gap-2">
              {isEditing ? (
                <form
                  className="flex items-center gap-1.5 flex-1"
                  onSubmit={(e) => { e.preventDefault(); submitRename() }}
                  onClick={(e) => e.stopPropagation()}
                >
                  <Input
                    value={editingName}
                    onChange={(e) => setEditingName(e.target.value)}
                    className="h-7 text-sm flex-1"
                    autoFocus
                    onKeyDown={(e) => { if (e.key === 'Escape') { setEditingNodeId(null); setEditingName('') } }}
                    onBlur={submitRename}
                  />
                </form>
              ) : (
                <span className="text-sm font-medium text-[var(--color-foreground)] truncate">
                  {node.taxonomy_node_name}
                </span>
              )}
              <div className="flex items-center gap-1.5 shrink-0">
                {canEdit && !isEditing && !isConfirmingDelete && (
                  <span className="hidden group-hover/row:inline-flex items-center gap-0.5">
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); startRename(node.taxonomy_node_id, node.taxonomy_node_name) }}
                      className="flex h-5 w-5 items-center justify-center text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] transition-colors"
                      aria-label={m.knowledge_taxonomy_node_rename()}
                    >
                      <Pencil className="h-3 w-3" />
                    </button>
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); setConfirmDeleteId(node.taxonomy_node_id) }}
                      className="flex h-5 w-5 items-center justify-center text-[var(--color-muted-foreground)] hover:text-[var(--color-destructive)] transition-colors"
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
                      className="h-6 text-xs px-2 bg-[var(--color-destructive)] text-white hover:opacity-90"
                      onClick={() => { onDelete?.(node.taxonomy_node_id); setConfirmDeleteId(null) }}
                    >
                      {m.knowledge_taxonomy_node_delete()}
                    </Button>
                    <Button size="sm" variant="ghost" className="h-6 text-xs px-2" onClick={() => setConfirmDeleteId(null)}>
                      <X className="h-3 w-3" />
                    </Button>
                  </div>
                )}
                {!isConfirmingDelete && (
                  <span className="text-xs text-[var(--color-muted-foreground)] tabular-nums">
                    {pct}%
                  </span>
                )}
              </div>
            </div>
            {node.description && !isEditing && (
              <p className="text-xs text-[var(--color-muted-foreground)] mb-1.5 line-clamp-2">
                {node.description}
              </p>
            )}
            <div className="h-1.5 w-full rounded-full bg-[var(--color-border)] overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${barColor(pct)}`}
                style={{ width: `${pct}%` }}
              />
            </div>
            <div className="flex items-center gap-3 mt-1.5">
              <span className="text-xs text-[var(--color-muted-foreground)]">
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
        <div className="rounded-lg border border-dashed border-[var(--color-border)] p-3">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-sm text-[var(--color-muted-foreground)]">
              {m.knowledge_taxonomy_coverage_untagged()}
            </span>
            <div className="flex items-center gap-2 shrink-0">
              <span className="text-xs text-[var(--color-muted-foreground)] tabular-nums">
                {total > 0 ? Math.round((coverage.untagged_count / total) * 100) : 0}%
              </span>
              {onSuggest && coverage.untagged_count >= 10 && total > 0 && Math.round((coverage.untagged_count / total) * 100) > 5 && (
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); onSuggest() }}
                  disabled={isSuggesting}
                  className="inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded-full font-medium bg-[var(--color-accent)] text-[var(--color-accent-foreground)] hover:opacity-90 transition-opacity disabled:opacity-50"
                >
                  {isSuggesting
                    ? <Loader2 className="h-3 w-3 animate-spin" />
                    : <Sparkles className="h-3 w-3" />
                  }
                  {m.knowledge_taxonomy_suggest_categories()}
                </button>
              )}
            </div>
          </div>
          <div className="h-1.5 w-full rounded-full bg-[var(--color-border)] overflow-hidden">
            <div
              className="h-full rounded-full bg-[var(--color-border)]"
              style={{ width: `${total > 0 ? Math.round((coverage.untagged_count / total) * 100) : 0}%` }}
            />
          </div>
          <span className="text-xs text-[var(--color-muted-foreground)] mt-1.5 block">
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
                ? 'border-[var(--color-accent)] bg-[var(--color-accent)] text-white'
                : 'border-[var(--color-border)] bg-[var(--color-secondary)] text-[var(--color-foreground)] hover:border-[var(--color-accent)]/50',
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

function TaxonomyTab() {
  const { kbSlug } = Route.useParams()
  const auth = useAuth()
  const token = auth.user?.access_token
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
    queryKey: ['app-knowledge-base', kbSlug],
    queryFn: async () => apiFetch<KnowledgeBase>(`/api/app/knowledge-bases/${kbSlug}`, token),
    enabled: !!token,
  })

  const { data: members } = useQuery<MembersResponse>({
    queryKey: ['kb-members', kbSlug],
    queryFn: async () => apiFetch<MembersResponse>(`/api/app/knowledge-bases/${kbSlug}/members`, token),
    enabled: !!token && !!kb,
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

  const nodesQuery = useQuery<{ nodes: TaxonomyNode[] }>({
    queryKey: ['taxonomy-nodes', kbSlug],
    queryFn: async () => {
      try {
        return await apiFetch<{ nodes: TaxonomyNode[] }>(`/api/app/knowledge-bases/${kbSlug}/taxonomy/nodes`, token)
      } catch (err) {
        taxonomyLogger.warn('Taxonomy nodes fetch failed', { slug: kbSlug, error: err })
        throw err
      }
    },
    enabled: !!token,
  })

  const proposalsQuery = useQuery<{ proposals: TaxonomyProposal[] }>({
    queryKey: ['taxonomy-proposals', kbSlug],
    queryFn: async () => {
      try {
        return await apiFetch<{ proposals: TaxonomyProposal[] }>(`/api/app/knowledge-bases/${kbSlug}/taxonomy/proposals?status=pending`, token)
      } catch (err) {
        taxonomyLogger.warn('Taxonomy proposals fetch failed', { slug: kbSlug, error: err })
        throw err
      }
    },
    enabled: !!token,
  })

  const coverageQuery = useQuery<TaxonomyCoverage>({
    queryKey: ['taxonomy-coverage', kbSlug],
    queryFn: async () => {
      try {
        return await apiFetch<TaxonomyCoverage>(`/api/app/knowledge-bases/${kbSlug}/taxonomy/coverage`, token)
      } catch (err) {
        taxonomyLogger.warn('Taxonomy coverage fetch failed', { slug: kbSlug, error: err })
        throw err
      }
    },
    enabled: !!token && isAdmin,
    staleTime: 5 * 60 * 1000,
  })

  const topTagsQuery = useQuery<TopTagsResponse>({
    queryKey: ['taxonomy-top-tags', kbSlug, activeNodeId],
    queryFn: async () => {
      const params = new URLSearchParams({ limit: '20' })
      if (activeNodeId !== null) params.set('taxonomy_node_id', String(activeNodeId))
      return apiFetch<TopTagsResponse>(`/api/app/knowledge-bases/${kbSlug}/taxonomy/top-tags?${params.toString()}`, token)
    },
    enabled: !!token,
    staleTime: 5 * 60 * 1000,
  })

  const createNodeMutation = useMutation({
    mutationFn: async ({ name, parentId }: { name: string; parentId: number | null }) => {
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/taxonomy/nodes`, token, {
        method: 'POST',
        body: JSON.stringify({ name, parent_id: parentId }),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-nodes', kbSlug] })
      setNewNodeName('')
      setShowAddRoot(false)
      setAddParentId(null)
    },
  })

  const renameNodeMutation = useMutation({
    mutationFn: async ({ nodeId, name }: { nodeId: number; name: string }) => {
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/taxonomy/nodes/${nodeId}`, token, {
        method: 'PATCH',
        body: JSON.stringify({ name }),
      })
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['taxonomy-nodes', kbSlug] }),
  })

  const deleteNodeMutation = useMutation({
    mutationFn: async (nodeId: number) => {
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/taxonomy/nodes/${nodeId}`, token, { method: 'DELETE' })
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['taxonomy-nodes', kbSlug] }),
  })

  const approveMutation = useMutation({
    mutationFn: async (proposalId: number) => {
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/taxonomy/proposals/${proposalId}/approve`, token, { method: 'POST' })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-proposals', kbSlug] })
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-nodes', kbSlug] })
    },
    onError: (err) => {
      const is409 = err instanceof Error && err.message.includes('409')
      taxonomyLogger.warn('Proposal approve failed', { error: String(err), is409 })
      if (is409) {
        toast.error(m.knowledge_taxonomy_proposals_conflict())
      } else {
        toast.error(m.knowledge_taxonomy_proposals_approve_error())
      }
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-proposals', kbSlug] })
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-nodes', kbSlug] })
    },
  })

  const rejectMutation = useMutation({
    mutationFn: async ({ proposalId, reason }: { proposalId: number; reason: string }) => {
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/taxonomy/proposals/${proposalId}/reject`, token, {
        method: 'POST',
        body: JSON.stringify({ reason }),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-proposals', kbSlug] })
      setRejectingProposalId(null)
      setRejectReason('')
    },
  })

  // -- Suggest categories flow --
  const [suggestState, setSuggestState] = useState<'idle' | 'generating' | 'proposals_ready' | 'applying' | 'done'>('idle')

  // Sync suggestState with server data so the banner survives a page refresh.
  useEffect(() => {
    if (!proposalsQuery.isSuccess) return
    const pending = (proposalsQuery.data?.proposals ?? []).filter((p) => p.status === 'pending').length
    if (pending > 0) {
      setSuggestState((prev) => (prev === 'idle' ? 'proposals_ready' : prev))
    }
  }, [proposalsQuery.isSuccess, proposalsQuery.data])

  const bootstrapMutation = useMutation({
    mutationFn: async () => {
      return await apiFetch<{ documents_scanned: number; proposals_submitted: number }>(
        `/api/app/knowledge-bases/${kbSlug}/taxonomy/bootstrap`,
        token,
        { method: 'POST' },
      )
    },
    onMutate: () => setSuggestState('generating'),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-proposals', kbSlug] })
      if (data.proposals_submitted > 0) {
        setSuggestState('proposals_ready')
      } else {
        setSuggestState('idle')
      }
    },
    onError: (err) => {
      taxonomyLogger.error('Bootstrap failed', { slug: kbSlug, error: err })
      setSuggestState('idle')
    },
  })

  const backfillMutation = useMutation({
    mutationFn: async () => {
      // 1. Enqueue the job
      const enqueue = await apiFetch<{ job_id: number; status: string }>(
        `/api/app/knowledge-bases/${kbSlug}/taxonomy/backfill-trigger`,
        token,
        { method: 'POST' },
      )
      const jobId = enqueue.job_id

      // 2. Poll until done (max 10 min, every 5 s)
      const MAX_POLLS = 120
      for (let i = 0; i < MAX_POLLS; i++) {
        await new Promise((r) => setTimeout(r, 5000))
        const s = await apiFetch<{ job_id: number; status: string }>(
          `/api/app/knowledge-bases/${kbSlug}/taxonomy/backfill/${jobId}`,
          token,
        )
        if (s.status === 'succeeded') return s
        if (s.status === 'failed') throw new Error('Backfill job failed')
      }
      throw new Error('Backfill timed out')
    },
    onMutate: () => setSuggestState('applying'),
    onSuccess: () => {
      setSuggestState('done')
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-nodes', kbSlug] })
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-proposals', kbSlug] })
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-coverage', kbSlug] })
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-top-tags', kbSlug] })
    },
    onError: (err) => {
      taxonomyLogger.error('Backfill failed', { slug: kbSlug, error: err })
      // If there are still pending proposals, go back to proposals_ready; otherwise idle
      setSuggestState((prev) => {
        if (prev === 'applying') {
          const pending = (proposalsQuery.data?.proposals ?? []).filter((p) => p.status === 'pending').length
          return pending > 0 ? 'proposals_ready' : 'idle'
        }
        return prev
      })
    },
  })

  async function handleApplyAll() {
    const pendingProposals = proposals.filter((p) => p.status === 'pending')
    // Approve all pending proposals sequentially
    for (const proposal of pendingProposals) {
      try {
        await apiFetch(
          `/api/app/knowledge-bases/${kbSlug}/taxonomy/proposals/${proposal.id}/approve`,
          token,
          { method: 'POST' },
        )
      } catch (err) {
        taxonomyLogger.warn('Failed to approve proposal during apply-all', { proposalId: proposal.id, error: err })
      }
    }
    void queryClient.invalidateQueries({ queryKey: ['taxonomy-proposals', kbSlug] })
    void queryClient.invalidateQueries({ queryKey: ['taxonomy-nodes', kbSlug] })
    // Then trigger backfill
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
          <Filter className="h-3.5 w-3.5 text-[var(--color-muted-foreground)] shrink-0" />
          <span className="text-xs text-[var(--color-muted-foreground)]">{m.knowledge_taxonomy_filter_heading()}:</span>
          {activeNode && (
            <button
              type="button"
              onClick={() => setActiveNodeId(null)}
              className="inline-flex items-center gap-1 rounded-full border border-[var(--color-accent)] bg-[var(--color-accent)]/10 px-2 py-0.5 text-xs text-[var(--color-foreground)] hover:bg-[var(--color-accent)]/20 transition-colors"
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
              className="inline-flex items-center gap-1 rounded-full border border-[var(--color-accent)] bg-[var(--color-accent)]/10 px-2 py-0.5 text-xs text-[var(--color-foreground)] hover:bg-[var(--color-accent)]/20 transition-colors"
            >
              {m.knowledge_taxonomy_filter_tag({ name: tag })}
              <X className="h-3 w-3" />
            </button>
          ))}
          <button
            type="button"
            onClick={clearAllFilters}
            className="text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] transition-colors ml-1"
          >
            {m.knowledge_taxonomy_filter_clear_all()}
          </button>
        </div>
      )}

      {/* Coverage widget — admin only */}
      {isAdmin && (
        <div>
          <div className="flex items-center gap-2 mb-3">
            <BarChart2 className="h-4 w-4 text-[var(--color-foreground)]" />
            <h2 className="text-sm font-semibold text-[var(--color-foreground)]">
              {m.knowledge_taxonomy_categories_coverage_heading()}
            </h2>
            {activeNodeId !== null && (
              <button
                type="button"
                onClick={() => setActiveNodeId(null)}
                className="text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] transition-colors"
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
                  className="h-6 text-xs px-2 text-[var(--color-muted-foreground)]"
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
            <p className="py-3 text-sm text-[var(--color-muted-foreground)]">
              <Loader2 className="inline h-4 w-4 animate-spin mr-1" />
              {m.knowledge_taxonomy_coverage_loading()}
            </p>
          )}
          {coverageQuery.data && (
            <CoverageWidget
              coverage={coverageQuery.data}
              activeNodeId={activeNodeId}
              onNodeClick={toggleNode}
              onSuggest={canEdit && suggestState === 'idle' ? () => bootstrapMutation.mutate() : undefined}
              isSuggesting={bootstrapMutation.isPending}
              canEdit={canEdit}
              onRename={(nodeId, newName) => renameNodeMutation.mutate({ nodeId, name: newName })}
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
      {proposals.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-3">
            <BarChart2 className="h-4 w-4 text-[var(--color-foreground)]" />
            <h2 className="text-sm font-semibold text-[var(--color-foreground)]">{m.knowledge_taxonomy_proposals_heading()}</h2>
            <Badge variant="accent">{String(proposals.length)}</Badge>
          </div>
          <div className="space-y-3">
            {proposals.map((proposal) => {
              const typeInfo = proposalTypeBadge[proposal.proposal_type] ?? { label: () => proposal.proposal_type, variant: 'secondary' as const }
              return (
                <Card key={proposal.id}>
                  <CardContent className="pt-4 pb-4">
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <Badge variant={typeInfo.variant}>{typeInfo.label()}</Badge>
                          {proposal.confidence_score != null && (
                            <span className="text-xs text-[var(--color-muted-foreground)]">
                              {m.knowledge_taxonomy_proposals_col_confidence()}: {Math.round(proposal.confidence_score * 100)}%
                            </span>
                          )}
                        </div>
                        <p className="text-sm font-medium text-[var(--color-foreground)]">{proposal.title}</p>
                        {typeof proposal.payload?.description === 'string' && (
                          <p className="text-xs text-[var(--color-muted-foreground)] mt-0.5">
                            {proposal.payload.description}
                          </p>
                        )}
                        <p className="text-xs text-[var(--color-muted-foreground)] mt-0.5">
                          {new Date(proposal.created_at).toLocaleDateString()}
                        </p>
                      </div>
                      {canEdit && (
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
                                onClick={() => approveMutation.mutate(proposal.id)}
                                disabled={approveMutation.isPending}
                              >
                                {m.knowledge_taxonomy_proposals_approve()}
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
            {canEdit && suggestState === 'proposals_ready' && (
              <div className="pt-3">
                <Button
                  size="sm"
                  onClick={() => applyAllMutation.mutate()}
                  disabled={applyAllMutation.isPending || backfillMutation.isPending}
                  className="bg-[var(--color-accent)] text-white hover:opacity-90"
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
          <Tag className="h-4 w-4 text-[var(--color-foreground)]" />
          <h2 className="text-sm font-semibold text-[var(--color-foreground)]">
            {m.knowledge_taxonomy_tags_heading()}
          </h2>
          {activeNodeId !== null && activeNode && (
            <span className="text-xs text-[var(--color-muted-foreground)]">
              {m.knowledge_taxonomy_coverage_filter_active({ name: activeNode.name })}
            </span>
          )}
        </div>
        {topTagsQuery.isLoading && (
          <p className="py-3 text-sm text-[var(--color-muted-foreground)]">
            <Loader2 className="inline h-4 w-4 animate-spin mr-1" />
            {m.knowledge_taxonomy_tags_loading()}
          </p>
        )}
        {topTagsQuery.data && topTagsQuery.data.tags.length === 0 && (
          <p className="text-sm text-[var(--color-muted-foreground)]">
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
        <div className="rounded-lg border border-[var(--color-accent)] bg-[var(--color-accent)]/5 p-4">
          <p className="text-sm font-medium text-[var(--color-foreground)]">
            {m.knowledge_taxonomy_suggest_ready({ count: String(proposals.length) })}
          </p>
        </div>
      )}

      {suggestState === 'applying' && (
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-secondary)] p-4 flex items-center gap-2">
          <Loader2 className="h-4 w-4 animate-spin text-[var(--color-accent)]" />
          <p className="text-sm text-[var(--color-foreground)]">{m.knowledge_taxonomy_suggest_applying()}</p>
        </div>
      )}

      {suggestState === 'done' && (
        <div className="rounded-lg border border-[var(--color-success)] bg-[var(--color-success)]/5 p-4">
          <p className="text-sm font-medium text-[var(--color-foreground)]">
            {m.knowledge_taxonomy_suggest_done()}
          </p>
        </div>
      )}

      {/* Review queue removed — moved to after Coverage */}
    </div>
  )
}
