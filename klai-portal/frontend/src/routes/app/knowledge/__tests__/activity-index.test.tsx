/**
 * SPEC-KNOWLEDGE-ACTIVITY-001 §4.3 (fase 1b1) - the knowledge activity list.
 *
 * The screen exists so a knowledge admin can work a queue: the list is
 * server-paginated by cursor and every filter lives in the URL search, so a
 * filtered list stays shareable. These tests pin the contracts that make
 * that true: rows render the band and judge badges with human labels (not
 * raw codes), the repeatable filters travel through the URL as one
 * comma-joined value each, a row expands to show its judge/review detail,
 * and a user without the kb.activity capability never fetches.
 */
import { useEffect, useReducer, type ReactNode } from 'react'
import { describe, it, expect, vi, beforeEach, beforeAll, afterAll } from 'vitest'
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import * as m from '@/paraglide/messages'

// jsdom doesn't ship ResizeObserver or Element#scrollIntoView - cmdk (behind
// the failure-category/cause/band/judge-outcome MultiSelects) needs both.
class ResizeObserverPolyfill {
  observe() {}
  unobserve() {}
  disconnect() {}
}
beforeAll(() => {
  vi.stubGlobal('ResizeObserver', ResizeObserverPolyfill)
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = () => {}
  }
})
afterAll(() => {
  vi.unstubAllGlobals()
})

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

// fetchMe goes through raw fetch, not apiFetch, so the tenant unlocks the
// screen depends on are mocked at the module boundary (same pattern as
// ActivityTab.conversations-link.test.tsx and the gaps drill-in test).
let unlockedFeatures: string[] = ['widgets', 'knowledge_activity']
vi.mock('@/lib/api-me', () => ({
  fetchMe: () =>
    Promise.resolve({
      portal_role: 'user',
      roles: [],
      capabilities: [],
      platform_unlocked_features: unlockedFeatures,
    }),
}))

vi.mock('@/components/layout/ProductGuard', () => ({
  ProductGuard: ({ children }: { children: ReactNode }) => <>{children}</>,
}))

vi.mock('@/components/layout/RoleGuard', () => ({
  RoleGuard: ({ children }: { children: ReactNode }) => <>{children}</>,
}))

import { ActivityPage } from '../activity/index'
import { parseActivitySearch, stringifyActivitySearch, type ActivitySearch } from '../activity/-search'

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
    judge: { outcome: 'resolved', failure_category: null, confidence: 'high', reasoning: null },
    ratings: { up: 1, down: 0 },
    review: { status: 'unreviewed', worst_verdict: null, causes: [], reviews: [] },
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

/** Opens a MultiSelect field (identified by its Label text) and clicks the
    option with the given visible text — the cmdk popover mounts its items on
    open, so both steps need a `waitFor`. */
async function pickMultiSelectOption(fieldLabel: RegExp, optionText: RegExp) {
  const field = screen.getByText(fieldLabel).closest('div') as HTMLElement
  fireEvent.click(within(field).getByRole('button'))
  await waitFor(() => expect(screen.getByText(optionText)).toBeTruthy())
  fireEvent.click(screen.getByText(optionText))
}

beforeEach(() => {
  navigateMock.mockClear()
  apiFetchMock.mockReset()
  rerenderers.clear()
  currentUser.capabilities = ['kb.activity']
  unlockedFeatures = ['widgets', 'knowledge_activity']
  // What validateSearch guarantees for a bare visit to the screen.
  searchRef.current = {
    days: 7,
    sort: 'newest',
    judge_outcome: [],
    failure_category: [],
    cause: [],
    band: [],
  }
})

