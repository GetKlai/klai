import { createFileRoute } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useEffect } from 'react'
import {
  Plus, Pencil, Trash2, Loader2, FolderTree, BarChart2,
  ChevronRight, ChevronDown, Check, X, Tag, Filter, Sparkles,
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
}: {
  coverage: TaxonomyCoverage
  activeNodeId: number | null
  onNodeClick: (nodeId: number) => void
}) {
  const total = coverage.total_chunks

  const healthColor = (health: string) => {
    if (health === 'healthy') return 'bg-[var(--color-success)]'
    if (health === 'attention_needed') return 'bg-amber-400'
    return 'bg-[var(--color-border)]'
  }

  const healthLabel = (health: string) => {
    if (health === 'healthy') return m.knowledge_taxonomy_coverage_health_healthy()
    if (health === 'attention_needed') return m.knowledge_taxonomy_coverage_health_attention()
    return m.knowledge_taxonomy_coverage_health_empty()
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
        return (
          <button
            key={node.taxonomy_node_id}
            type="button"
            onClick={() => onNodeClick(node.taxonomy_node_id)}
            className={[
              'w-full text-left rounded-lg border p-3 transition-colors',
              isActive
                ? 'border-[var(--color-accent)] bg-[var(--color-accent)]/5'
                : 'border-[var(--color-border)] hover:bg-[var(--color-secondary)]',
            ].join(' ')}
          >
            <div className="flex items-center justify-between mb-1.5 gap-2">
              <span className="text-sm font-medium text-[var(--color-foreground)] truncate">
                {node.taxonomy_node_name}
              </span>
              <div className="flex items-center gap-2 shrink-0">
                <span className="text-xs text-[var(--color-muted-foreground)] tabular-nums">
                  {pct}%
                </span>
                <span
                  className="text-[10px] px-1.5 py-0.5 rounded-full font-medium"
                  style={{
                    background: node.health === 'healthy' ? 'var(--color-success)' : node.health === 'attention_needed' ? '#F59E0B' : 'var(--color-border)',
                    color: node.health === 'empty' ? 'var(--color-muted-foreground)' : '#fff',
                  }}
                >
                  {healthLabel(node.health)}
                </span>
              </div>
            </div>
            <div className="h-1.5 w-full rounded-full bg-[var(--color-border)] overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${healthColor(node.health)}`}
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
          </button>
        )
      })}

      {coverage.untagged_count > 0 && (
        <div className="rounded-lg border border-dashed border-[var(--color-border)] p-3">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-sm text-[var(--color-muted-foreground)]">
              {m.knowledge_taxonomy_coverage_untagged()}
            </span>
            <span className="text-xs text-[var(--color-muted-foreground)] tabular-nums">
              {total > 0 ? Math.round((coverage.untagged_count / total) * 100) : 0}%
            </span>
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
            <span className="text-[10px] opacity-60 tabular-nums">{count}</span>
          </button>
        )
      })}
    </div>
  )
}

// -- Recursive tree component -------------------------------------------------

