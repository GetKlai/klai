/**
 * Knowledge-gaps page contract tests - SPEC-KNOWLEDGE-ACTIVITY-001 §4.5/§4.9
 *
 * Covers the moved page (`/app/knowledge/gaps`): source column, language
 * filter forwarded to the API, inline "Sluiten" row action posting to
 * /api/app/gaps/resolve, the conversation drill-in behind the kb.activity
 * capability, and the legacy /app/gaps redirect.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

// Route search params are injected through the createFileRoute stub, so the
// page renders without a full router (repo route-test pattern).
const searchValue: { days?: number; gapType?: string; language?: string; include_resolved?: boolean } = {}
const navigate = vi.fn()

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
    }: {
      to: string
      params?: Record<string, string | number>
      children?: ReactNode
    }) => (
      <a href={to.replace('$conversationId', String(params?.conversationId ?? ''))}>{children}</a>
    ),
    createFileRoute: () => (cfg: unknown) => ({ ...(cfg as object), useSearch: () => searchValue }),
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

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
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
import { Route as LegacyGapsRoute } from '../gaps'

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
    // Default to a human-review row so the default (content) view renders it;
    // tests covering the automatic search-signals view set source explicitly.
    source: 'review',
    conversation_id: null,
    resolved_at: null,
    resolved_by: null,
    resolved_by_name: null,
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

/** Wait until the row is actually painted, not just requested. */
async function waitForRow(queryText = 'Wat zijn de openingstijden?') {
  await screen.findByText(queryText)
}

beforeEach(() => {
  navigate.mockReset()
  apiFetchMock.mockReset()
  capabilities.length = 0
  capabilities.push('kb.gaps')
  unlockedFeatures = ['widgets', 'knowledge_activity', 'knowledge_gaps']
  for (const key of Object.keys(searchValue) as Array<keyof typeof searchValue>) delete searchValue[key]
})

describe('GapsPage source column', () => {
  it('labels a review-sourced gap as beoordeling and an automatic one as automatisch', async () => {
    mockGaps([gapItem({ source: 'review' }), gapItem({ query_text: 'Andere vraag', source: 'automatic' })])

    render(
      <Wrapper>
        <GapsPage />
      </Wrapper>,
    )

    // The review row is an asserted gap: shown in the default content view.
    await waitFor(() => expect(screen.getByText(/beoordeling|review/i)).toBeTruthy())
    // The automatic signal only appears once the search-signals view is active.
    fireEvent.click(screen.getByRole('tab', { name: /zoeksignalen|search signals/i }))
    await screen.findByText('Andere vraag')
    expect(screen.getAllByText(/^(automatisch|automatic)$/i).length).toBeGreaterThan(0)
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
})

describe('GapsPage language filter', () => {
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
})

describe('GapsPage close action', () => {
  it('posts the row to /api/app/gaps/resolve and refetches the list', async () => {
    mockGaps([gapItem({ query_text: 'Wat zijn de openingstijden?', gap_type: 'hard', language: 'nl' })])

    render(
      <Wrapper>
        <GapsPage />
      </Wrapper>,
    )

    await waitForRow()
    fireEvent.click(screen.getByRole('button', { name: /sluiten|close gap/i }))

    // Inline confirmation appears before the request is made.
    const confirmButtons = await screen.findAllByRole('button', { name: /^(sluiten|close)$/i })
    expect(confirmButtons.length).toBeGreaterThan(0)
    fireEvent.click(confirmButtons[confirmButtons.length - 1])

    await waitFor(() =>
      expect(
        apiFetchMock.mock.calls.find((call) => call[0] === '/api/app/gaps/resolve'),
      ).toBeTruthy(),
    )
    const [, rawInit] = apiFetchMock.mock.calls.find((call) => call[0] === '/api/app/gaps/resolve')!
    const init = rawInit as { method?: string; body?: string }
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body ?? '{}')).toEqual({
      query_text: 'Wat zijn de openingstijden?',
      gap_type: 'hard',
      language: 'nl',
    })

    await waitFor(() => expect(gapsFetchUrls().length).toBeGreaterThan(1))
  })
})

