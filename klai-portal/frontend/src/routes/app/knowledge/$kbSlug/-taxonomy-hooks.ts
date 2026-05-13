/**
 * Taxonomy queries + mutations extracted out of TaxonomyTab.tsx by
 * SPEC-PORTAL-TAXONOMY-SPLIT-001 commit 1.
 *
 * Each hook here owns one taxonomy concern. Query keys are unchanged
 * from the pre-SPEC inline versions. Mutation invalidation sets follow
 * the contract in Appendix A of the SPEC.
 *
 * `applyAllMutation` is intentionally NOT here — it orchestrates a
 * loop of `apiFetch(/approve?auto_categorise=false)` calls, three
 * queryClient invalidations, and a `backfillMutation.mutate()`
 * trigger. That orchestration belongs in the orchestrator
 * (TaxonomyTab.tsx). See SPEC Beslissingen § B5.
 *
 * Auth gating is the caller's responsibility — pass `enabled` to the
 * query hooks. This matches the pattern from
 * `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/-sources-hooks.ts`.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '@/lib/apiFetch'
import { taxonomyLogger } from '@/lib/logger'
import { toast } from 'sonner'
import * as m from '@/paraglide/messages'
import type {
  TaxonomyCoverage,
  TaxonomyNode,
  TaxonomyProposal,
  TopTagsResponse,
} from './-kb-types'

export type SuggestState =
  | 'idle'
  | 'generating'
  | 'proposals_ready'
  | 'applying'
  | 'done'

type SuggestStateSetter = (
  next: SuggestState | ((prev: SuggestState) => SuggestState),
) => void

// -- Queries -----------------------------------------------------------------

export function useTaxonomyNodes(kbSlug: string, enabled = true) {
  return useQuery<{ nodes: TaxonomyNode[] }>({
    queryKey: ['taxonomy-nodes', kbSlug],
    queryFn: async () => {
      try {
        return await apiFetch<{ nodes: TaxonomyNode[] }>(
          `/api/app/knowledge-bases/${kbSlug}/taxonomy/nodes`,
        )
      } catch (err) {
        taxonomyLogger.warn('Taxonomy nodes fetch failed', { slug: kbSlug, error: err })
        throw err
      }
    },
    enabled,
  })
}

export function useTaxonomyProposals(kbSlug: string, enabled = true) {
  return useQuery<{ proposals: TaxonomyProposal[] }>({
    queryKey: ['taxonomy-proposals', kbSlug],
    queryFn: async () => {
      try {
        return await apiFetch<{ proposals: TaxonomyProposal[] }>(
          `/api/app/knowledge-bases/${kbSlug}/taxonomy/proposals?status=all`,
        )
      } catch (err) {
        taxonomyLogger.warn('Taxonomy proposals fetch failed', { slug: kbSlug, error: err })
        throw err
      }
    },
    enabled,
  })
}

export function useTaxonomyCoverage(kbSlug: string, enabled = true) {
  return useQuery<TaxonomyCoverage>({
    queryKey: ['taxonomy-coverage', kbSlug],
    queryFn: async () => {
      try {
        return await apiFetch<TaxonomyCoverage>(
          `/api/app/knowledge-bases/${kbSlug}/taxonomy/coverage`,
        )
      } catch (err) {
        taxonomyLogger.warn('Taxonomy coverage fetch failed', { slug: kbSlug, error: err })
        throw err
      }
    },
    enabled,
    staleTime: 5 * 60 * 1000,
  })
}

export function useTopTags(
  kbSlug: string,
  activeNodeId: number | null,
  enabled = true,
) {
  return useQuery<TopTagsResponse>({
    queryKey: ['taxonomy-top-tags', kbSlug, activeNodeId],
    queryFn: async () => {
      const params = new URLSearchParams({ limit: '20' })
      if (activeNodeId !== null) params.set('taxonomy_node_id', String(activeNodeId))
      return apiFetch<TopTagsResponse>(
        `/api/app/knowledge-bases/${kbSlug}/taxonomy/top-tags?${params.toString()}`,
      )
    },
    enabled,
    staleTime: 5 * 60 * 1000,
  })
}

// -- Mutations ---------------------------------------------------------------

/**
 * Create a taxonomy node. `onSuccess` is called after the
 * `taxonomy-nodes` invalidation so the caller can reset its add-form
 * state (mirrors the pre-SPEC inline mutation which reset
 * `newNodeName`, `showAddRoot`, `addParentId`).
 */
export function useCreateNode(kbSlug: string, onSuccess: () => void) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({
      name,
      parentId,
    }: {
      name: string
      parentId: number | null
    }) => {
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/taxonomy/nodes`, {
        method: 'POST',
        body: JSON.stringify({ name, parent_id: parentId }),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-nodes', kbSlug] })
      onSuccess()
    },
  })
}

export function useRenameNode(kbSlug: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({
      nodeId,
      name,
      description,
    }: {
      nodeId: number
      name: string
      description?: string
    }) => {
      const body: Record<string, string> = { name }
      if (description !== undefined) body.description = description
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/taxonomy/nodes/${nodeId}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-nodes', kbSlug] })
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-coverage', kbSlug] })
    },
  })
}

export function useDeleteNode(kbSlug: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (nodeId: number) => {
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/taxonomy/nodes/${nodeId}`, {
        method: 'DELETE',
      })
    },
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-nodes', kbSlug] }),
  })
}

