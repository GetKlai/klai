import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiFetchMock = vi.fn()

vi.mock('@/lib/apiFetch', async () => {
  const actual = await vi.importActual<typeof import('@/lib/apiFetch')>('@/lib/apiFetch')
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) }
})

vi.mock('@/lib/auth', () => ({
  useAuth: () => ({ isAuthenticated: true }),
}))

vi.mock('@/lib/locale', () => ({
  useLocale: () => ({ locale: 'en' }),
}))

vi.mock('@/paraglide/messages', () => ({
  admin_shared_loading: () => 'Loading',
  product_updates_label: () => 'Product updates',
  product_updates_label_unread: ({ count }: { count: string }) => `Product updates, ${count} unread`,
  product_updates_title: () => "What's new",
  product_updates_unread_count: ({ count }: { count: string }) => `${count} unread`,
  product_updates_all_read: () => "You're up to date",
  product_updates_mark_all_read: () => 'Mark all as read',
  product_updates_empty_title: () => 'No product updates yet',
  product_updates_empty_description: () => 'Recent Klai changes will appear here.',
  product_updates_error: () => 'Could not load product updates.',
  product_updates_not_available: () => 'Product updates are not available yet.',
  product_updates_back: () => 'Back',
}))

import { ProductUpdatesPopover } from '../ProductUpdatesPopover'

const response = {
  unread_count: 1,
  items: [
    {
      id: 12,
      title: 'Faster knowledge sync',
      body: 'Knowledge sources now show progress while syncing.',
      commit_shas: ['abc1234', 'def5678'],
      created_at: '2026-06-06T10:00:00Z',
      read_at: null,
      unread: true,
    },
  ],
}

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

beforeEach(() => {
  apiFetchMock.mockReset()
  apiFetchMock.mockImplementation((path: string) => {
    if (path === '/api/app/product-updates') return Promise.resolve(response)
    if (path === '/api/app/product-updates/12/read') return Promise.resolve(undefined)
    if (path === '/api/app/product-updates/read-all') return Promise.resolve(undefined)
    return Promise.reject(new Error(`Unexpected apiFetch: ${path}`))
  })
})

describe('ProductUpdatesPopover', () => {
  it('shows the unread indicator and opens the updates list', async () => {
    render(
      <Wrapper>
        <ProductUpdatesPopover />
      </Wrapper>,
    )

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Product updates, 1 unread' })).toBeTruthy(),
    )

    fireEvent.click(screen.getByRole('button', { name: /product updates/i }))

    expect(await screen.findByText("What's new")).toBeTruthy()
    expect(screen.getByText('Faster knowledge sync')).toBeTruthy()
    expect(screen.getByText('Knowledge sources now show progress while syncing.')).toBeTruthy()
  })

  it('marks an unread update as read when opening the detail', async () => {
    render(
      <Wrapper>
        <ProductUpdatesPopover />
      </Wrapper>,
    )

    fireEvent.click(await screen.findByRole('button', { name: /product updates/i }))
    fireEvent.click(await screen.findByText('Faster knowledge sync'))

    await waitFor(() => {
      expect(apiFetchMock).toHaveBeenCalledWith('/api/app/product-updates/12/read', { method: 'POST' })
    })
    expect(screen.queryByText('abc1234')).toBeNull()
    expect(screen.queryByText('def5678')).toBeNull()
  })

  it('marks all updates as read from the list header', async () => {
    render(
      <Wrapper>
        <ProductUpdatesPopover />
      </Wrapper>,
    )

    fireEvent.click(await screen.findByRole('button', { name: /product updates/i }))
    fireEvent.click(await screen.findByRole('button', { name: /mark all as read/i }))

    await waitFor(() => {
      expect(apiFetchMock).toHaveBeenCalledWith('/api/app/product-updates/read-all', { method: 'POST' })
    })
  })
})
