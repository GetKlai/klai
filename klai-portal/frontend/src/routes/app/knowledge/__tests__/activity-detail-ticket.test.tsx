/**
 * SPEC-KNOWLEDGE-ESCALATION-001 §5: the "Maak ticket" flow on the conversation
 * detail. A reviewer who spots a Sales/Finance ask must be able to turn the
 * reviewed conversation into a HubSpot ticket in a couple of clicks: save the
 * review, pick a target, see who HubSpot will attach it to, confirm. These
 * tests pin the contracts that make that true: no ticket UI at all without
 * `ticket.available`, the button saves the review before opening the panel,
 * the preview only fetches once the panel opens, a target with a created
 * ticket is never offered again, and a failed ticket's retry re-posts the
 * same target.
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

function baseDetail(overrides: Record<string, unknown> = {}) {
  return {
    id: 12,
    widget_id: 'w-1',
    widget_name: 'Website',
    channel: 'webchat',
    started_at: '2026-09-14T09:00:00Z',
    language: 'nl',
    is_test: false,
    visitor: { name: 'Ada L', email: 'ada@example.com' },
    quality: null,
    messages: [
      {
        id: 101,
        role: 'user',
        content: 'Wat kost een uitbreiding van 50 naar 100 zetels?',
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
        content: 'Daarvoor verwijs ik je door naar ons Sales-team.',
        sequence: 2,
        created_at: '2026-09-14T09:00:30Z',
        sources: null,
        rating: null,
        answer_signals: null,
        review: null,
      },
    ],
    ticket: { available: true, targets: [{ key: 'sales', label: 'Sales' }], tickets: [] },
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

function mockApi({
  detail,
  preview,
  createTicketResult,
}: {
  detail: Record<string, unknown>
  preview?: Record<string, unknown>
  createTicketResult?: () => Promise<unknown>
}) {
  apiFetchMock.mockImplementation((path: unknown, init?: RequestInit) => {
    const url = String(path)
    const method = init?.method ?? 'GET'
    if (method === 'POST' && url.endsWith('/tickets')) {
      return createTicketResult ? createTicketResult() : Promise.reject(new Error('no ticket mock'))
    }
    if (url.endsWith('/ticket-preview')) {
      return preview ? Promise.resolve(preview) : Promise.reject(new Error('no preview mock'))
    }
    if (method === 'PUT' && url.endsWith('/test')) return Promise.resolve({ is_test: true })
    if (method === 'PUT') return Promise.resolve(reviewFixture)
    if (url.startsWith('/api/app/activity/conversations/')) {
      return Promise.resolve(detail)
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

const ticketPostBody = () => {
  const call = apiFetchMock.mock.calls.find(
    ([path, init]) => String(path).endsWith('/tickets') && (init as RequestInit)?.method === 'POST',
  )
  if (!call) return undefined
  const raw = (call[1] as RequestInit).body
  return typeof raw === 'string' ? JSON.parse(raw) : raw
}

beforeEach(() => {
  apiFetchMock.mockReset()
})

describe('conversation detail ticket flow', () => {
  it('renders no ticket UI when ticket.available is false', async () => {
    mockApi({ detail: baseDetail({ ticket: { available: false, targets: [], tickets: [] } }) })
    renderPage()

    await waitFor(() =>
      expect(screen.getByText('Daarvoor verwijs ik je door naar ons Sales-team.')).toBeTruthy(),
    )
    expect(screen.queryByRole('button', { name: /maak ticket|create ticket/i })).toBeNull()
    expect(screen.queryByText(/e-?mail/i)).toBeNull()
  })

  it('shows the "Maak ticket" button when ticket.available is true', async () => {
    mockApi({ detail: baseDetail() })
    renderPage()

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /maak ticket|create ticket/i })).toBeTruthy(),
    )
  })

  it('saves the review, then opens the panel and fetches the preview', async () => {
    mockApi({
      detail: baseDetail(),
      preview: { contact: 'existing', lifecycle_stage: 'customer', contact_name: null, company_name: null },
    })
    renderPage()

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /maak ticket|create ticket/i })).toBeTruthy(),
    )
    fireEvent.click(screen.getByRole('button', { name: /^(klopt|correct)$/i }))
    fireEvent.click(screen.getByRole('button', { name: /maak ticket|create ticket/i }))

    await waitFor(() =>
      expect(
        apiFetchMock.mock.calls.some(
          ([path, init]) =>
            String(path) === `/api/app/activity/messages/${ASSISTANT_MESSAGE_ID}/review` &&
            (init as RequestInit)?.method === 'PUT',
        ),
      ).toBe(true),
    )
    await waitFor(() =>
      expect(screen.getByText(/bestaand contact|existing contact/i)).toBeTruthy(),
    )
    expect(screen.getByText(/klant|customer/i)).toBeTruthy()
  })

  it('posts the chosen target and does not offer a target with a created ticket again', async () => {
    mockApi({
      detail: baseDetail({
        ticket: {
          available: true,
          targets: [
            { key: 'sales', label: 'Sales' },
            { key: 'finance', label: 'Finance' },
          ],
          tickets: [
            {
              target_key: 'finance',
              target_label: 'Finance',
              status: 'created',
              ticket_url: 'https://app-eu1.hubspot.com/contacts/1/record/0-5/9',
              contact_status: 'existing',
              error: null,
              created_by_name: 'Ada L',
              created_at: '2026-09-15T09:00:00Z',
            },
          ],
        },
      }),
      preview: { contact: 'new', lifecycle_stage: null, contact_name: null, company_name: null },
      createTicketResult: () =>
        Promise.resolve({
          target_key: 'sales',
          target_label: 'Sales',
          status: 'created',
          ticket_url: 'https://app-eu1.hubspot.com/contacts/1/record/0-5/10',
          contact_status: 'created',
          error: null,
          created_by_name: 'Ada L',
          created_at: '2026-09-15T10:00:00Z',
        }),
    })
    renderPage()

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /maak ticket|create ticket/i })).toBeTruthy(),
    )
    // The Finance target already has a created ticket: history shows it once,
    // as the "open in HubSpot" link, never again as a pickable target.
    expect(screen.getByRole('link', { name: /openen in hubspot|open in hubspot/i })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /^(klopt|correct)$/i }))
    fireEvent.click(screen.getByRole('button', { name: /maak ticket|create ticket/i }))

    await waitFor(() => expect(screen.getByText(/nieuw contact|new contact/i)).toBeTruthy())
    expect(screen.queryByRole('button', { name: 'Finance' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Sales' }))
    fireEvent.click(screen.getByRole('button', { name: /^ticket aanmaken$|^confirm ticket$/i }))

    await waitFor(() => expect(ticketPostBody()).toEqual({ target_key: 'sales' }))
  })

  it('shows the failure reason and re-posts the same target on retry', async () => {
    let attempt = 0
    mockApi({
      detail: baseDetail({
        ticket: {
          available: true,
          targets: [{ key: 'sales', label: 'Sales' }],
          tickets: [
            {
              target_key: 'sales',
              target_label: 'Sales',
              status: 'failed',
              ticket_url: null,
              contact_status: null,
              error: 'HubSpot antwoordde niet.',
              created_by_name: null,
              created_at: '2026-09-15T09:00:00Z',
            },
          ],
        },
      }),
      createTicketResult: () => {
        attempt += 1
        return Promise.resolve({
          target_key: 'sales',
          target_label: 'Sales',
          status: 'created',
          ticket_url: 'https://app-eu1.hubspot.com/contacts/1/record/0-5/11',
          contact_status: 'existing',
          error: null,
          created_by_name: 'Ada L',
          created_at: '2026-09-15T10:00:00Z',
        })
      },
    })
    renderPage()

    await waitFor(() => expect(screen.getByText(/HubSpot antwoordde niet\./)).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /opnieuw proberen|try again/i }))

    await waitFor(() => expect(ticketPostBody()).toEqual({ target_key: 'sales' }))
    expect(attempt).toBe(1)
  })
})
