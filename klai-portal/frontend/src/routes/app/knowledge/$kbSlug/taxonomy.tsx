import { createFileRoute } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  Plus, Pencil, Trash2, Loader2, FolderTree, BarChart2,
  ChevronRight, ChevronDown, Check, X,
} from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import * as m from '@/paraglide/messages'
import { API_BASE } from '@/lib/api'
import { taxonomyLogger } from '@/lib/logger'
import type { KnowledgeBase, MembersResponse, TaxonomyNode, TaxonomyProposal } from './-kb-types'

export const Route = createFileRoute('/app/knowledge/$kbSlug/taxonomy')({
  component: TaxonomyTab,
})

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

  // Derive permissions from cached queries
  const { data: kb } = useQuery<KnowledgeBase>({
    queryKey: ['app-knowledge-base', kbSlug],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('KB laden mislukt')
      return res.json() as Promise<KnowledgeBase>
    },
    enabled: !!token,
  })

  const { data: members } = useQuery<MembersResponse>({
    queryKey: ['kb-members', kbSlug],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/members`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Members laden mislukt')
      return res.json() as Promise<MembersResponse>
    },
    enabled: !!token && !!kb,
  })

  const myUserId = auth.user?.profile?.sub
  const isOwner = !!(myUserId && members?.users.some((u) => u.user_id === myUserId && u.role === 'owner'))
  const isContributor = !!(myUserId && members?.users.some((u) => u.user_id === myUserId && (u.role === 'owner' || u.role === 'contributor')))

  const [showAddRoot, setShowAddRoot] = useState(false)
  const [addParentId, setAddParentId] = useState<number | null>(null)
  const [newNodeName, setNewNodeName] = useState('')
  const [rejectingProposalId, setRejectingProposalId] = useState<number | null>(null)
  const [rejectReason, setRejectReason] = useState('')

  const nodesQuery = useQuery<{ nodes: TaxonomyNode[] }>({
    queryKey: ['taxonomy-nodes', kbSlug],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/taxonomy/nodes`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) {
        taxonomyLogger.warn('Taxonomy nodes fetch failed', { slug: kbSlug, status: res.status })
        throw new Error(m.knowledge_taxonomy_error_fetch())
      }
      return res.json() as Promise<{ nodes: TaxonomyNode[] }>
    },
    enabled: !!token,
  })

  const proposalsQuery = useQuery<{ proposals: TaxonomyProposal[] }>({
    queryKey: ['taxonomy-proposals', kbSlug],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/taxonomy/proposals?status=pending`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) {
        taxonomyLogger.warn('Taxonomy proposals fetch failed', { slug: kbSlug, status: res.status })
        throw new Error(m.knowledge_taxonomy_error_fetch())
      }
      return res.json() as Promise<{ proposals: TaxonomyProposal[] }>
    },
    enabled: !!token,
  })

  const createNodeMutation = useMutation({
    mutationFn: async ({ name, parentId }: { name: string; parentId: number | null }) => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/taxonomy/nodes`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, parent_id: parentId }),
      })
      if (!res.ok) throw new Error(m.knowledge_taxonomy_error_create())
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
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/taxonomy/nodes/${nodeId}`, {
        method: 'PATCH',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      })
      if (!res.ok) throw new Error(m.knowledge_taxonomy_error_rename())
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['taxonomy-nodes', kbSlug] }),
  })

  const deleteNodeMutation = useMutation({
    mutationFn: async (nodeId: number) => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/taxonomy/nodes/${nodeId}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(m.knowledge_taxonomy_error_delete())
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['taxonomy-nodes', kbSlug] }),
  })

  const approveMutation = useMutation({
    mutationFn: async (proposalId: number) => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/taxonomy/proposals/${proposalId}/approve`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(m.knowledge_taxonomy_error_approve())
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-proposals', kbSlug] })
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-nodes', kbSlug] })
    },
  })

  const rejectMutation = useMutation({
    mutationFn: async ({ proposalId, reason }: { proposalId: number; reason: string }) => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/taxonomy/proposals/${proposalId}/reject`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason }),
      })
      if (!res.ok) throw new Error(m.knowledge_taxonomy_error_reject())
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-proposals', kbSlug] })
      setRejectingProposalId(null)
      setRejectReason('')
    },
  })

  function handleAddChild(parentId: number) {
    setAddParentId(parentId)
    setShowAddRoot(false)
    setNewNodeName('')
  }

  function handleRename(node: TaxonomyNode, newName: string) {
    renameNodeMutation.mutate({ nodeId: node.id, name: newName })
  }

  const canEdit = isContributor
  const canDelete = isOwner
  const nodes = nodesQuery.data?.nodes ?? []
  const proposals = proposalsQuery.data?.proposals ?? []
  const isAddingChild = addParentId !== null

  const proposalTypeBadge: Record<string, { label: () => string; variant: 'accent' | 'success' | 'secondary' | 'destructive' }> = {
    new_node: { label: m.knowledge_taxonomy_proposals_type_new_node, variant: 'accent' },
    merge: { label: m.knowledge_taxonomy_proposals_type_merge, variant: 'secondary' },
    split: { label: m.knowledge_taxonomy_proposals_type_split, variant: 'secondary' },
    rename: { label: m.knowledge_taxonomy_proposals_type_rename, variant: 'accent' },
  }

  return (
    <div className="space-y-8">
      {/* Category tree */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <FolderTree className="h-4 w-4 text-[var(--color-purple-deep)]" />
            <h2 className="text-sm font-semibold text-[var(--color-purple-deep)]">{m.knowledge_taxonomy_tree_heading()}</h2>
          </div>
          {canEdit && !showAddRoot && !isAddingChild && (
            <Button size="sm" variant="outline" onClick={() => { setShowAddRoot(true); setAddParentId(null) }}>
              <Plus className="h-3.5 w-3.5 mr-1" />
              {m.knowledge_taxonomy_node_add_root()}
            </Button>
          )}
        </div>

        {nodes.length === 0 && !nodesQuery.isLoading && (
          <div className="rounded-lg border border-dashed border-[var(--color-border)] p-6 text-center">
            <FolderTree className="mx-auto h-8 w-8 text-[var(--color-muted-foreground)] mb-2" />
            <p className="text-sm text-[var(--color-muted-foreground)]">{m.knowledge_taxonomy_tree_empty()}</p>
            <p className="text-xs text-[var(--color-muted-foreground)] mt-1">{m.knowledge_taxonomy_tree_empty_hint()}</p>
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
          </div>
        )}
      </div>
    </div>
  )
}
