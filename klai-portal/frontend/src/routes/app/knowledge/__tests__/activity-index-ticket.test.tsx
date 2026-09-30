/**
 * SPEC-KNOWLEDGE-ESCALATION-001 §5: the activity list's ticket badge and
 * has_ticket filter. A reviewer scanning the queue must see at a glance which
 * conversations already escalated to a ticket, and be able to filter the
 * queue down to just those (or just the ones still missing one).
 */
import { useEffect, useReducer, type ReactNode } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const searchRef = { current: {} as Record<string, unknown> }
const rerenderers = new Set<() => void>()

const navigateMock = vi.fn((next: { search?: unknown }) => {
  const patch =
    typeof next?.search === 'function'
      ? (next.search as (prev: Record<string, unknown>) => Record<string, unknown>)(searchRef.current)
      : next?.search
  searchRef.current = { ...searchRef.current, ...(patch ?? {}) }
  rerenderers.forEach((rerender) => rerender())
})

vi.mock('@tanstack/react-router', async () => {
  const actual = await vi.importActual<typeof import('@tanstack/react-router')>(
    '@tanstack/react-router',
  )
  return {
    ...actual,
    useNavigate: () => navigateMock,
    createFileRoute: () => (cfg: unknown) => ({
      ...(cfg as object),
      useSearch: () => searchRef.current,
    }),
    Link: ({ children }: { children?: ReactNode }) => <a>{children}</a>,
  }
})

const apiFetchMock = vi.fn()
vi.mock('@/lib/apiFetch', async () => {
  const actual = await vi.importActual<typeof import('@/lib/apiFetch')>('@/lib/apiFetch')
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) }
})

vi.mock('@/lib/auth', () => ({
  useAuth: () => ({ isAuthenticated: true }),
}))

const currentUser = {
  capabilities: ['kb.activity'] as string[],
  hasCapability: (cap: string) => currentUser.capabilities.includes(cap),
}
vi.mock('@/hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({ user: currentUser }),
}))

vi.mock('@/lib/api-me', () => ({
  fetchMe: () =>
    Promise.resolve({
      portal_role: 'user',
      roles: [],
      capabilities: [],
      platform_unlocked_features: ['widgets', 'knowledge_activity'],
    }),
}))

vi.mock('@/components/layout/ProductGuard', () => ({
  ProductGuard: ({ children }: { children: ReactNode }) => <>{children}</>,
}))

vi.mock('@/components/layout/RoleGuard', () => ({
  RoleGuard: ({ children }: { children: ReactNode }) => <>{children}</>,
}))

import { ActivityPage } from '../activity/index'

function Harness() {
  const [tick, bump] = useReducer((n: number) => n + 1, 0)
  useEffect(() => {
    rerenderers.add(bump)
    return () => {
      rerenderers.delete(bump)
    }
  }, [bump])
  return <ActivityPage key={tick} />
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <Harness />
    </QueryClientProvider>,
  )
}

function item(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    widget_id: 'w-1',
    widget_name: 'Website',
    channel: 'webchat',
    started_at: '2026-09-14T09:00:00Z',
    last_message_at: '2026-09-14T09:05:00Z',
    message_count: 4,
    first_user_query: 'Wat kost een uitbreiding?',
    language: 'nl',
    worst_band: 'high',
    judge: null,
    ratings: { up: 0, down: 0 },
    review: { status: 'unreviewed', worst_verdict: null, causes: [], reviews: [] },
    open_gap_count: 0,
    ticket_labels: [],
    ...overrides,
  }
}

function mockConversations(items: Array<Record<string, unknown>>) {
  apiFetchMock.mockImplementation((path: unknown) => {
    const url = String(path)
    if (url.startsWith('/api/app/activity/conversations')) {
      return Promise.resolve({ items, next_cursor: null })
    }
    if (url.startsWith('/api/app/activity/queue-count')) {
      return Promise.resolve({ count: items.length })
    }
    return Promise.reject(new Error(`Unexpected apiFetch: ${url}`))
  })
}

const conversationUrls = () =>
  apiFetchMock.mock.calls
    .map((call) => String(call[0]))
    .filter((url) => url.startsWith('/api/app/activity/conversations'))

beforeEach(() => {
  navigateMock.mockClear()
  apiFetchMock.mockReset()
  rerenderers.clear()
  currentUser.capabilities = ['kb.activity']
  searchRef.current = {
    days: 7,
    sort: 'newest',
    judge_outcome: [],
    failure_category: [],
    cause: [],
    band: [],
  }
})

describe('activity list ticket badge and filter', () => {
  it('shows a badge per ticket label on the row', async () => {
    mockConversations([item({ ticket_labels: ['Sales'] })])
    renderPage()

    await waitFor(() => expect(screen.getByText('Wat kost een uitbreiding?')).toBeTruthy())
    expect(screen.getByText(/ticket:\s*sales/i)).toBeTruthy()
  })

  it('renders no ticket badge for a conversation without one', async () => {
    mockConversations([item()])
    renderPage()

    await waitFor(() => expect(screen.getByText('Wat kost een uitbreiding?')).toBeTruthy())
    expect(screen.queryByText(/ticket:/i)).toBeNull()
  })

  it('sends has_ticket in the request URL when the filter is set', async () => {
    mockConversations([item()])
    renderPage()

    await waitFor(() => expect(conversationUrls().length).toBeGreaterThan(0))

    fireEvent.change(screen.getByLabelText(/^ticket$/i), { target: { value: 'true' } })
    await waitFor(() => expect(conversationUrls().some((url) => url.includes('has_ticket=true'))).toBe(true))

    fireEvent.change(screen.getByLabelText(/^ticket$/i), { target: { value: 'false' } })
    await waitFor(() => expect(conversationUrls().some((url) => url.includes('has_ticket=false'))).toBe(true))
  })
})
