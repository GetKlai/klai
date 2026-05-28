import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiFetchMock = vi.fn()
vi.mock('@/lib/apiFetch', async () => {
  const actual = await vi.importActual<typeof import('@/lib/apiFetch')>('@/lib/apiFetch')
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) }
})

vi.mock('@tanstack/react-router', async () => {
  const actual = await vi.importActual<typeof import('@tanstack/react-router')>('@tanstack/react-router')
  return {
    ...actual,
    Link: ({ children }: { children: ReactNode }) => <a href="/app/knowledge">{children}</a>,
  }
})

vi.mock('@/paraglide/messages', () => ({
  chatbar_collections_general_ai: () => 'General AI',
  chatbar_collections_label: () => 'Collections',
  chatbar_collections_all_off: () => 'All off',
  chatbar_collections_all_on: () => 'All on',
  chatbar_collections_manage: () => 'Manage',
  chatbar_mode_label: () => 'Mode',
  chatbar_mode_narrow_on: () => 'Strict',
  chatbar_mode_narrow_off: () => 'Open',
  chatbar_mode_broad_description: () => 'Use KB plus general knowledge.',
  chatbar_mode_narrow_description: () => 'Use only the KB.',
  chatbar_instructions_label: () => 'Instructions',
  chatbar_instructions_empty: () => 'None',
  chatbar_instructions_clear: () => 'Clear',
}))

import { ChatConfigBar } from '../ChatConfigBar'

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

function mockApi({
  kbs,
  kbNarrow = false,
}: {
  kbs: Array<{ slug: string; name: string }>
  kbNarrow?: boolean
}) {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === '/api/app/account/kb-preference') {
      return Promise.resolve({
        kb_retrieval_enabled: true,
        kb_personal_enabled: true,
        kb_slugs_filter: null,
        kb_narrow: kbNarrow,
        kb_pref_version: 1,
        active_template_ids: null,
      })
    }
    if (path === '/api/app/knowledge-bases') {
      return Promise.resolve({ knowledge_bases: kbs })
    }
    if (path === '/api/app/templates') return Promise.resolve([])
    return Promise.reject(new Error(`Unexpected apiFetch: ${path}`))
  })
}

beforeEach(() => {
  apiFetchMock.mockReset()
})

describe('ChatConfigBar', () => {
  it('keeps the Open/Strict toggle visible for personal-only workspaces', async () => {
    mockApi({ kbs: [{ slug: 'personal-user-1', name: 'Persoonlijk' }] })

    render(
      <Wrapper>
        <ChatConfigBar />
      </Wrapper>,
    )

    await waitFor(() => expect(screen.getByRole('radiogroup', { name: 'Mode' })).toBeTruthy())
    expect(screen.getByRole('radio', { name: 'Open' })).toBeTruthy()
    expect(screen.getByRole('radio', { name: 'Strict' })).toBeTruthy()
  })
})