export interface ApproveProposalVars {
  proposalId: number
  /** SPEC-TAXONOMY-REVIEW-FLOW-001 Issue 5: edit-before-approve. */
  title?: string
  description?: string
  /**
   * SPEC-TAXONOMY-REVIEW-FLOW-001 Issue 4: when applyAll is the caller,
   * pass `false` so the backend skips per-approve classification; the
   * orchestrator triggers a single backfill at the end. The
   * orchestrator does NOT use this hook (it calls apiFetch directly,
   * see Beslissingen § B5) — this flag exists for parity with the
   * pre-SPEC API.
   */
  autoCategorise?: boolean
}

/**
 * Approve a single proposal.
 *
 * On error, the hook surfaces a user-visible toast (409 → conflict
 * copy; other → generic approve-error copy) AND invalidates
 * `taxonomy-proposals` + `taxonomy-nodes` so the UI re-syncs with
 * server state. Mirrors the pre-SPEC `approveMutation.onError`.
 */
export function useApproveProposal(kbSlug: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (vars: ApproveProposalVars) => {
      const params = new URLSearchParams()
      if (vars.autoCategorise === false) params.set('auto_categorise', 'false')
      const qs = params.toString() ? `?${params.toString()}` : ''
      const body: Record<string, string> = {}
      if (vars.title !== undefined) body.title = vars.title
      if (vars.description !== undefined) body.description = vars.description
      const init: { method: string; body?: string } = { method: 'POST' }
      if (Object.keys(body).length > 0) init.body = JSON.stringify(body)
      await apiFetch(
        `/api/app/knowledge-bases/${kbSlug}/taxonomy/proposals/${vars.proposalId}/approve${qs}`,
        init,
      )
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-proposals', kbSlug] })
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-nodes', kbSlug] })
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-coverage', kbSlug] })
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
}

/**
 * Reject a proposal. `onSuccess` is called after the
 * `taxonomy-proposals` invalidation so the caller can reset its
 * reject-form state (`rejectingProposalId`, `rejectReason`).
 */
export function useRejectProposal(kbSlug: string, onSuccess: () => void) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({
      proposalId,
      reason,
    }: {
      proposalId: number
      reason: string
    }) => {
      await apiFetch(
        `/api/app/knowledge-bases/${kbSlug}/taxonomy/proposals/${proposalId}/reject`,
        {
          method: 'POST',
          body: JSON.stringify({ reason }),
        },
      )
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-proposals', kbSlug] })
      onSuccess()
    },
  })
}

/**
 * Run taxonomy bootstrap (auto-suggest categories from KB content).
 * The hook drives the `suggestState` state machine via the
 * `onStateChange` callback — caller (TaxonomyTab) owns the
 * `useState` and passes its setter.
 */
export function useBootstrapTaxonomy(
  kbSlug: string,
  onStateChange: SuggestStateSetter,
) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      return await apiFetch<{ documents_scanned: number; proposals_submitted: number }>(
        `/api/app/knowledge-bases/${kbSlug}/taxonomy/bootstrap`,
        { method: 'POST' },
      )
    },
    onMutate: () => onStateChange('generating'),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-proposals', kbSlug] })
      if (data.proposals_submitted > 0) {
        onStateChange('proposals_ready')
      } else {
        onStateChange('idle')
      }
    },
    onError: (err) => {
      taxonomyLogger.error('Bootstrap failed', { slug: kbSlug, error: err })
      onStateChange('idle')
    },
  })
}

/**
 * Enqueue a backfill job and poll until it succeeds, fails, or times
 * out (max 120 polls × 5s = 10 min).
 *
 * `opts.proposalsForFallback` is a getter the hook calls during
 * `onError` to compute the fallback state (proposals_ready when any
 * remain pending, idle otherwise). It's a getter, not a snapshot,
 * because TanStack Query data can change between mutation start and
 * error. Caller (TaxonomyTab) passes
 * `() => proposalsQuery.data?.proposals ?? []`.
 */
export function useBackfillTaxonomy(
  kbSlug: string,
  onStateChange: SuggestStateSetter,
  opts: { proposalsForFallback: () => TaxonomyProposal[] },
) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const enqueue = await apiFetch<{ job_id: number; status: string }>(
        `/api/app/knowledge-bases/${kbSlug}/taxonomy/backfill-trigger`,
        { method: 'POST' },
      )
      const jobId = enqueue.job_id
      const MAX_POLLS = 120
      for (let i = 0; i < MAX_POLLS; i++) {
        await new Promise((r) => setTimeout(r, 5000))
        const s = await apiFetch<{ job_id: number; status: string }>(
          `/api/app/knowledge-bases/${kbSlug}/taxonomy/backfill/${jobId}`,
        )
        if (s.status === 'succeeded') return s
        if (s.status === 'failed') throw new Error('Backfill job failed')
      }
      throw new Error('Backfill timed out')
    },
    onMutate: () => onStateChange('applying'),
    onSuccess: () => {
      onStateChange('done')
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-nodes', kbSlug] })
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-proposals', kbSlug] })
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-coverage', kbSlug] })
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-top-tags', kbSlug] })
    },
    onError: (err) => {
      taxonomyLogger.error('Backfill failed', { slug: kbSlug, error: err })
      onStateChange((prev) => {
        if (prev === 'applying') {
          const pending = opts
            .proposalsForFallback()
            .filter((p) => p.status === 'pending').length
          return pending > 0 ? 'proposals_ready' : 'idle'
        }
        return prev
      })
    },
  })
}
