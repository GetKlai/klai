/**
 * SPEC-KNOWLEDGE-ACTIVITY-001 §4.3 (fase 1b1) - the knowledge activity list.
 *
 * The screen exists so a knowledge admin can work a queue: the list is
 * server-paginated by cursor and every filter lives in the URL search, so a
 * filtered list stays shareable. These tests pin the three contracts that make
 * that true: rows render the band and judge badges, the queue toggle travels
 * through the URL into the request, and a user without the kb.activity
 * capability never fetches.
 */
import { useEffect, useReducer, type ReactNode } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

// The page reads its filters through the route's validated search, so the test
// harness keeps a validated-shaped search object and re-renders whenever a
// filter control pushes a new one (which is exactly what a URL change does).
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
  capabilities: [] as string[],
  hasCapability: (cap: string) => currentUser.capabilities.includes(cap),
}
vi.mock('@/hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({ user: currentUser }),
}))

vi.mock('@/components/layout/ProductGuard', () => ({
  ProductGuard: ({ children }: { children: ReactNode }) => <>{children}</>,
}))

vi.mock('@/components/layout/RoleGuard', () => ({
  RoleGuard: ({ children }: { children: ReactNode }) => <>{children}</>,
}))

import { ActivityPage } from '../activity/index'
import { parseActivitySearch } from '../activity/-search'

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
    first_user_query: 'Wat is jullie retourbeleid?',
    language: 'nl',
    worst_band: 'high',
    judge: { outcome: 'resolved', failure_category: null, confidence: 'high' },
    ratings: { up: 1, down: 0 },
    review: { status: 'unreviewed', worst_verdict: null, causes: [] },
    open_gap_count: 0,
    ...overrides,
  }
}

function mockConversations(items: Array<Record<string, unknown>>, nextCursor: string | null = null) {
  apiFetchMock.mockImplementation((path: unknown) => {
    const url = String(path)
    if (url.startsWith('/api/app/activity/conversations')) {
      return Promise.resolve({ items, next_cursor: nextCursor })
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
  // What validateSearch guarantees for a bare visit to the screen.
  searchRef.current = { days: 7, queue: true, sort: 'newest' }
})

describe('activity list', () => {
  it('renders the mocked conversations with band and judge badges', async () => {
    mockConversations([
      item(),
      item({
        id: 2,
        first_user_query: 'Waarom is mijn bestelling vertraagd?',
        worst_band: 'low',
        judge: { outcome: 'unresolved', failure_category: 'retrieval_miss', confidence: 'medium' },
      }),
    ])

    const { container } = renderPage()

    await waitFor(() => expect(screen.getByText('Wat is jullie retourbeleid?')).toBeTruthy())
    expect(screen.getByText('Waarom is mijn bestelling vertraagd?')).toBeTruthy()
    expect(container.querySelector('table')).not.toBeNull()

    const lowRow = screen
      .getByText('Waarom is mijn bestelling vertraagd?')
      .closest('tr') as HTMLElement
    expect(within(lowRow).getByText(/laag|low/i).className).toContain('var(--color-warning)')

    const judgeCell = within(lowRow).getByText(/onopgelost|unresolved/i)
    expect(judgeCell.className).not.toContain('var(--color-success-text)')
    expect(within(lowRow).getByText('retrieval_miss')).toBeTruthy()
  })

  it('sends queue=true by default and queue=false after toggling the queue switch', async () => {
    mockConversations([item()])

    renderPage()

    await waitFor(() => expect(conversationUrls().length).toBeGreaterThan(0))
    expect(conversationUrls().some((url) => url.includes('queue=true'))).toBe(true)

    fireEvent.click(screen.getByRole('switch', { name: /werkvoorraad|queue/i }))

    await waitFor(() =>
      expect(conversationUrls().some((url) => url.includes('queue=false'))).toBe(true),
    )
  })

  it('shows the disabled placeholder and fetches nothing without kb.activity', async () => {
    currentUser.capabilities = []
    mockConversations([item()])

    const { container } = renderPage()

    await waitFor(() =>
      expect(container.querySelector('[aria-disabled="true"]')).not.toBeNull(),
    )
    expect(apiFetchMock).not.toHaveBeenCalled()
  })
})

describe('activity search contract', () => {
  it('defaults to the work queue over the last 7 days, newest first', () => {
    expect(parseActivitySearch({})).toMatchObject({ days: 7, queue: true, sort: 'newest' })
  })

  it('keeps only known filter values and drops anything else', () => {
    const parsed = parseActivitySearch({
      days: '90',
      judge_outcome: 'not-an-outcome',
      band: 'low',
      rating: 'thumbsDown',
      review_status: 'reviewed',
      queue: 'false',
      sort: 'worst',
      cursor: 'c-9',
      bogus: 'x',
    })
    expect(parsed).toEqual({
      days: 7,
      widget_id: undefined,
      language: undefined,
      judge_outcome: undefined,
      failure_category: undefined,
      review_status: 'reviewed',
      cause: undefined,
      band: 'low',
      rating: 'thumbsDown',
      queue: false,
      sort: 'worst',
      cursor: 'c-9',
    })
    expect(parseActivitySearch({ queue: false }).queue).toBe(false)
    expect(parseActivitySearch({ queue: true }).queue).toBe(true)
  })
})
