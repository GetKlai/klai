/**
 * Hook tests for `-sources-hooks.ts`.
 *
 * Focused on the contract that other layers can't verify by inspection:
 *
 *   1. `useSourceSync` hits the correct endpoint per source.kind
 *      (connector → /sync, upload → /reindex). One wrong character here
 *      and the row's primary action becomes a no-op against the wrong URL.
 *
 *   2. `useSourceRename` calls `onDone` ONLY on success - failures keep the
 *      inline-edit overlay open so the user can retry without re-typing
 *      (regression captured during SPEC-PORTAL-SOURCES-RENAME-001 Phase 4
 *      adversarial review; the original `onSettled` version dropped the
 *      typed name on network failure).
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { type ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useSourceDelete, useSourceRename, useSourceSync } from '../-sources-hooks'
import type { Source } from '../-sources-types'

vi.mock('@/lib/apiFetch', () => ({
  apiFetch: vi.fn(),
}))
vi.mock('@/lib/logger', () => ({
  queryLogger: { error: vi.fn() },
}))

import { apiFetch } from '@/lib/apiFetch'

const apiFetchMock = vi.mocked(apiFetch)

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
}

function uploadSource(overrides: Partial<Source> = {}): Source {
  return {
    kind: 'upload',
    id: 'art-1',
    name: 'old-name.pdf',
    type_label: 'PDF',
    connector_type: null,
    items_count: 1,
    chunks_count: 5,
    status: null,
    last_sync_at: null,
    created_at: null,
    ...overrides,
  }
}

function connectorSource(overrides: Partial<Source> = {}): Source {
  return {
    kind: 'connector',
    id: 'conn-1',
    name: 'Productdocs',
    type_label: 'Notion',
    connector_type: 'notion',
    items_count: 12,
    chunks_count: 42,
    status: 'success',
    last_sync_at: null,
    created_at: null,
    ...overrides,
  }
}

describe('useSourceSync', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    apiFetchMock.mockResolvedValue({})
  })
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('hits /uploads/{id}/reindex for an upload source', async () => {
    const wrapper = makeWrapper()
    const { result } = renderHook(() => useSourceSync('kb-a', uploadSource()), { wrapper })
    result.current.mutate()
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(apiFetchMock).toHaveBeenCalledWith(
      '/api/app/knowledge-bases/kb-a/uploads/art-1/reindex',
      { method: 'POST' },
    )
  })

  it('hits /connectors/{id}/sync for a connector source', async () => {
    const wrapper = makeWrapper()
    const { result } = renderHook(() => useSourceSync('kb-a', connectorSource()), { wrapper })
    result.current.mutate()
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(apiFetchMock).toHaveBeenCalledWith(
      '/api/app/knowledge-bases/kb-a/connectors/conn-1/sync',
      { method: 'POST' },
    )
  })
})

describe('useSourceDelete', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    apiFetchMock.mockResolvedValue(undefined)
  })
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('deletes an empty upload source via the upload endpoint', async () => {
    const wrapper = makeWrapper()
    const source = uploadSource({
      id: 'art-empty',
      chunks_count: 0,
      index_status: null,
      status: null,
    })
    const { result } = renderHook(() => useSourceDelete('kb-a', source), { wrapper })
    result.current.mutate()
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(apiFetchMock).toHaveBeenCalledWith(
      '/api/app/knowledge-bases/kb-a/uploads/art-empty',
      { method: 'DELETE' },
    )
  })

  it('deletes a connector source via the connector endpoint', async () => {
    const wrapper = makeWrapper()
    const { result } = renderHook(() => useSourceDelete('kb-a', connectorSource()), { wrapper })
    result.current.mutate()
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(apiFetchMock).toHaveBeenCalledWith(
      '/api/app/knowledge-bases/kb-a/connectors/conn-1',
      { method: 'DELETE' },
    )
  })
})

describe('useSourceRename', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
  })
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('calls onDone after a successful rename', async () => {
    apiFetchMock.mockResolvedValue({ artifact_id: 'art-1', display_name: 'new-name.pdf' })
    const wrapper = makeWrapper()
    const onDone = vi.fn()
    const { result } = renderHook(
      () => useSourceRename('kb-a', uploadSource(), onDone),
      { wrapper },
    )
    result.current.mutate('new-name.pdf')
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(onDone).toHaveBeenCalledTimes(1)
    expect(apiFetchMock).toHaveBeenCalledWith(
      '/api/app/knowledge-bases/kb-a/uploads/art-1',
      { method: 'PATCH', body: JSON.stringify({ name: 'new-name.pdf' }) },
    )
  })

  it('does NOT call onDone when the rename request fails', async () => {
    apiFetchMock.mockRejectedValue(new Error('500 internal'))
    const wrapper = makeWrapper()
    const onDone = vi.fn()
    const { result } = renderHook(
      () => useSourceRename('kb-a', uploadSource(), onDone),
      { wrapper },
    )
    result.current.mutate('new-name.pdf')
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(onDone).not.toHaveBeenCalled()
  })
})
