/**
 * Tenant gate — Sources tab "Support cases" entry (knowledge_gaps rollout).
 *
 * The link into the support-case inbox is already org-only; on top of that it
 * must be gated on the tenant unlock so a revoked tenant (or a caller without
 * kb.gaps) never reaches the gap-detection surface from the Sources tab. This
 * exercises the route, not the presentational bar's boolean.
 */
import type { JSX, ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@tanstack/react-router', () => ({
  createFileRoute: () => (config: object) => ({
    ...config,
    useParams: () => ({ kbSlug: 'company-kb' }),
  }),
  Link: ({ to, children }: { to: string; children?: ReactNode }) => (
    <a href={typeof to === 'string' ? to : ''}>{children}</a>
  ),
}))

const apiFetchMock = vi.hoisted(() => vi.fn())
vi.mock('@/lib/apiFetch', async () => {
  const actual = await vi.importActual<typeof import('@/lib/apiFetch')>('@/lib/apiFetch')
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) }
})

vi.mock('@/lib/auth', () => ({ useAuth: () => ({ isAuthenticated: true }) }))

const capabilities: string[] = ['kb.gaps']
vi.mock('@/hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({
    user: {
      isAdmin: false,
      workspace_url: 'https://company.getklai.com',
      hasCapability: (c: string) => capabilities.includes(c),
    },
  }),
}))

let unlockedFeatures: string[] = ['knowledge_gaps']
vi.mock('@/lib/api-me', () => ({
  fetchMe: () => Promise.resolve({ platform_unlocked_features: unlockedFeatures }),
}))

vi.mock('../-sources-hooks', () => ({
  useSyncAllConnectors: () => ({ mutate: vi.fn(), isPending: false }),
}))

vi.mock('@/paraglide/messages', async () => {
  const actual = await vi.importActual<typeof import('@/paraglide/messages')>(
    '@/paraglide/messages',
  )
  return { ...actual, support_cases_action_open: () => 'Support cases' }
})

import { Route as RouteConfig } from '../sources'

function renderTab() {
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

function mockOrgKb() {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === '/api/app/knowledge-bases/company-kb') {
      return Promise.resolve({ slug: 'company-kb', owner_type: 'org', docs_enabled: false })
    }
    if (path.endsWith('/sources')) {
      return Promise.resolve({ sources: [] })
    }
    return Promise.reject(new Error(`Unexpected apiFetch: ${path}`))
  })
}

beforeEach(() => {
  apiFetchMock.mockReset()
  capabilities.length = 0
  capabilities.push('kb.gaps')
  unlockedFeatures = ['knowledge_gaps']
})

describe('Sources tab support-cases gate', () => {
  it('shows the support-cases link on an unlocked organisation KB', async () => {
    mockOrgKb()
    renderTab()
    expect(await screen.findByText('Support cases')).toBeTruthy()
  })

  it('hides the support-cases link when the tenant unlock is revoked', async () => {
    unlockedFeatures = []
    mockOrgKb()
    renderTab()
    await waitFor(() =>
      expect(apiFetchMock).toHaveBeenCalledWith('/api/app/knowledge-bases/company-kb'),
    )
    expect(screen.queryByText('Support cases')).toBeNull()
  })
})
