/**
 * Tenant gate — add-connector picker (knowledge_gaps rollout).
 *
 * The HubSpot support connector feeds the support-gap pipeline, so its entry
 * point in the picker is gated on the tenant unlock (knowledge_gaps) AND the
 * kb.gaps capability AND an organisation KB. A locked tenant, a caller without
 * the capability, or a personal KB never sees the option, and a deep-linked
 * ?type=hubspot_support falls back to the picker instead of the form.
 */
import type { JSX } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const routerMocks: {
  navigate: ReturnType<typeof vi.fn>
  search: { type?: string }
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
      useParams: () => ({ kbSlug: 'handbook' }),
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
    admin_connectors_type_website: () => 'Website',
    admin_connectors_type_hubspot_support: () => 'HubSpot support',
  }
})

import { Route as RouteConfig } from '../$kbSlug_.add-connector'

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

function mockKb(ownerType: string) {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === '/api/app/knowledge-bases/handbook') {
      return Promise.resolve({ slug: 'handbook', owner_type: ownerType })
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

describe('add-connector picker HubSpot gate', () => {
  it('offers HubSpot support on an unlocked organisation KB', async () => {
    mockKb('org')
    renderPage()
    expect(await screen.findByRole('button', { name: /HubSpot support/ })).toBeTruthy()
  })

  it('hides HubSpot support when the tenant has not unlocked knowledge_gaps', async () => {
    unlockedFeatures = []
    mockKb('org')
    renderPage()
    await waitFor(() =>
      expect(apiFetchMock).toHaveBeenCalledWith('/api/app/knowledge-bases/handbook'),
    )
    await screen.findByRole('button', { name: 'Website' })
    expect(screen.queryByRole('button', { name: /HubSpot support/ })).toBeNull()
  })

  it('hides HubSpot support when the caller lacks the kb.gaps capability', async () => {
    capabilities.length = 0
    mockKb('org')
    renderPage()
    await screen.findByRole('button', { name: 'Website' })
    expect(screen.queryByRole('button', { name: /HubSpot support/ })).toBeNull()
  })

  it('hides HubSpot support on a personal KB even when unlocked', async () => {
    mockKb('user')
    renderPage()
    await waitFor(() =>
      expect(apiFetchMock).toHaveBeenCalledWith('/api/app/knowledge-bases/handbook'),
    )
    await screen.findByRole('button', { name: 'Website' })
    expect(screen.queryByRole('button', { name: /HubSpot support/ })).toBeNull()
  })

  it('falls back to the picker (no form) for a deep-linked ?type=hubspot_support while locked', async () => {
    unlockedFeatures = []
    routerMocks.search = { type: 'hubspot_support' }
    mockKb('org')
    renderPage()
    // The type-picker Website button only renders when no type form is shown.
    await screen.findByRole('button', { name: 'Website' })
    expect(screen.queryByRole('button', { name: /HubSpot support/ })).toBeNull()
  })
})
