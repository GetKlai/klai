/**
 * One taxonomy proposal card - pending / approved / rejected status,
 * inline edit-before-approve form (SPEC-TAXONOMY-REVIEW-FLOW-001
 * Issue 5), inline reject-with-reason form.
 *
 * Extracted from the `proposals.map()` callback in TaxonomyTab.tsx
 * by SPEC-PORTAL-TAXONOMY-SPLIT-001 commit 4.
 *
 * State machine:
 * - `isEditing` and `isRejecting` are SINGLETONS owned by the parent
 *   (TaxonomyTab keeps `editingProposalId` and `rejectingProposalId`
 *   so only one card may be in edit OR reject mode at any time).
 * - Per-card edit-buffer state (`editingTitle`, `editingDescription`,
 *   `rejectReason`) lives here. It is initialised when `isEditing` /
 *   `isRejecting` transitions to true.
 */
import { useEffect, useRef, useState } from 'react'
import { X } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import * as m from '@/paraglide/messages'
import type { TaxonomyProposal } from '../-kb-types'

type BadgeVariant = 'accent' | 'success' | 'secondary' | 'destructive'

const proposalTypeBadge: Record<string, { label: () => string; variant: BadgeVariant }> = {
  new_node: { label: m.knowledge_taxonomy_proposals_type_new_node, variant: 'accent' },
  merge: { label: m.knowledge_taxonomy_proposals_type_merge, variant: 'secondary' },
  split: { label: m.knowledge_taxonomy_proposals_type_split, variant: 'secondary' },
  rename: { label: m.knowledge_taxonomy_proposals_type_rename, variant: 'accent' },
}

export interface ProposalCardProps {
  proposal: TaxonomyProposal
  canEdit: boolean
  isEditing: boolean
  isRejecting: boolean
  /** Parent's approveMutation.isPending - shared across all cards. */
  approvePending: boolean
  /** Parent's rejectMutation.isPending - shared across all cards. */
  rejectPending: boolean
  onStartEdit: () => void
  onSubmitEdit: (title: string, description: string) => void
  onCancelEdit: () => void
  onStartReject: () => void
  onSubmitReject: (reason: string) => void
  onCancelReject: () => void
  onApprove: () => void
}

function payloadDescription(payload: TaxonomyProposal['payload']): string {
  return typeof payload?.description === 'string' ? payload.description : ''
}

