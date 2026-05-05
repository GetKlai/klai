import type { JSX } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
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
      useParams: () => ({ profile: 'kb_manager' }),
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

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

vi.mock('@/paraglide/messages', () => ({
  admin_groups_members_add: () => 'Add member',
  admin_groups_members_search_placeholder: () => 'Search…',
  admin_groups_members_success_added: () => 'Added',
  admin_users_cancel: () => 'Cancel',
  admin_profiles_error_change: () => 'Failed',
  profile_kb_manager_label: () => 'Knowledge manager',
}))

import { Route as RouteCfg } from '../add-member'

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

describe('Add member to profile', () => {
  it('shows the target profile label as context', async () => {
    apiFetchMock.mockResolvedValue({ users: [] })

    const Cfg = RouteCfg as unknown as { component: () => JSX.Element }
    render(
      <Wrapper>
        <Cfg.component />
      </Wrapper>,
    )

    await waitFor(() => {
      expect(screen.getByText('Knowledge manager')).toBeTruthy()
    })
  })

  it('only lists users whose current role differs from the target profile', async () => {
    apiFetchMock.mockResolvedValue({
      users: [
        { zitadel_user_id: 'u1', email: 'a@x', first_name: 'Alice', last_name: 'Anders', role: 'company' },
        { zitadel_user_id: 'u2', email: 'b@x', first_name: 'Bob', last_name: 'Bouwer', role: 'kb_manager' },
        { zitadel_user_id: 'u3', email: 'c@x', first_name: 'Carol', last_name: 'Cooper', role: 'admin' },
      ],
    })

    const Cfg = RouteCfg as unknown as { component: () => JSX.Element }
    render(
      <Wrapper>
        <Cfg.component />
      </Wrapper>,
    )

    await waitFor(() => {
      expect(screen.getByRole('combobox')).toBeTruthy()
    })

    // Open combobox by click
    fireEvent.click(screen.getByRole('combobox'))

    // Alice (company) and Carol (admin) are eligible; Bob (kb_manager) is filtered out
    await waitFor(() => {
      expect(screen.getByText('Alice Anders')).toBeTruthy()
    })
    expect(screen.getByText('Alice Anders')).toBeTruthy()
    expect(screen.getByText('Carol Cooper')).toBeTruthy()
    expect(screen.queryByText('Bob Bouwer')).toBeNull()
  })
})
