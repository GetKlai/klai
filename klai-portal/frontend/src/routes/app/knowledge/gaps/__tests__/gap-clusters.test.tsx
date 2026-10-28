/**
 * Weekly cluster screens (knowledge-gap-to-article phase 1): the list shows the
 * clusters exactly as the API returns them, the detail shows the questions and
 * the proposal, and a decision is final. apiFetch is mocked; the backend
 * contract is GET /api/app/gaps/clusters and, per cluster key (URL-encoded),
 * GET/POST .../proposal and POST .../decision.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

let paramsValue: { clusterKey: string } = { clusterKey: '' }
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
      params?: Record<string, string>
      children?: ReactNode
    }) => <a href={to.replace('$clusterKey', params?.clusterKey ?? '')}>{children}</a>,
    createFileRoute: () => (cfg: unknown) => ({ ...(cfg as object), useParams: () => paramsValue }),
  }
})

const apiFetchMock = vi.fn()
vi.mock('@/lib/apiFetch', async () => {
  const actual = await vi.importActual<typeof import('@/lib/apiFetch')>('@/lib/apiFetch')
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) }
})

vi.mock('@/lib/auth', () => ({ useAuth: () => ({ isAuthenticated: true }) }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

vi.mock('@/hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({ user: { isAdmin: false, hasCapability: (cap: string) => cap === 'kb.gaps' } }),
}))
vi.mock('@/lib/api-me', () => ({
  fetchMe: () =>
    Promise.resolve({
      portal_role: 'user',
      roles: [],
      capabilities: [],
      platform_unlocked_features: ['widgets', 'knowledge_activity', 'knowledge_gaps'],
    }),
}))

import { ApiError } from '@/lib/apiFetch'
import { GapClustersPage } from '../clusters.index'
import { GapClusterDetailPage } from '../clusters.$clusterKey'

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

const KEY = 'billing|invoice-export'
const ENC = encodeURIComponent(KEY)

function cluster(overrides: Record<string, unknown> = {}) {
  return {
    cluster_key: KEY,
    product: 'Facturatie',
    topic: 'Facturen exporteren',
    count: 5,
    widget_count: 4,
    internal_count: 1,
    kind: 'article_misses_part',
    kinds: { article_misses_part: 5 },
    question: 'Hoe exporteer ik al mijn facturen in een keer?',
    questions: ['Hoe exporteer ik al mijn facturen in een keer?', 'Kan ik facturen als csv downloaden?'],
    article: { title: 'Facturen bekijken', url: 'https://docs.example.com/facturen', score: 0.62 },
    articles: [{ title: 'Facturen bekijken', url: 'https://docs.example.com/facturen', score: 0.62 }],
    open_parts: ['Exporteren naar csv'],
    language: 'nl',
    last_seen: '2026-09-20T10:00:00Z',
    decision: 'none',
    decided_by: null,
    decided_at: null,
    has_proposal: false,
    ...overrides,
  }
}

function proposal(overrides: Record<string, unknown> = {}) {
  return {
    id: 3,
    cluster_key: KEY,
    missing: 'Een uitleg over het exporteren van facturen.',
    where_kind: 'existing',
    where_title: 'Facturen bekijken',
    where_url: 'https://docs.example.com/facturen',
    where_section: 'Exporteren',
    example_text: '## Facturen exporteren\n\nKies **Exporteer** rechtsboven.',
    open_facts: ['Het exacte menupad'],
    language: 'nl',
    model: 'synthetic-model',
    generated_at: '2026-09-21T09:00:00Z',
    decision: 'none',
    decided_by: null,
    decided_at: null,
    ...overrides,
  }
}

const notFound = () => Promise.reject(new ApiError(404, 'Not found'))

function mockApi(opts: {
  clusters?: Array<Record<string, unknown>>
  get?: () => Promise<unknown>
  post?: () => Promise<unknown>
  decide?: () => Promise<unknown>
}) {
  apiFetchMock.mockImplementation((path: string, init?: { method?: string }) => {
    if (path === `/api/app/gaps/clusters/${ENC}/proposal`) {
      return init?.method === 'POST' ? (opts.post ?? notFound)() : (opts.get ?? notFound)()
    }
    if (path === `/api/app/gaps/clusters/${ENC}/decision`) return (opts.decide ?? (() => Promise.resolve({})))()
    if (path.startsWith('/api/app/gaps/clusters?')) {
      const clusters = opts.clusters ?? [cluster()]
      return Promise.resolve({ days: 7, clusters })
    }
    return Promise.reject(new Error(`Unexpected apiFetch: ${path}`))
  })
}

const callsTo = (suffix: string) => apiFetchMock.mock.calls.filter((call) => String(call[0]).endsWith(suffix))
const listUrls = () =>
  apiFetchMock.mock.calls.map((call) => String(call[0])).filter((url) => url.startsWith('/api/app/gaps/clusters?'))

beforeEach(() => {
  apiFetchMock.mockReset()
  navigate.mockClear()
  paramsValue = { clusterKey: KEY }
})

describe('GapClustersPage', () => {
  it('lists the clusters in the order the API returns them, with kind labels', async () => {
    mockApi({
      clusters: [
        cluster({ cluster_key: 'a', topic: 'Eerste', count: 2, kind: 'weak_match' }),
        cluster({ cluster_key: 'b', topic: 'Tweede', count: 9, kind: 'no_article', article: null, articles: [] }),
      ],
    })

    render(<Wrapper><GapClustersPage /></Wrapper>)

    await screen.findByText(/Eerste/)
    const rows = screen.getAllByRole('row').slice(1)
    expect(rows[0].textContent).toMatch(/Eerste/)
    expect(rows[1].textContent).toMatch(/Tweede/)
    expect(within(rows[0]).getByText(/zwak gevonden|weak match/i)).toBeTruthy()
    expect(within(rows[1]).getByText(/^(geen artikel|no article)$/i, { selector: 'div' })).toBeTruthy()
    expect(within(rows[1]).getByText(/^(geen artikel|no article)$/i, { selector: 'span' })).toBeTruthy()
  })

  it('shows the product prefix, the shortened question, the split and the proposal marker', async () => {
    mockApi({
      clusters: [cluster({ has_proposal: true, question: 'x'.repeat(200) })],
    })

    render(<Wrapper><GapClustersPage /></Wrapper>)

    expect(await screen.findByText('Facturatie · Facturen exporteren')).toBeTruthy()
    expect(screen.getByText(/4 widget · 1 (intern|internal)/)).toBeTruthy()
    expect(screen.queryByText('x'.repeat(200))).toBeNull()
    expect(screen.getByText(/^(voorstel|proposal)$/i)).toBeTruthy()
  })

  it('marks an internal-only cluster instead of showing a question', async () => {
    mockApi({
      clusters: [cluster({ cluster_key: 'internal|billing', question: null, questions: [], article: null, articles: [] })],
    })

    render(<Wrapper><GapClustersPage /></Wrapper>)

    expect(await screen.findByText(/interne chat, zonder tekst|internal chat, no text/i)).toBeTruthy()
  })

  it('refetches with the chosen period and with decided clusters when asked', async () => {
    mockApi({})

    render(<Wrapper><GapClustersPage /></Wrapper>)

    await screen.findByText('Facturatie · Facturen exporteren')
    expect(listUrls()).toEqual(['/api/app/gaps/clusters?days=7&include_decided=false'])

    fireEvent.change(screen.getByRole('combobox'), { target: { value: '14' } })
    await waitFor(() => expect(listUrls()).toContain('/api/app/gaps/clusters?days=14&include_decided=false'))

    fireEvent.click(screen.getByRole('checkbox', { name: /toon besliste clusters|show decided clusters/i }))
    await waitFor(() => expect(listUrls()).toContain('/api/app/gaps/clusters?days=14&include_decided=true'))
  })
})

describe('GapClusterDetailPage', () => {
  it('renders the questions, open parts, articles and the proposal', async () => {
    mockApi({ get: () => Promise.resolve(proposal()) })

    render(<Wrapper><GapClusterDetailPage /></Wrapper>)

    expect(await screen.findByText('Kan ik facturen als csv downloaden?')).toBeTruthy()
    expect(screen.getByText('Exporteren naar csv')).toBeTruthy()
    expect(await screen.findByText('Een uitleg over het exporteren van facturen.')).toBeTruthy()
    // Two links to the same article: the articles list and the proposal's "Waar".
    expect(screen.getAllByRole('link', { name: 'Facturen bekijken' }).length).toBe(2)
    expect(screen.getByRole('heading', { name: 'Facturen exporteren', level: 2 })).toBeTruthy()
    expect(screen.getByText('Het exacte menupad')).toBeTruthy()
    // The key is URL-encoded in the path.
    expect(apiFetchMock.mock.calls.some((call) => call[0] === `/api/app/gaps/clusters/${ENC}/proposal`)).toBe(true)
  })

  it('names the parent section and the nearest article for a new article', async () => {
    mockApi({
      get: () => Promise.resolve(proposal({ where_kind: 'new', where_section: 'Handleidingen', open_facts: [] })),
    })

    render(<Wrapper><GapClusterDetailPage /></Wrapper>)

    expect(await screen.findByText(/(nieuw artikel onder|new article under) Handleidingen/i)).toBeTruthy()
    expect(screen.queryByText(/nog in te vullen|still to fill in/i)).toBeNull()
  })

  it('copies the raw markdown and announces it', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    mockApi({ get: () => Promise.resolve(proposal()) })

    render(<Wrapper><GapClusterDetailPage /></Wrapper>)

    fireEvent.click(await screen.findByRole('button', { name: /^(kopieer|copy)$/i }))

    await waitFor(() => expect(writeText).toHaveBeenCalledWith('## Facturen exporteren\n\nKies **Exporteer** rechtsboven.'))
    await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/gekopieerd|copied/i))
  })

  it('generates a proposal on "Maak voorstel" and shows the API detail for 409 and 429', async () => {
    const outcomes = [
      () => Promise.reject(new ApiError(409, 'Telemetry level does not allow proposals')),
      () => Promise.reject(new ApiError(429, 'Weekly cap of 20 reached (20)')),
      () => Promise.resolve(proposal({ missing: 'Zojuist gegenereerd.' })),
    ]
    let call = 0
    mockApi({ post: () => outcomes[call++]() })

    render(<Wrapper><GapClusterDetailPage /></Wrapper>)

    const create = () => screen.findByRole('button', { name: /maak voorstel|create proposal/i })
    fireEvent.click(await create())
    expect(await screen.findByText('Telemetry level does not allow proposals')).toBeTruthy()
    fireEvent.click(await create())
    expect(await screen.findByText('Weekly cap of 20 reached (20)')).toBeTruthy()
    fireEvent.click(await create())
    expect(await screen.findByText('Zojuist gegenereerd.')).toBeTruthy()
  })

  it('asks for confirmation, posts the final decision, shows who and when and hides the buttons', async () => {
    let decided = false
    mockApi({
      get: () =>
        Promise.resolve(
          decided
            ? proposal({ decision: 'rejected', decided_by: 'Anna Voorbeeld', decided_at: '2026-09-22T09:00:00Z' })
            : proposal(),
        ),
      decide: () => {
        decided = true
        return Promise.resolve({})
      },
    })

    render(<Wrapper><GapClusterDetailPage /></Wrapper>)

    fireEvent.click(await screen.findByRole('button', { name: /^(afwijzen|reject)$/i }))
    expect(callsTo('/decision')).toHaveLength(0)
    const listCallsBefore = listUrls().length
    fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: /bevestig|confirm/i }))

    await waitFor(() => expect(callsTo('/decision')).toHaveLength(1))
    expect(JSON.parse((callsTo('/decision')[0][1] as { body: string }).body)).toEqual({ decision: 'rejected' })
    expect(await screen.findByText(/Anna Voorbeeld/)).toBeTruthy()
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /^(afwijzen|reject)$/i })).toBeNull()
      expect(screen.queryByRole('button', { name: /buiten scope|out of scope/i })).toBeNull()
    })
    // The cluster list is invalidated so the cluster leaves the default list.
    await waitFor(() => expect(listUrls().length).toBeGreaterThan(listCallsBefore))
  })

  it('shows a decided cluster as decided, never as open, without decision buttons', async () => {
    mockApi({
      clusters: [cluster({ decision: 'out_of_scope', decided_by: 'Anna Voorbeeld', decided_at: '2026-09-22T09:00:00Z' })],
      get: notFound,
    })

    render(<Wrapper><GapClusterDetailPage /></Wrapper>)

    expect(await screen.findByText(/Anna Voorbeeld/)).toBeTruthy()
    expect(screen.queryByText(/^(open)$/i)).toBeNull()
    expect(screen.getAllByText(/buiten scope|out of scope/i).length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: /^(afwijzen|reject)$/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /^(buiten scope|out of scope)$/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /maak voorstel|create proposal/i })).toBeNull()
  })

  it('shows a not-found state when the cluster is not in the list', async () => {
    mockApi({ clusters: [] })

    render(<Wrapper><GapClusterDetailPage /></Wrapper>)

    expect(await screen.findByText(/kon niet worden gevonden|could not be found/i)).toBeTruthy()
  })
})
