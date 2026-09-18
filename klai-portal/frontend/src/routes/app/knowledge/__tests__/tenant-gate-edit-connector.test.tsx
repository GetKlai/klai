/**
 * Tenant gate — edit-connector form (knowledge_gaps rollout).
 *
 * A deep-linked edit of a HubSpot support connector must not expose its
 * config form once the tenant unlock is revoked: the wizard fields drive new
 * support collection/analysis, which the backend refuses while locked. The row
 * still allows delete/cleanup (tested separately); here we assert the edit
 * surface itself is blocked when locked and shown when unlocked.
 */
import type { JSX } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const routerMocks: {
  navigate: ReturnType<typeof vi.fn>
  search: { step?: string; show?: string }
} = vi.hoisted(() => ({ navigate: vi.fn(), search: {} }))

vi.mock('@tanstack/react-router', async () => {
  const actual = await vi.importActual<typeof import('@tanstack/react-router')>(
    '@tanstack/react-router',
  )
  return {
    ...actual,
    useNavigate: () => routerMocks.navigate,
    createFileRoute: () => (config: object) => ({
      ...config,
      useParams: () => ({ kbSlug: 'handbook', connectorId: 'connector-1' }),
      useSearch: () => routerMocks.search,
    }),
  }
})

const apiFetchMock = vi.hoisted(() => vi.fn())
vi.mock('@/lib/apiFetch', async () => {
  const actual = await vi.importActual<typeof import('@/lib/apiFetch')>('@/lib/apiFetch')
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) }
})

vi.mock('@/lib/auth', () => ({ useAuth: () => ({ isAuthenticated: true }) }))

const capabilities: string[] = ['kb.gaps']
vi.mock('@/hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({
    user: { isAdmin: false, hasCapability: (c: string) => capabilities.includes(c) },
  }),
}))

let unlockedFeatures: string[] = ['knowledge_gaps']
vi.mock('@/lib/api-me', () => ({
  fetchMe: () => Promise.resolve({ platform_unlocked_features: unlockedFeatures }),
}))

vi.mock('@/paraglide/messages', async () => {
  const actual = await vi.importActual<typeof import('@/paraglide/messages')>(
    '@/paraglide/messages',
  )
  return {
    ...actual,
    admin_connectors_hubspot_support_account_id: () => 'Account ID',
    gaps_unlock_required: () => 'Knowledge gaps is not turned on for your organisation yet.',
  }
})

import { Route as RouteConfig } from '../$kbSlug_.edit-connector.$connectorId'

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const config = RouteConfig as unknown as { component: () => JSX.Element }
  return render(
    <QueryClientProvider client={client}>
      <config.component />
    </QueryClientProvider>,
  )
}

function mockHubspotConnector() {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === '/api/app/knowledge-bases/handbook/connectors/') {
      return Promise.resolve([
        {
          id: 'connector-1',
          name: 'HubSpot inbox',
          connector_type: 'hubspot_support',
          config: { account_id: '42', lookback_days: 30 },
          schedule: null,
          is_enabled: true,
          last_sync_status: null,
          last_sync_at: null,
          last_sync_documents_ok: null,
          allowed_assertion_modes: null,
        },
      ])
    }
    return Promise.reject(new Error(`Unexpected apiFetch: ${path}`))
  })
}

beforeEach(() => {
  routerMocks.navigate.mockReset()
  routerMocks.search = {}
  apiFetchMock.mockReset()
  capabilities.length = 0
  capabilities.push('kb.gaps')
  unlockedFeatures = ['knowledge_gaps']
})

describe('edit-connector HubSpot form gate', () => {
  it('shows the HubSpot edit form when the tenant is unlocked', async () => {
    mockHubspotConnector()
    renderPage()
    expect(await screen.findByLabelText('Account ID')).toBeTruthy()
  })

  it('blocks the HubSpot edit form and shows the locked notice when revoked', async () => {
    unlockedFeatures = []
    mockHubspotConnector()
    renderPage()
    expect(
      await screen.findByText('Knowledge gaps is not turned on for your organisation yet.'),
    ).toBeTruthy()
    expect(screen.queryByLabelText('Account ID')).toBeNull()
  })
})