function TaxonomyTree({
  nodes,
  parentId,
  depth,
  canEdit,
  canDelete,
  onAddChild,
  onRename,
  onDelete,
}: {
  nodes: TaxonomyNode[]
  parentId: number | null
  depth: number
  canEdit: boolean
  canDelete: boolean
  onAddChild: (parentId: number) => void
  onRename: (node: TaxonomyNode, newName: string) => void
  onDelete: (nodeId: number) => void
}) {
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set())
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editName, setEditName] = useState('')
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null)

  const children = nodes.filter((n) => n.parent_id === parentId).sort((a, b) => a.sort_order - b.sort_order)
  if (children.length === 0) return null

  function toggleExpand(id: number) {
    setExpandedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function startRename(node: TaxonomyNode) {
    setEditingId(node.id)
    setEditName(node.name)
  }

  function submitRename(node: TaxonomyNode) {
    if (editName.trim() && editName.trim() !== node.name) {
      onRename(node, editName.trim())
    }
    setEditingId(null)
  }

  const hasChildren = (id: number) => nodes.some((n) => n.parent_id === id)

  return (
    <div>
      {children.map((node) => {
        const isExpanded = expandedIds.has(node.id)
        const hasKids = hasChildren(node.id)
        return (
          <div key={node.id}>
            <div
              className="group flex items-center gap-1 py-1.5 pr-2 rounded hover:bg-[var(--color-secondary)] transition-colors"
              style={{ paddingLeft: depth * 20 + 4 }}
            >
              <button
                type="button"
                onClick={() => toggleExpand(node.id)}
                className="flex h-5 w-5 items-center justify-center shrink-0"
                aria-label={isExpanded ? 'Collapse' : 'Expand'}
              >
                {hasKids ? (
                  isExpanded ? <ChevronDown className="h-3.5 w-3.5 text-[var(--color-muted-foreground)]" /> : <ChevronRight className="h-3.5 w-3.5 text-[var(--color-muted-foreground)]" />
                ) : (
                  <span className="h-3.5 w-3.5" />
                )}
              </button>

              {editingId === node.id ? (
                <form
                  className="flex items-center gap-1 flex-1"
                  onSubmit={(e) => { e.preventDefault(); submitRename(node) }}
                >
                  <Input
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    className="h-6 text-sm py-0 px-1.5 flex-1"
                    autoFocus
                    onKeyDown={(e) => { if (e.key === 'Escape') setEditingId(null) }}
                  />
                  <button type="submit" className="flex h-5 w-5 items-center justify-center rounded bg-[var(--color-success)] text-white hover:opacity-90">
                    <Check className="h-3 w-3" />
                  </button>
                  <button type="button" onClick={() => setEditingId(null)} className="flex h-5 w-5 items-center justify-center rounded border border-[var(--color-border)] text-[var(--color-muted-foreground)] hover:bg-[var(--color-border)]">
                    <X className="h-3 w-3" />
                  </button>
                </form>
              ) : (
                <>
                  <span className="text-sm text-[var(--color-foreground)] truncate flex-1">{node.name}</span>
                  <span className="text-xs text-[var(--color-muted-foreground)] tabular-nums shrink-0">
                    {node.doc_count > 0 && m.knowledge_taxonomy_node_doc_count({ count: String(node.doc_count) })}
                  </span>

                  {canEdit && (
                    <div className="hidden group-hover:flex items-center gap-0.5 ml-1">
                      <button
                        type="button"
                        onClick={() => onAddChild(node.id)}
                        aria-label={m.knowledge_taxonomy_node_add_child()}
                        className="flex h-5 w-5 items-center justify-center text-[var(--color-accent)] hover:opacity-70"
                      >
                        <Plus className="h-3 w-3" />
                      </button>
                      <button
                        type="button"
                        onClick={() => startRename(node)}
                        aria-label={m.knowledge_taxonomy_node_rename()}
                        className="flex h-5 w-5 items-center justify-center text-[var(--color-warning)] hover:opacity-70"
                      >
                        <Pencil className="h-3 w-3" />
                      </button>
                      {canDelete && (confirmDeleteId === node.id ? (
                        <div className="flex items-center gap-0.5">
                          <button
                            type="button"
                            onClick={() => { onDelete(node.id); setConfirmDeleteId(null) }}
                            className="flex h-5 w-5 items-center justify-center rounded bg-[var(--color-destructive)] text-white hover:opacity-90"
                          >
                            <Check className="h-3 w-3" />
                          </button>
                          <button
                            type="button"
                            onClick={() => setConfirmDeleteId(null)}
                            className="flex h-5 w-5 items-center justify-center rounded border border-[var(--color-border)] text-[var(--color-muted-foreground)] hover:bg-[var(--color-border)]"
                          >
                            <X className="h-3 w-3" />
                          </button>
                        </div>
                      ) : (
                        <button
                          type="button"
                          onClick={() => setConfirmDeleteId(node.id)}
                          aria-label={m.knowledge_taxonomy_node_delete()}
                          className="flex h-5 w-5 items-center justify-center text-[var(--color-destructive)] hover:opacity-70"
                        >
                          <Trash2 className="h-3 w-3" />
                        </button>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>

            {hasKids && isExpanded && (
              <TaxonomyTree
                nodes={nodes}
                parentId={node.id}
                depth={depth + 1}
                canEdit={canEdit}
                canDelete={canDelete}
                onAddChild={onAddChild}
                onRename={onRename}
                onDelete={onDelete}
              />
            )}
          </div>
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
  const isOwner = isCreator || !!(myUserId && members?.users.some((u) => u.user_id === myUserId && u.role === 'owner'))
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

  function handleAddChild(parentId: number) {
    setAddParentId(parentId)
    setShowAddRoot(false)
    setNewNodeName('')
  }

  function handleRename(node: TaxonomyNode, newName: string) {
    renameNodeMutation.mutate({ nodeId: node.id, name: newName })
  }

  const canEdit = isContributor || isAdmin
  const canDelete = isOwner || isAdmin
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
              className="inline-flex items-center gap-1 rounded-full border border-[var(--color-accent)] bg-[var(--color-accent)]/10 px-2 py-0.5 text-xs text-[var(--color-purple-deep)] hover:bg-[var(--color-accent)]/20 transition-colors"
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
              className="inline-flex items-center gap-1 rounded-full border border-[var(--color-accent)] bg-[var(--color-accent)]/10 px-2 py-0.5 text-xs text-[var(--color-purple-deep)] hover:bg-[var(--color-accent)]/20 transition-colors"
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
            <BarChart2 className="h-4 w-4 text-[var(--color-purple-deep)]" />
            <h2 className="text-sm font-semibold text-[var(--color-purple-deep)]">
              {m.knowledge_taxonomy_coverage_heading()}
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
            {canEdit && nodes.length > 0 && suggestState !== 'applying' && (
              <Button
                size="sm"
                variant="ghost"
                className="ml-auto h-6 text-xs px-2 text-[var(--color-muted-foreground)]"
                onClick={() => backfillMutation.mutate()}
                disabled={backfillMutation.isPending}
                title={m.knowledge_taxonomy_suggest_apply_all()}
              >
                {backfillMutation.isPending
                  ? <Loader2 className="h-3 w-3 animate-spin" />
                  : <Sparkles className="h-3 w-3" />
                }
                {m.knowledge_taxonomy_retag()}
              </Button>
            )}
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
            />
          )}
        </div>
      )}

      {/* Tag cloud */}
      <div>
        <div className="flex items-center gap-2 mb-3">
          <Tag className="h-4 w-4 text-[var(--color-purple-deep)]" />
          <h2 className="text-sm font-semibold text-[var(--color-purple-deep)]">
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

      {/* Category tree */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <FolderTree className="h-4 w-4 text-[var(--color-purple-deep)]" />
            <h2 className="text-sm font-semibold text-[var(--color-purple-deep)]">{m.knowledge_taxonomy_tree_heading()}</h2>
          </div>
          <div className="flex items-center gap-2">
            {canEdit && nodes.length === 0 && suggestState === 'idle' && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => bootstrapMutation.mutate()}
                disabled={bootstrapMutation.isPending}
              >
                <Sparkles className="h-3.5 w-3.5 mr-1" />
                {m.knowledge_taxonomy_suggest_categories()}
              </Button>
            )}
            {canEdit && !showAddRoot && !isAddingChild && (
              <Button size="sm" variant="outline" onClick={() => { setShowAddRoot(true); setAddParentId(null) }}>
                <Plus className="h-3.5 w-3.5 mr-1" />
                {m.knowledge_taxonomy_node_add_root()}
              </Button>
            )}
          </div>
        </div>

        {nodes.length === 0 && !nodesQuery.isLoading && (
          <div className="rounded-lg border border-dashed border-[var(--color-border)] p-6 text-center">
            {suggestState === 'generating' ? (
              <>
                <Loader2 className="mx-auto h-8 w-8 text-[var(--color-accent)] mb-2 animate-spin" />
                <p className="text-sm text-[var(--color-foreground)]">{m.knowledge_taxonomy_suggest_generating()}</p>
              </>
            ) : (
              <>
                <FolderTree className="mx-auto h-8 w-8 text-[var(--color-muted-foreground)] mb-2" />
                <p className="text-sm text-[var(--color-muted-foreground)]">{m.knowledge_taxonomy_tree_empty()}</p>
                <p className="text-xs text-[var(--color-muted-foreground)] mt-1">{m.knowledge_taxonomy_tree_empty_hint()}</p>
                {canEdit && suggestState === 'idle' && (
                  <Button
                    size="sm"
                    variant="outline"
                    className="mt-3"
                    onClick={() => bootstrapMutation.mutate()}
                    disabled={bootstrapMutation.isPending}
                  >
                    <Sparkles className="h-3.5 w-3.5 mr-1" />
                    {m.knowledge_taxonomy_suggest_categories()}
                  </Button>
                )}
              </>
            )}
            {bootstrapMutation.isError && (
              <p className="text-sm text-[var(--color-destructive)] mt-2">
                {m.knowledge_taxonomy_suggest_error()}
              </p>
            )}
          </div>
        )}

        {nodes.length > 0 && (
          <Card>
            <CardContent className="pt-3 pb-3 px-2">
              <TaxonomyTree
                nodes={nodes}
                parentId={null}
                depth={0}
                canEdit={canEdit}
                canDelete={canDelete}
                onAddChild={handleAddChild}
                onRename={handleRename}
                onDelete={(id) => deleteNodeMutation.mutate(id)}
              />
            </CardContent>
          </Card>
        )}

        {nodesQuery.isLoading && (
          <p className="py-4 text-sm text-[var(--color-muted-foreground)]">
            <Loader2 className="inline h-4 w-4 animate-spin mr-1" />
            {m.admin_connectors_loading()}
          </p>
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

      {/* Review queue */}
      <div>
        <div className="flex items-center gap-2 mb-3">
          <BarChart2 className="h-4 w-4 text-[var(--color-purple-deep)]" />
          <h2 className="text-sm font-semibold text-[var(--color-purple-deep)]">{m.knowledge_taxonomy_proposals_heading()}</h2>
          {proposals.length > 0 && (
            <Badge variant="accent">{String(proposals.length)}</Badge>
          )}
        </div>

        {proposalsQuery.isLoading && (
          <p className="py-4 text-sm text-[var(--color-muted-foreground)]">
            <Loader2 className="inline h-4 w-4 animate-spin mr-1" />
            {m.admin_connectors_loading()}
          </p>
        )}

        {!proposalsQuery.isLoading && proposals.length === 0 && (
          <p className="text-sm text-[var(--color-muted-foreground)]">{m.knowledge_taxonomy_proposals_empty()}</p>
        )}

        {proposals.length > 0 && (
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

            {/* Apply all to knowledge base */}
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
        )}
      </div>
    </div>
  )
}
