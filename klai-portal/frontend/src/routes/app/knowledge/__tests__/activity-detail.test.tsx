/**
 * SPEC-KNOWLEDGE-ACTIVITY-001 §4.3 + Appendix A (fase 1b2) — the conversation
 * detail behind the knowledge activity list.
 *
 * A knowledge admin must be able to record, in two clicks per assistant answer,
 * whether it was right and why not, pre-filled from the nightly judge. These
 * tests pin the four contracts that make that true: the answer's confidence
 * signals and the judge panel render, the judge suggestion pre-selects the
 * verdict/cause pair, the review is written through the documented PUT contract
 * with a valid verdict/cause pair only, and the visitor block appears only when
 * the backend actually sent the `visitor` key.
 */
import { type ReactNode } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

vi.mock('@tanstack/react-router', async () => {
  const actual = await vi.importActual<typeof import('@tanstack/react-router')>(
    '@tanstack/react-router',
  )
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    createFileRoute: () => (cfg: unknown) => ({
      ...(cfg as object),
      useParams: () => ({ conversationId: '12' }),
      useSearch: () => ({}),
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

vi.mock('@/hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({
    user: { hasCapability: (cap: string) => cap === 'kb.activity' },
  }),
}))

// fetchMe goes through raw fetch, not apiFetch; the tenant is unlocked for
// every test here unless a test overrides it (SPEC §4.3 access model).
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

import { ActivityDetailPage } from '../activity/$conversationId'

const ASSISTANT_MESSAGE_ID = 102

function detail(overrides: Record<string, unknown> = {}) {
  return {
    id: 12,
    widget_id: 'w-1',
    widget_name: 'Website',
    channel: 'webchat',
    started_at: '2026-09-14T09:00:00Z',
    language: 'nl',
    is_test: false,
    visitor: { name: 'Ada L', email: 'ada@example.com' },
    quality: {
      outcome: 'unresolved',
      failure_category: 'retrieval_miss',
      reasoning: 'De kennisbank bevat het retourbeleid niet.',
      confidence: 'high',
      suggested_action: 'Voeg het retourbeleid toe.',
      judged_at: null,
    },
    messages: [
      {
        id: 101,
        role: 'user',
        content: 'Wat is jullie retourbeleid?',
        sequence: 1,
        created_at: '2026-09-14T09:00:00Z',
        sources: null,
        rating: null,
        answer_signals: null,
        review: null,
      },
      {
        id: ASSISTANT_MESSAGE_ID,
        role: 'assistant',
        content: 'Je kunt binnen 14 dagen retourneren.',
        sequence: 2,
        created_at: '2026-09-14T09:00:30Z',
        sources: [{ label: '1', title: 'FAQ', url: 'https://example.com/faq' }],
        rating: 'thumbsDown',
        answer_signals: {
          top_score: 0.312,
          band: 'low',
          gap_type: 'hard',
          sources_count: 2,
          refused: false,
          broad_mode: false,
          language: 'nl',
          model: 'gpt-4o-mini',
        },
        review: null,
      },
    ],
    ...overrides,
  }
}

const reviewFixture = {
  verdict: 'correct',
  cause: 'none',
  note: null,
  kb_slug: null,
  reviewer_name: 'Ada L',
  reviewed_at: '2026-09-15T09:00:00Z',
}

function mockApi(body: Record<string, unknown>) {
  apiFetchMock.mockImplementation((path: unknown, init?: RequestInit) => {
    const url = String(path)
    const method = init?.method ?? 'GET'
    if (method === 'PUT' && url.endsWith('/test')) return Promise.resolve({ is_test: true })
    if (method === 'PUT') return Promise.resolve(reviewFixture)
    if (method === 'DELETE') return Promise.resolve(null)
    if (url.startsWith('/api/app/activity/conversations/')) {
      return Promise.resolve(body)
    }
    if (url.startsWith('/api/app/knowledge-bases')) {
      return Promise.resolve({
        knowledge_bases: [
          { id: 1, name: 'Handleiding', slug: 'handleiding', owner_type: 'org' },
          { id: 2, name: 'Privacy', slug: 'privacy', owner_type: 'personal' },
        ],
      })
    }
    return Promise.reject(new Error(`Unexpected apiFetch: ${method} ${url}`))
  })
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <ActivityDetailPage />
    </QueryClientProvider>,
  )
}

const verdictButton = (name: RegExp) => screen.getByRole('button', { name })
const reviewRequestBody = () => {
  const call = apiFetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === 'PUT')
  if (!call) return undefined
  const raw = (call[1] as RequestInit).body
  return {
    path: String(call[0]),
    body: typeof raw === 'string' ? JSON.parse(raw) : raw,
  }
}