export function ProposalCard({
  proposal,
  canEdit,
  isEditing,
  isRejecting,
  approvePending,
  rejectPending,
  onStartEdit,
  onSubmitEdit,
  onCancelEdit,
  onStartReject,
  onSubmitReject,
  onCancelReject,
  onApprove,
}: ProposalCardProps) {
  const [editingTitle, setEditingTitle] = useState(proposal.title)
  const [editingDescription, setEditingDescription] = useState(payloadDescription(proposal.payload))
  const [rejectReason, setRejectReason] = useState('')

  // Initialise edit buffers ONLY when the parent flips this card from
  // not-editing to editing - not on every re-render while editing is
  // active. Without the prevIsEditing ref the effect's `proposal.payload`
  // dep (an object) re-fires on every TanStack Query refetch (window
  // focus, mutation invalidation), wiping the user's typed input.
  // Mirrors the pre-SPEC inline behaviour where clicking "Edit" set
  // parent state including `setEditingProposalTitle(proposal.title)`
  // and then left the buffer alone until Save/Cancel.
  const prevIsEditing = useRef(false)
  useEffect(() => {
    if (isEditing && !prevIsEditing.current) {
      setEditingTitle(proposal.title)
      setEditingDescription(payloadDescription(proposal.payload))
    }
    prevIsEditing.current = isEditing
    // We intentionally do NOT depend on `proposal.title` /
    // `proposal.payload` - they are read for initial-value purposes
    // only and would re-fire this effect on every query refetch,
    // overwriting the user's typed input.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isEditing])

  // Reject reason: same transition guard. The buffer should clear only
  // when the card transitions into reject-mode, not on every render
  // while it stays in reject-mode.
  const prevIsRejecting = useRef(false)
  useEffect(() => {
    if (isRejecting && !prevIsRejecting.current) setRejectReason('')
    prevIsRejecting.current = isRejecting
  }, [isRejecting])

  const typeInfo =
    proposalTypeBadge[proposal.proposal_type] ??
    { label: () => proposal.proposal_type, variant: 'secondary' as const }

  const isApproved = proposal.status === 'approved'
  const isRejected = proposal.status === 'rejected'
  const isPending = proposal.status === 'pending'

  // status badge: "Nieuw" / "Goedgekeurd" / "Afgewezen"
  const statusBadge: { label: string; variant: BadgeVariant } = isApproved
    ? { label: m.knowledge_taxonomy_proposals_status_approved(), variant: 'success' }
    : isRejected
      ? { label: m.knowledge_taxonomy_proposals_status_rejected(), variant: 'destructive' }
      : { label: m.knowledge_taxonomy_proposals_status_pending(), variant: 'accent' }

  const description = payloadDescription(proposal.payload)

  return (
    <Card
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
                  onSubmitEdit(editingTitle.trim(), editingDescription)
                }}
              >
                <Input
                  value={editingTitle}
                  onChange={(e) => setEditingTitle(e.target.value)}
                  placeholder={m.knowledge_taxonomy_proposals_edit_title_placeholder()}
                  className="h-7 text-sm font-medium"
                  autoFocus
                />
                <textarea
                  value={editingDescription}
                  onChange={(e) => setEditingDescription(e.target.value)}
                  placeholder={m.knowledge_taxonomy_proposals_edit_description_placeholder()}
                  rows={2}
                  className="w-full rounded-lg border border-gray-200 px-2.5 py-1.5 text-xs text-gray-900 resize-y"
                />
                <div className="flex items-center gap-2">
                  <Button
                    type="submit"
                    size="sm"
                    className="h-7 text-xs px-2.5 bg-[var(--color-success)] text-white hover:opacity-90"
                    disabled={approvePending}
                  >
                    {m.knowledge_taxonomy_proposals_save_and_approve()}
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="h-7 text-xs px-2.5"
                    onClick={onCancelEdit}
                  >
                    {m.knowledge_taxonomy_proposals_cancel()}
                  </Button>
                </div>
              </form>
            ) : (
              <>
                <p className="text-sm font-medium text-gray-900">{proposal.title}</p>
                {description && (
                  <p className="text-xs text-gray-400 mt-0.5">
                    {description}
                  </p>
                )}
                <p className="text-xs text-gray-400 mt-0.5">
                  {new Date(proposal.created_at).toLocaleDateString()}
                  {proposal.rejection_reason && (
                    <span className="ml-2">- {proposal.rejection_reason}</span>
                  )}
                </p>
              </>
            )}
          </div>
          {canEdit && isPending && !isEditing && (
            <div className="flex items-center gap-1.5 shrink-0">
              {isRejecting ? (
                <form
                  className="flex items-center gap-1.5"
                  onSubmit={(e) => {
                    e.preventDefault()
                    onSubmitReject(rejectReason)
                  }}
                >
                  <Input
                    value={rejectReason}
                    onChange={(e) => setRejectReason(e.target.value)}
                    placeholder={m.knowledge_taxonomy_proposals_reject_reason_placeholder()}
                    className="h-7 text-xs w-48"
                    autoFocus
                  />
                  <Button type="submit" size="sm" variant="outline" className="h-7 text-xs px-2" disabled={rejectPending}>
                    {m.knowledge_taxonomy_proposals_reject()}
                  </Button>
                  <Button type="button" size="sm" variant="ghost" className="h-7 text-xs px-2" onClick={onCancelReject}>
                    <X className="h-3 w-3" />
                  </Button>
                </form>
              ) : (
                <>
                  <Button
                    size="sm"
                    className="h-7 text-xs px-2.5 bg-[var(--color-success)] text-white hover:opacity-90"
                    onClick={onApprove}
                    disabled={approvePending}
                  >
                    {m.knowledge_taxonomy_proposals_approve()}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 text-xs px-2.5"
                    onClick={onStartEdit}
                  >
                    {m.knowledge_taxonomy_proposals_edit()}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 text-xs px-2.5 text-[var(--color-destructive)] border-[var(--color-destructive)]/30 hover:bg-[var(--color-destructive)]/5"
                    onClick={onStartReject}
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
}
