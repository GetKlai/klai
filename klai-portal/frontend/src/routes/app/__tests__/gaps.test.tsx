/**
 * Knowledge-gaps screens contract tests - SPEC-KNOWLEDGE-ACTIVITY-001 §4.5/§4.9
 *
 * Covers the two-screen redesign: the list (`/app/knowledge/gaps`) with its
 * bron/periode/status filters and flat occurrence-sorted table, the detail
 * screen (`/app/knowledge/gaps/$groupKey`) with its stat cards, evidence links
 * and resolve action, and the legacy `/app/gaps` redirect.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

// Route search/params are injected through the createFileRoute stub, so each
// page renders without a full router (repo route-test pattern).
const searchValue: { days?: number; language?: string; include_resolved?: boolean } = {}
let paramsValue: { groupKey: string } = { groupKey: '' }
// `search` updaters read `e.target.value` live off the DOM node; React
// resets a controlled `<select>` back to its prop value right after the
// change event finishes dispatching, so the closure must be invoked
// synchronously inside the same dispatch (as the real router does), not
// stashed and called later once the DOM has already reverted.
let lastNavigateSearch: Record<string, unknown> | undefined
const navigate = vi.fn((opts: { search?: (prev: Record<string, unknown>) => Record<string, unknown> }) => {
  if (typeof opts?.search === 'function') lastNavigateSearch = opts.search({})
})

vi.mock('@tanstack/react-router', async () => {
  const actual = await vi.importActual<typeof import('@tanstack/react-router')>(
    '@tanstack/react-router',
  )
  return {
    ...actual,
    useNavigate: () => navigate,
    Link: ({
      to,
      params,
      children,
      ...rest
    }: {
      to: string
      params?: Record<string, string | number>
      children?: ReactNode
      [key: string]: unknown
    }) => {
      let href = to
      for (const [key, value] of Object.entries(params ?? {})) {
        href = href.replace(`$${key}`, String(value))
      }
      return (
        <a href={href} {...rest}>
          {children}
        </a>
      )
    },
    createFileRoute: () => (cfg: unknown) => ({
      ...(cfg as object),
      useSearch: () => searchValue,
      useParams: () => paramsValue,
    }),
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

const toastError = vi.fn()
vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: (...args: unknown[]) => toastError(...args) },
}))

const capabilities: string[] = ['kb.gaps']
vi.mock('@/hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({ user: { isAdmin: false, hasCapability: (cap: string) => capabilities.includes(cap) } }),
}))

// fetchMe goes through raw fetch, not apiFetch, so the tenant unlocks the
// drill-in link depends on are mocked at the module boundary (same pattern as
// ActivityTab.conversations-link.test.tsx).
let unlockedFeatures: string[] = ['widgets', 'knowledge_activity', 'knowledge_gaps']
vi.mock('@/lib/api-me', () => ({
  fetchMe: () =>
    Promise.resolve({
      portal_role: 'user',
      roles: [],
      capabilities: [],
      platform_unlocked_features: unlockedFeatures,
    }),
}))

import { GapsPage } from '../knowledge/gaps/index'
import { GapDetailPage } from '../knowledge/gaps/$groupKey'
import { Route as LegacyGapsRoute } from '../gaps'
import { gapGroupDigest } from '../knowledge/gaps/-gap-identity'

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

function gapItem(overrides: Record<string, unknown> = {}) {
  return {
    query_text: 'Wat zijn de openingstijden?',
    gap_type: 'soft',
    top_score: 0.21,
    nearest_kb_slug: 'company-kb',
    occurrence_count: 3,
    last_occurred: '2026-09-01T10:00:00Z',
    language: 'nl',
    // Default to a human-review row so it renders without any source filter.
    source: 'review',
    conversation_id: null,
    resolved_at: null,
    resolved_by: null,
    resolved_by_name: null,
    diagnosis: null,
    audience: null,
    support_case_ids: [],
    group_key: null,
    topic: null,
    ...overrides,
  }
}

function mockGaps(items: Array<Record<string, unknown>>) {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === '/api/app/gaps/resolve') return Promise.resolve({ resolved: 1 })
    if (path.startsWith('/api/app/gaps')) {
      return Promise.resolve({ gaps: items, total: items.length })
    }
    if (path === '/api/app/knowledge-bases') {
      return Promise.resolve({ knowledge_bases: [{ id: 1, name: 'Company KB', slug: 'company-kb', owner_type: 'org' }] })
    }
    return Promise.reject(new Error(`Unexpected apiFetch: ${path}`))
  })
}

function gapsFetchUrls(): string[] {
  return apiFetchMock.mock.calls
    .map((call) => String(call[0]))
    .filter((url) => url.startsWith('/api/app/gaps?'))
}

async function waitForRow(queryText = 'Wat zijn de openingstijden?') {
  await screen.findByText(queryText)
}

beforeEach(() => {
  navigate.mockClear()
  lastNavigateSearch = undefined
  apiFetchMock.mockReset()
  toastError.mockReset()
  capabilities.length = 0
  capabilities.push('kb.gaps')
  unlockedFeatures = ['widgets', 'knowledge_activity', 'knowledge_gaps']
  for (const key of Object.keys(searchValue) as Array<keyof typeof searchValue>) delete searchValue[key]
  paramsValue = { groupKey: '' }
})

describe('GapsPage list', () => {
  it('shows automatic and review rows together in one flat list, sorted as returned', async () => {
    mockGaps([gapItem({ source: 'review' }), gapItem({ query_text: 'Andere vraag', source: 'automatic' })])

    render(
      <Wrapper>
        <GapsPage />
      </Wrapper>,
    )

    await waitForRow()
    await screen.findByText('Andere vraag')
    expect(screen.getAllByText(/^(automatisch|automatic)$/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/^(beoordeling|review)$/i).length).toBeGreaterThan(0)
  })

  it('shows every support source vendor behind a grouped need', async () => {
    mockGaps([
      gapItem({
        source: 'support',
        gap_type: 'content',
        diagnosis: 'missing',
        support_case_ids: [7, 8],
        support_sources: ['audio', 'hubspot'],
      }),
    ])

    render(<Wrapper><GapsPage /></Wrapper>)

    await waitForRow()
    expect(screen.getByText(/gespreksopname|call recording/i)).toBeTruthy()
    expect(screen.getByText('HubSpot')).toBeTruthy()
  })

  it('forwards language from the URL search to the gaps fetch', async () => {
    searchValue.language = 'en'
    mockGaps([])

    render(
      <Wrapper>
        <GapsPage />
      </Wrapper>,
    )

    await waitFor(() => expect(gapsFetchUrls().length).toBeGreaterThan(0))
    expect(gapsFetchUrls()[0]).toContain('language=en')
  })

  it('filters the list to one source when the bron filter changes', async () => {
    mockGaps([gapItem({ source: 'review' }), gapItem({ query_text: 'Andere vraag', source: 'automatic' })])

    render(
      <Wrapper>
        <GapsPage />
      </Wrapper>,
    )

    await waitForRow()
    fireEvent.change(screen.getByLabelText(/bron|source/i), { target: { value: 'automatic' } })

    expect(screen.queryByText('Wat zijn de openingstijden?')).toBeNull()
    await screen.findByText('Andere vraag')
  })

  it('sets include_resolved when the status filter is switched to all', async () => {
    mockGaps([gapItem()])
    render(
      <Wrapper>
        <GapsPage />
      </Wrapper>,
    )

    await waitForRow()
    fireEvent.change(screen.getByLabelText(/^status$/i), { target: { value: 'all' } })

    expect(lastNavigateSearch).toEqual({ include_resolved: true })
  })

  it('with include_resolved a closed row shows the badge and closer name', async () => {
    searchValue.include_resolved = true
    mockGaps([
      gapItem({
        query_text: 'Wat kost Freedom?',
        resolved_at: '2026-09-10T08:00:00Z',
        resolved_by: 'manual',
        resolved_by_name: 'Klaas Klai',
      }),
    ])

    render(
      <Wrapper>
        <GapsPage />
      </Wrapper>,
    )

    await waitForRow('Wat kost Freedom?')
    expect(screen.getAllByText(/gesloten|closed/i).length).toBeGreaterThan(0)
    expect(screen.getByText(/Klaas Klai/)).toBeTruthy()
  })

  it('links a row to its detail screen by the group digest', async () => {
    const item = gapItem({ query_text: 'How do I export my invoices?' })
    mockGaps([item])

    const { container } = render(
      <Wrapper>
        <GapsPage />
      </Wrapper>,
    )

    await waitForRow('How do I export my invoices?')
    const expectedHref = `/app/knowledge/gaps/${gapGroupDigest(item)}`
    const links = Array.from(container.querySelectorAll('a'))
    expect(links.some((a) => a.getAttribute('href') === expectedHref)).toBe(true)
  })

  it('keeps the visitor question out of the detail URL', async () => {
    // A URL outlives the 7-day window the question itself lives under: it lands
    // in browser history, in the referrer of every link on the page and in the
    // proxy's access log (docs/privacy/telemetry-modes.md).
    const item = gapItem({ query_text: 'How do I export my invoices?' })
    mockGaps([item])

    const { container } = render(
      <Wrapper>
        <GapsPage />
      </Wrapper>,
    )

    await waitForRow('How do I export my invoices?')
    const hrefs = Array.from(container.querySelectorAll('a')).map((a) => a.getAttribute('href') ?? '')
    for (const href of hrefs) expect(href).not.toContain('invoices')
    const detailHrefs = hrefs.filter((href) => /^\/app\/knowledge\/gaps\/[^/]+$/.test(href) && href !== '/app/knowledge/gaps/support-cases')
    expect(detailHrefs.length).toBeGreaterThan(0)
    for (const href of detailHrefs) expect(href).toMatch(/^\/app\/knowledge\/gaps\/[0-9a-f]{16}$/)
  })
})

describe('/app/gaps legacy redirect', () => {
  it('redirects to /app/knowledge/gaps preserving search', () => {
    const beforeLoad = (LegacyGapsRoute as unknown as {
      beforeLoad: (ctx: { search: Record<string, unknown> }) => never
    }).beforeLoad

    let thrown: unknown
    try {
      beforeLoad({ search: { days: 14 } })
    } catch (e) {
      thrown = e
    }

    expect(thrown).toBeInstanceOf(Response)
    const response = thrown as Response & {
      options?: { to?: string; search?: Record<string, unknown> }
    }
    expect(response.status).toBe(307)
    expect(response.options?.to).toBe('/app/knowledge/gaps')
    expect(response.options?.search?.days).toBe(14)
  })
})

describe('GapDetailPage', () => {
  function setParamsFor(item: ReturnType<typeof gapItem>) {
    paramsValue = { groupKey: gapGroupDigest(item) }
  }

  it('shows the conversations, issue and existing-knowledge stat cards', async () => {
    const item = gapItem({ occurrence_count: 5, top_score: 0.42, nearest_kb_slug: 'billing-kb' })
    setParamsFor(item)
    mockGaps([item])

    render(<Wrapper><GapDetailPage /></Wrapper>)

    await screen.findByText('Wat zijn de openingstijden?')
    expect(screen.getByText('5')).toBeTruthy()
    expect(screen.getByText('billing-kb')).toBeTruthy()
    expect(screen.getByText(/42%/)).toBeTruthy()
  })

  it('links to the support-case evidence page for every case id', async () => {
    const item = gapItem({
      query_text: 'How do I export my invoices?',
      gap_type: 'content',
      source: 'support',
      diagnosis: 'missing',
      support_case_ids: [7, 9],
    })
    setParamsFor(item)
    mockGaps([item])

    const { container } = render(<Wrapper><GapDetailPage /></Wrapper>)

    await screen.findByText('How do I export my invoices?')
    expect(container.querySelector('a[href="/app/knowledge/gaps/support-cases/7"]')).not.toBeNull()
    expect(container.querySelector('a[href="/app/knowledge/gaps/support-cases/9"]')).not.toBeNull()
  })

  it('links to the activity conversation only with the kb.activity capability', async () => {
    const item = gapItem({ conversation_id: 42 })
    setParamsFor(item)
    mockGaps([item])

    const { container, rerender } = render(<Wrapper><GapDetailPage /></Wrapper>)
    await screen.findByText('Wat zijn de openingstijden?')
    expect(container.querySelector('a[href="/app/knowledge/activity/42"]')).toBeNull()

    capabilities.push('kb.activity')
    rerender(<Wrapper><GapDetailPage /></Wrapper>)
    await waitFor(() =>
      expect(container.querySelector('a[href="/app/knowledge/activity/42"]')).not.toBeNull(),
    )
  })

  it('posts the resolve body to /api/app/gaps/resolve after the confirm step, including group_key for a non-support gap', async () => {
    const item = gapItem({
      query_text: 'Wat zijn de openingstijden?',
      gap_type: 'hard',
      language: 'nl',
      group_key: 'telemetry-group-key',
    })
    setParamsFor(item)
    mockGaps([item])

    render(<Wrapper><GapDetailPage /></Wrapper>)

    await screen.findByText('Wat zijn de openingstijden?')
    fireEvent.click(screen.getByRole('button', { name: /sluiten|close gap/i }))
    const confirmButtons = await screen.findAllByRole('button', { name: /^(sluiten|close)$/i })
    fireEvent.click(confirmButtons[confirmButtons.length - 1])

    await waitFor(() =>
      expect(apiFetchMock.mock.calls.find((call) => call[0] === '/api/app/gaps/resolve')).toBeTruthy(),
    )
    const [, rawInit] = apiFetchMock.mock.calls.find((call) => call[0] === '/api/app/gaps/resolve')!
    const init = rawInit as { method?: string; body?: string }
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body ?? '{}')).toEqual({
      query_text: 'Wat zijn de openingstijden?',
      gap_type: 'hard',
      language: 'nl',
      diagnosis: null,
      audience: null,
      nearest_kb_slug: 'company-kb',
      group_key: 'telemetry-group-key',
    })
  })

  it('includes the support-group identity in the resolve body for a support-sourced gap', async () => {
    const item = gapItem({
      query_text: 'How do I export my invoices?',
      gap_type: 'content',
      language: 'en',
      source: 'support',
      diagnosis: 'missing',
      audience: 'customer',
      nearest_kb_slug: 'billing-kb',
      group_key: 'canonical-export-group',
    })
    setParamsFor(item)
    mockGaps([item])

    render(<Wrapper><GapDetailPage /></Wrapper>)

    await screen.findByText('How do I export my invoices?')
    fireEvent.click(screen.getByRole('button', { name: /sluiten|close gap/i }))
    const confirmButtons = await screen.findAllByRole('button', { name: /^(sluiten|close)$/i })
    fireEvent.click(confirmButtons[confirmButtons.length - 1])

    await waitFor(() =>
      expect(apiFetchMock.mock.calls.find((call) => call[0] === '/api/app/gaps/resolve')).toBeTruthy(),
    )
    const [, rawInit] = apiFetchMock.mock.calls.find((call) => call[0] === '/api/app/gaps/resolve')!
    const init = rawInit as { body?: string }
    expect(JSON.parse(init.body ?? '{}')).toEqual({
      query_text: 'How do I export my invoices?',
      gap_type: 'content',
      language: 'en',
      diagnosis: 'missing',
      audience: 'customer',
      nearest_kb_slug: 'billing-kb',
      group_key: 'canonical-export-group',
    })
  })

  it('shows a not-found state when the group key does not match any loaded gap', async () => {
    paramsValue = { groupKey: gapGroupDigest(gapItem({ query_text: 'Nooit opgeslagen' })) }
    mockGaps([])

    render(<Wrapper><GapDetailPage /></Wrapper>)

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalled())
    expect(await screen.findByText(/kon niet worden gevonden|could not be found/i)).toBeTruthy()
  })
})