describe('activity list', () => {
  it('renders the mocked conversations with band and judge badges', async () => {
    mockConversations([
      item(),
      item({
        id: 2,
        first_user_query: 'Waarom is mijn bestelling vertraagd?',
        worst_band: 'low',
        judge: {
          outcome: 'unresolved',
          failure_category: 'retrieval_miss',
          confidence: 'medium',
          reasoning: null,
        },
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
  })

  it('shows the failure category as its human label, never the raw code', async () => {
    mockConversations([
      item({
        judge: {
          outcome: 'unresolved',
          failure_category: 'retrieval_miss',
          confidence: 'medium',
          reasoning: null,
        },
      }),
    ])

    renderPage()

    await waitFor(() => expect(screen.getByText('Wat is jullie retourbeleid?')).toBeTruthy())
    expect(screen.queryByText('retrieval_miss')).toBeNull()
    expect(screen.getByText(/kennis niet gevonden|knowledge not found/i)).toBeTruthy()
  })

  it('expands a row to show the reviewer note and the human verdict label', async () => {
    mockConversations([
      item({
        review: {
          status: 'reviewed',
          worst_verdict: 'wrong',
          causes: ['knowledge_missing'],
          reviews: [
            {
              verdict: 'wrong',
              cause: 'knowledge_missing',
              note: 'Sectie ontbreekt in de handleiding.',
              kb_slug: null,
              reviewer_name: 'Ada L',
              reviewed_at: '2026-09-14T09:10:00Z',
            },
          ],
        },
      }),
    ])

    renderPage()

    await waitFor(() => expect(screen.getByText('Wat is jullie retourbeleid?')).toBeTruthy())
    expect(screen.queryByText('Sectie ontbreekt in de handleiding.')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /details tonen|show details/i }))

    const detailRow = screen
      .getByText('Sectie ontbreekt in de handleiding.')
      .closest('tr') as HTMLElement
    // "Onjuist"/"Wrong" is the verdict label; the raw code never renders.
    expect(within(detailRow).getByText(/^(onjuist|wrong)$/i)).toBeTruthy()
    expect(screen.queryByText('wrong')).toBeNull()
  })

  it('carries the current filters into the detail so back keeps them', async () => {
    const filters: ActivitySearch = {
      days: 14,
      sort: 'newest',
      band: ['low'],
      judge_outcome: [],
      failure_category: [],
      cause: [],
    }
    searchRef.current = filters
    mockConversations([item()])

    renderPage()

    await waitFor(() => expect(screen.getByText('Wat is jullie retourbeleid?')).toBeTruthy())
    fireEvent.click(screen.getByText('Wat is jullie retourbeleid?'))

    expect(navigateMock).toHaveBeenCalledWith(
      expect.objectContaining({
        to: '/app/knowledge/activity/$conversationId',
        params: { conversationId: '1' },
        // One flat string, not a nested object: main.tsx's router-wide
        // stringifySearch only supports flat values.
        search: { back: stringifyActivitySearch(filters) },
      }),
    )
  })

  it('shows the failure category, cause and sort filters as multi-selects and sends them as search params', async () => {
    mockConversations([item()])

    renderPage()

    await waitFor(() => expect(conversationUrls().length).toBeGreaterThan(0))

    await pickMultiSelectOption(/foutcategorie|failure category/i, /kennis niet gevonden|knowledge not found/i)
    await waitFor(() =>
      expect(
        conversationUrls().some((url) => url.includes('failure_category=retrieval_miss')),
      ).toBe(true),
    )

    await pickMultiSelectOption(/^oorzaak$|^cause$/i, /kennis ontbreekt|knowledge missing/i)
    await waitFor(() =>
      expect(conversationUrls().some((url) => url.includes('cause=knowledge_missing'))).toBe(true),
    )

    fireEvent.change(screen.getByLabelText(/sort/i), { target: { value: 'worst' } })
    await waitFor(() =>
      expect(conversationUrls().some((url) => url.includes('sort=worst'))).toBe(true),
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

  it('shows the unlock-required message and never fetches /api/app/activity without the tenant unlock', async () => {
    unlockedFeatures = ['widgets']
    mockConversations([item()])

    renderPage()

    await waitFor(() => expect(screen.getByText(m.activity_unlock_required())).toBeTruthy())
    expect(
      apiFetchMock.mock.calls.some((call) => String(call[0]).startsWith('/api/app/activity')),
    ).toBe(false)
  })
})

describe('activity search contract', () => {
  it('defaults to the last 7 days, newest first, with no filters set', () => {
    expect(parseActivitySearch({})).toMatchObject({
      days: 7,
      sort: 'newest',
      judge_outcome: [],
      failure_category: [],
      cause: [],
      band: [],
    })
  })

  it('keeps only known filter values and drops anything else', () => {
    const parsed = parseActivitySearch({
      days: '90',
      judge_outcome: 'not-an-outcome',
      band: 'low',
      rating: 'thumbsDown',
      review_status: 'reviewed',
      sort: 'worst',
      cursor: 'c-9',
      bogus: 'x',
    })
    expect(parsed).toEqual({
      days: 7,
      widget_id: undefined,
      language: undefined,
      judge_outcome: [],
      failure_category: [],
      review_status: 'reviewed',
      cause: [],
      band: ['low'],
      rating: 'thumbsDown',
      sort: 'worst',
      cursor: 'c-9',
    })
  })

  it('round-trips a two-value failure_category filter through stringify and parse', () => {
    const search = parseActivitySearch({ failure_category: 'retrieval_miss,scope_mismatch' })
    expect(search.failure_category).toEqual(['retrieval_miss', 'scope_mismatch'])

    const qs = stringifyActivitySearch(search)
    const roundTripped = parseActivitySearch(Object.fromEntries(new URLSearchParams(qs)))
    expect(roundTripped.failure_category).toEqual(['retrieval_miss', 'scope_mismatch'])
  })
})
