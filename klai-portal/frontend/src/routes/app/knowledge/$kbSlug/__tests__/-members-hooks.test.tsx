import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { type ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useInviteUser, useKnowledgeBaseUpdate, useRemoveGroup } from '../-members-hooks'

vi.mock('@/lib/apiFetch', () => ({
  apiFetch: vi.fn(),
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

describe('members mutation hooks', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    apiFetchMock.mockResolvedValue({})
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('patches the knowledge base visibility settings', async () => {
    const wrapper = makeWrapper()
    const { result } = renderHook(() => useKnowledgeBaseUpdate('kb-a'), { wrapper })

    result.current.mutate({ visibility: 'internal', default_org_role: 'viewer' })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(apiFetchMock).toHaveBeenCalledWith(
      '/api/app/knowledge-bases/kb-a',
      {
        method: 'PATCH',
        body: JSON.stringify({ visibility: 'internal', default_org_role: 'viewer' }),
      },
    )
  })

  it('invites a user and calls the success reset callback', async () => {
    const wrapper = makeWrapper()
    const onInvited = vi.fn()
    const { result } = renderHook(() => useInviteUser('kb-a', onInvited), { wrapper })

    result.current.mutate({ email: 'person@example.com', role: 'viewer' })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(apiFetchMock).toHaveBeenCalledWith(
      '/api/app/knowledge-bases/kb-a/members/users',
      {
        method: 'POST',
        body: JSON.stringify({ email: 'person@example.com', role: 'viewer' }),
      },
    )
    expect(onInvited).toHaveBeenCalledTimes(1)
  })

  it('deletes a group member by membership id', async () => {
    const wrapper = makeWrapper()
    const { result } = renderHook(() => useRemoveGroup('kb-a'), { wrapper })

    result.current.mutate(42)

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(apiFetchMock).toHaveBeenCalledWith(
      '/api/app/knowledge-bases/kb-a/members/groups/42',
      { method: 'DELETE' },
    )
  })
})
