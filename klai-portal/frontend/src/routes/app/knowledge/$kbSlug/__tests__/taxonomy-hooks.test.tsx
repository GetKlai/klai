/**
 * Hook tests for `-taxonomy-hooks.ts`.
 *
 * Locks the invalidation contract in Appendix A of
 * SPEC-PORTAL-TAXONOMY-SPLIT-001. Each test asserts the URL + method +
 * body the hook calls, the query keys it invalidates on success, and
 * the side-effects (callbacks, state transitions, toast/logging) per
 * the table.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { type ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  useApproveProposal,
  useBackfillTaxonomy,
  useBootstrapTaxonomy,
  useCreateNode,
  useDeleteNode,
  useRejectProposal,
  useRenameNode,
} from '../-taxonomy-hooks'
import type { TaxonomyProposal } from '../-kb-types'

vi.mock('@/lib/apiFetch', () => ({ apiFetch: vi.fn() }))
vi.mock('@/lib/logger', () => ({
  taxonomyLogger: { warn: vi.fn(), error: vi.fn() },
}))
vi.mock('sonner', () => ({ toast: { error: vi.fn() } }))
vi.mock('@/paraglide/messages', () => ({
  knowledge_taxonomy_proposals_conflict: () => 'conflict-msg',
  knowledge_taxonomy_proposals_approve_error: () => 'generic-msg',
}))

import { apiFetch } from '@/lib/apiFetch'
import { taxonomyLogger } from '@/lib/logger'
import { toast } from 'sonner'

const apiFetchMock = vi.mocked(apiFetch)
const toastErrorMock = vi.mocked(toast.error)
const loggerWarnMock = vi.mocked(taxonomyLogger.warn)
const loggerErrorMock = vi.mocked(taxonomyLogger.error)

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
}

function makeWrapper(client: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
}

function invalidationSpy(client: QueryClient) {
  return vi.spyOn(client, 'invalidateQueries')
}

function invalidatedKeys(spy: ReturnType<typeof invalidationSpy>): unknown[][] {
  return spy.mock.calls.map((c) => c[0]?.queryKey as unknown[])
}

beforeEach(() => {
  apiFetchMock.mockReset()
  toastErrorMock.mockReset()
  loggerWarnMock.mockReset()
  loggerErrorMock.mockReset()
})

afterEach(() => {
  vi.clearAllMocks()
})

// ---------------------------------------------------------------------------
// useCreateNode
// ---------------------------------------------------------------------------

describe('useCreateNode', () => {
  it('POSTs name + parent_id; invalidates taxonomy-nodes; calls onSuccess', async () => {
    apiFetchMock.mockResolvedValue(undefined)
    const client = makeClient()
    const spy = invalidationSpy(client)
    const onSuccess = vi.fn()
    const { result } = renderHook(() => useCreateNode('kb-a', onSuccess), {
      wrapper: makeWrapper(client),
    })

    result.current.mutate({ name: 'Sales', parentId: null })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(apiFetchMock).toHaveBeenCalledWith(
      '/api/app/knowledge-bases/kb-a/taxonomy/nodes',
      { method: 'POST', body: JSON.stringify({ name: 'Sales', parent_id: null }) },
    )
    expect(invalidatedKeys(spy)).toContainEqual(['taxonomy-nodes', 'kb-a'])
    expect(onSuccess).toHaveBeenCalledTimes(1)
  })

  it('does NOT call onSuccess when the request fails', async () => {
    apiFetchMock.mockRejectedValue(new Error('500 internal'))
    const client = makeClient()
    const onSuccess = vi.fn()
    const { result } = renderHook(() => useCreateNode('kb-a', onSuccess), {
      wrapper: makeWrapper(client),
    })

    result.current.mutate({ name: 'X', parentId: 5 })
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(onSuccess).not.toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// useRenameNode
// ---------------------------------------------------------------------------

describe('useRenameNode', () => {
  it('PATCHes name only when description is undefined; invalidates nodes + coverage', async () => {
    apiFetchMock.mockResolvedValue(undefined)
    const client = makeClient()
    const spy = invalidationSpy(client)
    const { result } = renderHook(() => useRenameNode('kb-a'), {
      wrapper: makeWrapper(client),
    })

    result.current.mutate({ nodeId: 12, name: 'New name' })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(apiFetchMock).toHaveBeenCalledWith(
      '/api/app/knowledge-bases/kb-a/taxonomy/nodes/12',
      { method: 'PATCH', body: JSON.stringify({ name: 'New name' }) },
    )
    const keys = invalidatedKeys(spy)
    expect(keys).toContainEqual(['taxonomy-nodes', 'kb-a'])
    expect(keys).toContainEqual(['taxonomy-coverage', 'kb-a'])
  })

  it('includes description in the body when provided', async () => {
    apiFetchMock.mockResolvedValue(undefined)
    const client = makeClient()
    const { result } = renderHook(() => useRenameNode('kb-a'), {
      wrapper: makeWrapper(client),
    })

    result.current.mutate({ nodeId: 12, name: 'X', description: 'desc' })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(apiFetchMock).toHaveBeenCalledWith(
      '/api/app/knowledge-bases/kb-a/taxonomy/nodes/12',
      {
        method: 'PATCH',
        body: JSON.stringify({ name: 'X', description: 'desc' }),
      },
    )
  })
})

// ---------------------------------------------------------------------------
// useDeleteNode
// ---------------------------------------------------------------------------

describe('useDeleteNode', () => {
  it('DELETEs the node; invalidates taxonomy-nodes only', async () => {
    apiFetchMock.mockResolvedValue(undefined)
    const client = makeClient()
    const spy = invalidationSpy(client)
    const { result } = renderHook(() => useDeleteNode('kb-a'), {
      wrapper: makeWrapper(client),
    })

    result.current.mutate(7)
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(apiFetchMock).toHaveBeenCalledWith(
      '/api/app/knowledge-bases/kb-a/taxonomy/nodes/7',
      { method: 'DELETE' },
    )
    const keys = invalidatedKeys(spy)
    expect(keys).toContainEqual(['taxonomy-nodes', 'kb-a'])
    expect(keys).not.toContainEqual(['taxonomy-coverage', 'kb-a'])
  })
})

// ---------------------------------------------------------------------------
// useApproveProposal
// ---------------------------------------------------------------------------

describe('useApproveProposal', () => {
  it('POSTs without query string or body when only proposalId is passed', async () => {
    apiFetchMock.mockResolvedValue(undefined)
    const client = makeClient()
    const { result } = renderHook(() => useApproveProposal('kb-a'), {
      wrapper: makeWrapper(client),
    })

    result.current.mutate({ proposalId: 42 })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(apiFetchMock).toHaveBeenCalledWith(
      '/api/app/knowledge-bases/kb-a/taxonomy/proposals/42/approve',
      { method: 'POST' },
    )
  })

  it('sends title + description in the body when provided', async () => {
    apiFetchMock.mockResolvedValue(undefined)
    const client = makeClient()
    const { result } = renderHook(() => useApproveProposal('kb-a'), {
      wrapper: makeWrapper(client),
    })

    result.current.mutate({
      proposalId: 42,
      title: 'Renamed',
      description: 'New description',
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(apiFetchMock).toHaveBeenCalledWith(
      '/api/app/knowledge-bases/kb-a/taxonomy/proposals/42/approve',
      {
        method: 'POST',
        body: JSON.stringify({ title: 'Renamed', description: 'New description' }),
      },
    )
  })

  it('appends ?auto_categorise=false when autoCategorise is false', async () => {
    apiFetchMock.mockResolvedValue(undefined)
    const client = makeClient()
    const { result } = renderHook(() => useApproveProposal('kb-a'), {
      wrapper: makeWrapper(client),
    })

    result.current.mutate({ proposalId: 42, autoCategorise: false })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(apiFetchMock).toHaveBeenCalledWith(
      '/api/app/knowledge-bases/kb-a/taxonomy/proposals/42/approve?auto_categorise=false',
      { method: 'POST' },
    )
  })

  it('invalidates proposals + nodes + coverage on success', async () => {
    apiFetchMock.mockResolvedValue(undefined)
    const client = makeClient()
    const spy = invalidationSpy(client)
    const { result } = renderHook(() => useApproveProposal('kb-a'), {
      wrapper: makeWrapper(client),
    })

    result.current.mutate({ proposalId: 42 })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    const keys = invalidatedKeys(spy)
    expect(keys).toContainEqual(['taxonomy-proposals', 'kb-a'])
    expect(keys).toContainEqual(['taxonomy-nodes', 'kb-a'])
    expect(keys).toContainEqual(['taxonomy-coverage', 'kb-a'])
  })

  it('shows 409-specific toast and warns with is409=true on conflict', async () => {
    apiFetchMock.mockRejectedValue(new Error('409 conflict'))
    const client = makeClient()
    const spy = invalidationSpy(client)
    const { result } = renderHook(() => useApproveProposal('kb-a'), {
      wrapper: makeWrapper(client),
    })

    result.current.mutate({ proposalId: 42 })
    await waitFor(() => expect(result.current.isError).toBe(true))

    expect(toastErrorMock).toHaveBeenCalledWith('conflict-msg')
    expect(loggerWarnMock).toHaveBeenCalledWith(
      'Proposal approve failed',
      expect.objectContaining({ is409: true }),
    )
    // On error: re-syncs proposals + nodes (not coverage).
    const keys = invalidatedKeys(spy)
    expect(keys).toContainEqual(['taxonomy-proposals', 'kb-a'])
    expect(keys).toContainEqual(['taxonomy-nodes', 'kb-a'])
  })

  it('shows generic toast and warns with is409=false on non-conflict error', async () => {
    apiFetchMock.mockRejectedValue(new Error('500 internal'))
    const client = makeClient()
    const { result } = renderHook(() => useApproveProposal('kb-a'), {
      wrapper: makeWrapper(client),
    })

    result.current.mutate({ proposalId: 42 })
    await waitFor(() => expect(result.current.isError).toBe(true))

    expect(toastErrorMock).toHaveBeenCalledWith('generic-msg')
    expect(loggerWarnMock).toHaveBeenCalledWith(
      'Proposal approve failed',
      expect.objectContaining({ is409: false }),
    )
  })
})

// ---------------------------------------------------------------------------
// useRejectProposal
// ---------------------------------------------------------------------------

describe('useRejectProposal', () => {
  it('POSTs reason; invalidates proposals; calls onSuccess', async () => {
    apiFetchMock.mockResolvedValue(undefined)
    const client = makeClient()
    const spy = invalidationSpy(client)
    const onSuccess = vi.fn()
    const { result } = renderHook(() => useRejectProposal('kb-a', onSuccess), {
      wrapper: makeWrapper(client),
    })

    result.current.mutate({ proposalId: 9, reason: 'not relevant' })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(apiFetchMock).toHaveBeenCalledWith(
      '/api/app/knowledge-bases/kb-a/taxonomy/proposals/9/reject',
      { method: 'POST', body: JSON.stringify({ reason: 'not relevant' }) },
    )
    expect(invalidatedKeys(spy)).toContainEqual(['taxonomy-proposals', 'kb-a'])
    expect(onSuccess).toHaveBeenCalledTimes(1)
  })

  it('does NOT call onSuccess when the request fails', async () => {
    apiFetchMock.mockRejectedValue(new Error('500'))
    const client = makeClient()
    const onSuccess = vi.fn()
    const { result } = renderHook(() => useRejectProposal('kb-a', onSuccess), {
      wrapper: makeWrapper(client),
    })

    result.current.mutate({ proposalId: 9, reason: 'x' })
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(onSuccess).not.toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// useBootstrapTaxonomy
// ---------------------------------------------------------------------------

describe('useBootstrapTaxonomy', () => {
  it('drives generating -> proposals_ready when proposals_submitted > 0', async () => {
    apiFetchMock.mockResolvedValue({ documents_scanned: 12, proposals_submitted: 3 })
    const client = makeClient()
    const states: string[] = []
    const setState = (
      next: string | ((prev: string) => string),
    ) => states.push(typeof next === 'function' ? next(states[states.length - 1] ?? 'idle') : next)

    const { result } = renderHook(
      () =>
        useBootstrapTaxonomy(
          'kb-a',
          setState as unknown as Parameters<typeof useBootstrapTaxonomy>[1],
        ),
      { wrapper: makeWrapper(client) },
    )

    result.current.mutate()
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(states).toEqual(['generating', 'proposals_ready'])
  })

  it('drives generating -> idle when proposals_submitted = 0', async () => {
    apiFetchMock.mockResolvedValue({ documents_scanned: 0, proposals_submitted: 0 })
    const client = makeClient()
    const states: string[] = []
    const setState = (next: string | ((prev: string) => string)) =>
      states.push(typeof next === 'function' ? next(states[states.length - 1] ?? 'idle') : next)

    const { result } = renderHook(
      () =>
        useBootstrapTaxonomy(
          'kb-a',
          setState as unknown as Parameters<typeof useBootstrapTaxonomy>[1],
        ),
      { wrapper: makeWrapper(client) },
    )
    result.current.mutate()
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(states).toEqual(['generating', 'idle'])
  })

  it('drives generating -> idle and logs error on failure', async () => {
    apiFetchMock.mockRejectedValue(new Error('boom'))
    const client = makeClient()
    const states: string[] = []
    const setState = (next: string | ((prev: string) => string)) =>
      states.push(typeof next === 'function' ? next(states[states.length - 1] ?? 'idle') : next)

    const { result } = renderHook(
      () =>
        useBootstrapTaxonomy(
          'kb-a',
          setState as unknown as Parameters<typeof useBootstrapTaxonomy>[1],
        ),
      { wrapper: makeWrapper(client) },
    )
    result.current.mutate()
    await waitFor(() => expect(result.current.isError).toBe(true))

    expect(states).toEqual(['generating', 'idle'])
    expect(loggerErrorMock).toHaveBeenCalledWith(
      'Bootstrap failed',
      expect.objectContaining({ slug: 'kb-a' }),
    )
  })
})

// ---------------------------------------------------------------------------
// useBackfillTaxonomy
// ---------------------------------------------------------------------------

function pendingProposal(id: number): TaxonomyProposal {
  return {
    id,
    status: 'pending',
    proposal_type: 'new_node',
    title: `Proposal ${id}`,
    confidence_score: 0.9,
    created_at: '2026-05-13T08:00:00Z',
    payload: {},
    rejection_reason: null,
  } as unknown as TaxonomyProposal
}

describe('useBackfillTaxonomy', () => {
  // The success path (`applying -> done` + 4 invalidations) is exercised
  // by the pre-SPEC inline implementation in production and by the
  // post-extract Playwright pass on Voys (AC6). It is not unit-tested
  // here because the hook's 5-second `setTimeout` poll loop is awkward
  // to stub in isolation: any `globalThis.setTimeout` mock leaks into
  // TanStack Query's own timing and times out neighbouring tests.
  // The error path tests below DO cover the state-machine transitions
  // (Appendix A row for `useBackfillTaxonomy.onError`) — that is the
  // refactor-sensitive part.

  it('on error during applying: falls back to proposals_ready if any pending', async () => {
    apiFetchMock.mockRejectedValue(new Error('enqueue failed'))
    const client = makeClient()
    const states: string[] = []
    const setState = (next: string | ((prev: string) => string)) =>
      states.push(typeof next === 'function' ? next(states[states.length - 1] ?? 'idle') : next)

    const { result } = renderHook(
      () =>
        useBackfillTaxonomy(
          'kb-a',
          setState as unknown as Parameters<typeof useBackfillTaxonomy>[1],
          { proposalsForFallback: () => [pendingProposal(1), pendingProposal(2)] },
        ),
      { wrapper: makeWrapper(client) },
    )

    result.current.mutate()
    await waitFor(() => expect(result.current.isError).toBe(true))

    expect(states).toEqual(['applying', 'proposals_ready'])
    expect(loggerErrorMock).toHaveBeenCalledWith(
      'Backfill failed',
      expect.objectContaining({ slug: 'kb-a' }),
    )
  })

  it('on error during applying: falls back to idle if no pending proposals', async () => {
    apiFetchMock.mockRejectedValue(new Error('enqueue failed'))
    const client = makeClient()
    const states: string[] = []
    const setState = (next: string | ((prev: string) => string)) =>
      states.push(typeof next === 'function' ? next(states[states.length - 1] ?? 'idle') : next)

    const { result } = renderHook(
      () =>
        useBackfillTaxonomy(
          'kb-a',
          setState as unknown as Parameters<typeof useBackfillTaxonomy>[1],
          { proposalsForFallback: () => [] },
        ),
      { wrapper: makeWrapper(client) },
    )

    result.current.mutate()
    await waitFor(() => expect(result.current.isError).toBe(true))

    expect(states).toEqual(['applying', 'idle'])
  })
})
