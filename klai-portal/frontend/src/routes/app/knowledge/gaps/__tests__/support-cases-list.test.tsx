/**
 * Support-case list contract (support-gap-detection.md, shared UI contract):
 * the list surfaces EVERY imported case — uncertain, covered/no-gap, failed and
 * pending — not just the ones that produced gap rows, and each case keeps its
 * own status. The KB picker offers organisation KBs only and drives the fetch.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

const searchValue: { kbSlug?: string; offset?: number } = {}
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
    }) => <a href={to.replace('$caseId', String(params?.caseId ?? ''))}>{children}</a>,
    createFileRoute: () => (cfg: unknown) => ({ ...(cfg as object), useSearch: () => searchValue }),
  }
})

const apiFetchMock = vi.fn()
vi.mock('@/lib/apiFetch', async () => {
  const actual = await vi.importActual<typeof import('@/lib/apiFetch')>('@/lib/apiFetch')
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) }
})

vi.mock('@/lib/auth', () => ({ useAuth: () => ({ isAuthenticated: true }) }))

const capabilities: string[] = ['kb.gaps']
vi.mock('@/hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({ user: { isAdmin: false, hasCapability: (c: string) => capabilities.includes(c) } }),
}))

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

import { SupportCasesListPage } from '../support-cases.index'

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

function caseRow(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    kb_slug: 'company-kb',
    subject: 'How do I export invoices?',
    source: 'hubspot',
    status: 'analyzed',
    imported_at: '2026-09-10T08:00:00Z',
    mediums: ['email'],
    question_count: 2,
    uncertain_count: 0,
    reviewed_count: 0,
    ...overrides,
  }
}

function mockList(cases: Array<Record<string, unknown>>) {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === '/api/app/knowledge-bases') {
      return Promise.resolve({
        knowledge_bases: [
          { id: 1, name: 'Company KB', slug: 'company-kb', owner_type: 'org' },
          { id: 2, name: 'My personal KB', slug: 'personal-kb', owner_type: 'user' },
        ],
      })
    }
    if (path.includes('/support-cases')) {
      return Promise.resolve({ cases, total: cases.length })
    }
    return Promise.reject(new Error(`Unexpected apiFetch: ${path}`))
  })
}

function casesFetchUrls(): string[] {
  return apiFetchMock.mock.calls
    .map((c) => String(c[0]))
    .filter((u) => u.includes('/support-cases'))
}

beforeEach(() => {
  navigate.mockReset()
  apiFetchMock.mockReset()
  capabilities.length = 0
  capabilities.push('kb.gaps')
  unlockedFeatures = ['widgets', 'knowledge_activity', 'knowledge_gaps']
  for (const k of Object.keys(searchValue) as Array<keyof typeof searchValue>) delete searchValue[k]
})

describe('SupportCasesListPage', () => {
  it('lists uncertain, covered/no-gap, failed and pending cases, each with its own status', async () => {
    searchValue.kbSlug = 'company-kb'
    mockList([
      caseRow({ id: 1, subject: 'Case with an uncertain outcome', uncertain_count: 1 }),
      caseRow({ id: 2, subject: 'Case the KB already covered', question_count: 1 }),
      caseRow({ id: 3, subject: 'Case three', status: 'failed', question_count: 0 }),
      caseRow({ id: 4, subject: 'Case four', status: 'pending', question_count: 0 }),
    ])

    render(
      <Wrapper>
        <SupportCasesListPage />
      </Wrapper>,
    )

    await screen.findByText('Case with an uncertain outcome')
    expect(screen.getByText('Case the KB already covered')).toBeTruthy()
    expect(screen.getByText('Case three')).toBeTruthy()
    expect(screen.getByText('Case four')).toBeTruthy()
    // The failed and pending states are shown distinctly, not as empty analyses.
    expect(screen.getByText(/mislukt|failed/i)).toBeTruthy()
    expect(screen.getByText(/behandeling|pending/i)).toBeTruthy()
  })

  it('fetches the selected KB and offers organisation KBs only in the picker', async () => {
    searchValue.kbSlug = 'company-kb'
    mockList([caseRow()])

    render(
      <Wrapper>
        <SupportCasesListPage />
      </Wrapper>,
    )

    await waitFor(() => expect(casesFetchUrls().length).toBeGreaterThan(0))
    expect(casesFetchUrls()[0]).toContain('/api/app/knowledge-bases/company-kb/support-cases')
    // Personal KB (owner_type=user) must not be an option.
    await screen.findByRole('option', { name: 'Company KB' })
    expect(screen.queryByRole('option', { name: 'My personal KB' })).toBeNull()
  })

  it('shows the capability-unavailable message, not an empty case list, without kb.gaps', async () => {
    searchValue.kbSlug = 'company-kb'
    capabilities.length = 0
    mockList([])

    render(
      <Wrapper>
        <SupportCasesListPage />
      </Wrapper>,
    )

    await screen.findByText(/Available on Klai Knowledge|Beschikbaar op Klai Knowledge/i)
    expect(screen.queryByText(/No support cases|geen support-cases/i)).toBeNull()
    // A user without the capability triggers no support-case fetch.
    expect(casesFetchUrls()).toHaveLength(0)
  })

  it('shows a loading state, not an empty case list, while the knowledge-base query is unresolved', async () => {
    searchValue.kbSlug = 'company-kb'
    apiFetchMock.mockImplementation((path: string) => {
      if (path === '/api/app/knowledge-bases') return new Promise(() => {})
      if (path.includes('/support-cases')) return Promise.resolve({ cases: [], total: 0 })
      return Promise.reject(new Error(`Unexpected apiFetch: ${path}`))
    })

    render(
      <Wrapper>
        <SupportCasesListPage />
      </Wrapper>,
    )

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/api/app/knowledge-bases'))
    await screen.findByText(/Loading|Laden/i)
    expect(screen.queryByText(/No support cases|geen support-cases/i)).toBeNull()
  })

  it('shows a retry on a failed knowledge-base query and lists cases after a successful retry', async () => {
    searchValue.kbSlug = 'company-kb'
    let kbCalls = 0
    apiFetchMock.mockImplementation((path: string) => {
      if (path === '/api/app/knowledge-bases') {
        kbCalls += 1
        if (kbCalls === 1) return Promise.reject(new Error('kb boom'))
        return Promise.resolve({
          knowledge_bases: [{ id: 1, name: 'Company KB', slug: 'company-kb', owner_type: 'org' }],
        })
      }
      if (path.includes('/support-cases')) return Promise.resolve({ cases: [caseRow()], total: 1 })
      return Promise.reject(new Error(`Unexpected apiFetch: ${path}`))
    })

    render(
      <Wrapper>
        <SupportCasesListPage />
      </Wrapper>,
    )

    const retry = await screen.findByRole('button', { name: /Try again|Opnieuw proberen/i })
    expect(screen.queryByText(/No support cases|geen support-cases/i)).toBeNull()

    fireEvent.click(retry)
    await screen.findByText('How do I export invoices?')
  })

  it('links a case row to its evidence detail page', async () => {
    searchValue.kbSlug = 'company-kb'
    mockList([caseRow({ id: 42 })])

    const { container } = render(
      <Wrapper>
        <SupportCasesListPage />
      </Wrapper>,
    )

    await screen.findByText('How do I export invoices?')
    expect(container.querySelector('a[href="/app/knowledge/gaps/support-cases/42"]')).not.toBeNull()
  })
})