describe('GapsPage support gap close', () => {
  it('closes the canonical group even when the displayed question is a paraphrase', async () => {
    mockGaps([
      gapItem({
        query_text: 'How do I export my invoices?',
        gap_type: 'content',
        language: 'en',
        source: 'support',
        diagnosis: 'missing',
        audience: 'customer',
        nearest_kb_slug: 'billing-kb',
        support_case_ids: [7],
        group_key: 'canonical-export-group',
      }),
      gapItem({
        query_text: 'How do I export my invoices?',
        gap_type: 'content',
        language: 'en',
        source: 'support',
        diagnosis: 'incomplete',
        audience: 'customer',
        nearest_kb_slug: 'billing-kb',
        support_case_ids: [8],
      }),
    ])

    render(
      <Wrapper>
        <GapsPage />
      </Wrapper>,
    )

    await screen.findAllByText('How do I export my invoices?')
    fireEvent.click(screen.getAllByRole('button', { name: /sluiten|close gap/i })[0])
    const confirmButtons = await screen.findAllByRole('button', { name: /^(sluiten|close)$/i })
    expect(confirmButtons).toHaveLength(1)
    fireEvent.click(confirmButtons[0])

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

  it('links to the support-case evidence page', async () => {
    mockGaps([
      gapItem({
        query_text: 'How do I export my invoices?',
        gap_type: 'content',
        source: 'support',
        diagnosis: 'missing',
        support_case_ids: [7],
      }),
    ])

    const { container } = render(
      <Wrapper>
        <GapsPage />
      </Wrapper>,
    )

    await waitForRow('How do I export my invoices?')
    expect(
      container.querySelector('a[href="/app/knowledge/gaps/support-cases/$caseId"]'),
    ).not.toBeNull()
  })
})

describe('GapsPage closed rows', () => {
  it('with include_resolved a closed row shows the badge and closer name and has no close button', async () => {
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
    expect(screen.queryByRole('button', { name: /sluiten|close gap/i })).toBeNull()
  })
})

describe('GapsPage conversation drill-in', () => {
  it('links to the activity conversation when the user has kb.activity', async () => {
    capabilities.push('kb.activity')
    mockGaps([gapItem({ conversation_id: 42 })])

    const { container } = render(
      <Wrapper>
        <GapsPage />
      </Wrapper>,
    )

    await waitForRow()
    expect(container.querySelector('a[href="/app/knowledge/activity/42"]')).not.toBeNull()
  })

  it('hides the conversation link without the kb.activity capability', async () => {
    mockGaps([gapItem({ conversation_id: 42 })])

    const { container } = render(
      <Wrapper>
        <GapsPage />
      </Wrapper>,
    )

    await waitForRow()
    expect(container.querySelector('a[href*="/app/knowledge/activity/"]')).toBeNull()
  })

  it('hides the conversation link with kb.activity but without the knowledge_activity unlock', async () => {
    capabilities.push('kb.activity')
    unlockedFeatures = ['widgets', 'knowledge_gaps']
    mockGaps([gapItem({ conversation_id: 42 })])

    const { container } = render(
      <Wrapper>
        <GapsPage />
      </Wrapper>,
    )

    await waitForRow()
    await waitFor(() =>
      expect(container.querySelector('a[href*="/app/knowledge/activity/"]')).toBeNull(),
    )
  })
})

describe('GapsPage content grouping', () => {
  it('groups content needs by topic and excludes automatic search signals until the view is switched', async () => {
    mockGaps([
      gapItem({
        query_text: 'Hoe behoud ik mijn nummer?',
        source: 'support',
        gap_type: 'content',
        diagnosis: 'missing',
        nearest_kb_slug: 'company-kb',
        support_case_ids: [1],
        group_key: 'g1',
        topic: { id: 10, name: 'Nummerbehoud' },
      }),
      gapItem({
        query_text: 'Wat kost porteren?',
        source: 'support',
        gap_type: 'content',
        diagnosis: 'incomplete',
        nearest_kb_slug: 'company-kb',
        support_case_ids: [2],
        group_key: 'g2',
        topic: { id: 10, name: 'Nummerbehoud' },
      }),
      gapItem({
        query_text: 'Hoe stel ik voicemail in?',
        source: 'review',
        nearest_kb_slug: 'company-kb',
        topic: { id: 20, name: 'Voicemail' },
      }),
      gapItem({ query_text: 'ruwe retrieval-misser', source: 'automatic', topic: null }),
    ])

    render(
      <Wrapper>
        <GapsPage />
      </Wrapper>,
    )

    // Default (content) view: two needs under one topic heading, plus a
    // distinct topic. Each need is rendered once.
    await screen.findByText('Nummerbehoud')
    expect(screen.getByText('Hoe behoud ik mijn nummer?')).toBeTruthy()
    expect(screen.getByText('Wat kost porteren?')).toBeTruthy()
    expect(screen.getByText('Voicemail')).toBeTruthy()
    // The raw automatic retrieval signal is not an asserted gap: hidden here.
    expect(screen.queryByText('ruwe retrieval-misser')).toBeNull()

    // Switching to the search-signals view reveals the automatic signal.
    fireEvent.click(screen.getByRole('tab', { name: /zoeksignalen|search signals/i }))
    await screen.findByText('ruwe retrieval-misser')
    expect(screen.queryByText('Wat kost porteren?')).toBeNull()
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
