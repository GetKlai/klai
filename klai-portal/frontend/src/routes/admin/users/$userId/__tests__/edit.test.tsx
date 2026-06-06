import type { JSX } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

const navigate = vi.fn()
vi.mock('@tanstack/react-router', async () => {
  const actual = await vi.importActual<typeof import('@tanstack/react-router')>(
    '@tanstack/react-router',
  )
  return {
    ...actual,
    useNavigate: () => navigate,
    createFileRoute: () => (cfg: unknown) => ({
      ...(cfg as object),
      useParams: () => ({ userId: 'uX' }),
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

vi.mock('@/hooks/useUserLifecycle', () => ({
  useSuspendUser: () => ({ mutate: vi.fn(), isPending: false }),
  useReactivateUser: () => ({ mutate: vi.fn(), isPending: false }),
  useOffboardUser: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteUserWithDispositions: () => ({ mutate: vi.fn(), isPending: false }),
}))

vi.mock('@/hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({ user: { user_id: 'someone-else' } }),
}))

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

import { adminMessageMocks } from '../../../_components/__tests__/_messages'

vi.mock('@/paraglide/messages', () => ({ ...adminMessageMocks }))

import { Route as RouteCfg } from '../edit'

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchInterval: false },
      mutations: { retry: false },
    },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

beforeEach(() => {
  navigate.mockReset()
  apiFetchMock.mockReset()
})

describe('EditUserPage', () => {
  it('renders ONE submit button (no second Save for profile)', async () => {
    apiFetchMock.mockResolvedValue({
      users: [
        {
          zitadel_user_id: 'uX',
          email: 't@t',
          first_name: 'Test',
          last_name: 'User',
          preferred_language: 'nl',
          status: 'active',
          invite_pending: false,
          role: 'company',
        },
      ],
    })

    const Cfg = RouteCfg as unknown as { component: () => JSX.Element }
    const { container } = render(
      <Wrapper>
        <Cfg.component />
      </Wrapper>,
    )

    await waitFor(() => {
      expect(screen.getByDisplayValue('Test')).toBeTruthy()
    })

    const submitButtons = container.querySelectorAll('button[type="submit"]')
    expect(submitButtons.length).toBe(1)
  })

  it('does NOT render a Groups section', async () => {
    apiFetchMock.mockResolvedValue({
      users: [
        {
          zitadel_user_id: 'uX',
          email: 't@t',
          first_name: 'Test',
          last_name: 'User',
          preferred_language: 'nl',
          status: 'active',
          invite_pending: false,
          role: 'company',
        },
      ],
    })

    const Cfg = RouteCfg as unknown as { component: () => JSX.Element }
    render(
      <Wrapper>
        <Cfg.component />
      </Wrapper>,
    )

    await waitFor(() => {
      expect(screen.getByDisplayValue('Test')).toBeTruthy()
    })

    expect(screen.queryByText(/^Groups$/)).toBeNull()
  })

  it('renders the header subtitle clarifying profiles vs groups', async () => {
    apiFetchMock.mockResolvedValue({ users: [] })

    const Cfg = RouteCfg as unknown as { component: () => JSX.Element }
    render(
      <Wrapper>
        <Cfg.component />
      </Wrapper>,
    )

    await waitFor(() => {
      expect(
        screen.getByText(/Profiles control what tools the user can use/),
      ).toBeTruthy()
    })
  })

  it('initialises Profile picker with the user current role', async () => {
    apiFetchMock.mockResolvedValue({
      users: [
        {
          zitadel_user_id: 'uX',
          email: 't@t',
          first_name: 'Test',
          last_name: 'User',
          preferred_language: 'nl',
          status: 'active',
          invite_pending: false,
          role: 'kb_manager',
        },
      ],
    })

    const Cfg = RouteCfg as unknown as { component: () => JSX.Element }
    render(
      <Wrapper>
        <Cfg.component />
      </Wrapper>,
    )

    await waitFor(() => {
      const radios = screen.getAllByRole('radio')
      const kbRadio = radios.find((r) => (r as HTMLInputElement).value === 'kb_manager') as
        | HTMLInputElement
        | undefined
      expect(kbRadio?.checked).toBe(true)
    })
  })
})