beforeEach(() => {
  apiFetchMock.mockReset()
})

describe('activity conversation detail', () => {
  it('renders the assistant turn with its low band badge and the judge reasoning', async () => {
    mockApi(detail())
    renderPage()

    await waitFor(() =>
      expect(screen.getByText('De kennisbank bevat het retourbeleid niet.')).toBeTruthy(),
    )

    // The confidence signals block is always visible now (no disclosure to open).
    expect(document.querySelector('details')).toBeNull()
    expect(screen.getByText(/^0\.31$/)).toBeTruthy()
    expect(screen.getByText(/^(laag|low)$/i).className).toContain('var(--color-warning)')
  })

  it('preselects verdict wrong and cause knowledge missing from a retrieval_miss judge row', async () => {
    mockApi(detail())
    renderPage()

    await waitFor(() => expect(screen.getByText('Je kunt binnen 14 dagen retourneren.')).toBeTruthy())

    expect(verdictButton(/^(onjuist|wrong)$/i).getAttribute('aria-pressed')).toBe('true')
    expect(
      verdictButton(/kennis ontbreekt|knowledge missing/i).getAttribute('aria-pressed'),
    ).toBe('true')
  })

  it('saves verdict correct as cause none through PUT on the message review endpoint', async () => {
    mockApi(detail())
    renderPage()

    await waitFor(() => expect(screen.getByText('Je kunt binnen 14 dagen retourneren.')).toBeTruthy())

    fireEvent.click(verdictButton(/^(klopt|correct)$/i))
    fireEvent.click(screen.getByRole('button', { name: /opslaan|save/i }))

    await waitFor(() => expect(reviewRequestBody()).toBeDefined())
    const request = reviewRequestBody()
    expect(request?.path).toBe(`/api/app/activity/messages/${ASSISTANT_MESSAGE_ID}/review`)
    expect(request?.body).toMatchObject({ verdict: 'correct', cause: 'none' })
  })

  it('keeps save disabled while verdict wrong has no cause', async () => {
    mockApi(detail({ quality: null }))
    renderPage()

    await waitFor(() => expect(screen.getByText('Je kunt binnen 14 dagen retourneren.')).toBeTruthy())

    fireEvent.click(verdictButton(/^(onjuist|wrong)$/i))
    expect(screen.getByRole('button', { name: /opslaan|save/i }).hasAttribute('disabled')).toBe(true)
  })

  it('renders no visitor block when the answer has no visitor key', async () => {
    const body = detail()
    delete (body as Record<string, unknown>).visitor
    mockApi(body)
    renderPage()

    await waitFor(() => expect(screen.getByText('Je kunt binnen 14 dagen retourneren.')).toBeTruthy())
    expect(screen.queryByText(/Ada L/)).toBeNull()
    expect(screen.queryByText(/ada@example\.com/)).toBeNull()
  })

  it('marks the conversation as a test message through PUT .../test after inline confirmation', async () => {
    mockApi(detail())
    renderPage()

    await waitFor(() => expect(screen.getByText('Je kunt binnen 14 dagen retourneren.')).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: /markeer als testbericht|mark as test message/i }))
    const confirmButtons = await screen.findAllByRole('button', {
      name: /markeer als testbericht|mark as test message/i,
    })
    fireEvent.click(confirmButtons[confirmButtons.length - 1])

    await waitFor(() =>
      expect(
        apiFetchMock.mock.calls.find(
          ([path, init]) => String(path).endsWith('/test') && (init as RequestInit)?.method === 'PUT',
        ),
      ).toBeTruthy(),
    )
    const [path, rawInit] = apiFetchMock.mock.calls.find(
      ([callPath, init]) => String(callPath).endsWith('/test') && (init as RequestInit)?.method === 'PUT',
    )!
    expect(String(path)).toBe('/api/app/activity/conversations/12/test')
    expect(JSON.parse((rawInit as RequestInit).body as string)).toEqual({ is_test: true })
  })

  it('hides the review form and shows the muted line for a test-marked conversation', async () => {
    mockApi(detail({ is_test: true }))
    renderPage()

    await waitFor(() => expect(screen.getByText('Je kunt binnen 14 dagen retourneren.')).toBeTruthy())

    expect(
      screen.getByText(/telt nergens mee en wordt niet beoordeeld|does not count anywhere and is not reviewed/i),
    ).toBeTruthy()
    expect(screen.queryByRole('button', { name: /opslaan|save/i })).toBeNull()
    // The badge and the unmark action stay visible.
    expect(screen.getByText(/^(testbericht|test message)$/i)).toBeTruthy()
    expect(screen.getByRole('button', { name: /toch geen testbericht|not a test message after all/i })).toBeTruthy()
  })
})
