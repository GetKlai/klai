import type { JSX, ReactNode } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const navigate = vi.fn()
vi.mock('@tanstack/react-router', async () => {
  const actual = await vi.importActual<typeof import('@tanstack/react-router')>(
    '@tanstack/react-router',
  )
  return {
    ...actual,
    useNavigate: () => navigate,
    createFileRoute: () => (cfg: unknown) => cfg,
  }
})

const apiFetchMock = vi.fn()
vi.mock('@/lib/apiFetch', async () => {
  const actual = await vi.importActual<typeof import('@/lib/apiFetch')>('@/lib/apiFetch')
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) }
})

vi.mock('@/lib/auth', () => ({
  useAuth: () => ({
    isAuthenticated: true,
    user: { profile: { sub: 'current-user' } },
  }),
}))

vi.mock('@/hooks/useUserLifecycle', () => ({
  useSuspendUser: () => ({ mutate: vi.fn(), isPending: false }),
  useReactivateUser: () => ({ mutate: vi.fn(), isPending: false }),
}))

vi.mock('@/components/admin/offboard-wizard', () => ({
  OffboardWizard: () => null,
}))

vi.mock('@/paraglide/runtime', () => ({
  getLocale: () => 'en',
}))

vi.mock('@/paraglide/registry', () => ({
  datetime: (_locale: string, isoString: string) => `date:${isoString}`,
  plural: (_locale: string, count: number) => (count === 1 ? 'one' : 'other'),
}))

import { adminMessageMocks } from '../../_components/__tests__/_messages'

vi.mock('@/paraglide/messages', () => ({
  ...adminMessageMocks,
  admin_users_heading: () => 'Users',
  admin_users_count_one: () => '1 user',
  admin_users_count_other: ({ count }: { count: string }) => `${count} users`,
  admin_users_search_placeholder: () => 'Search users',
  admin_users_loading: () => 'Loading users...',
  admin_users_empty: () => 'No users',
  admin_users_col_status: () => 'Status',
  admin_users_col_actions: () => 'Actions',
  admin_users_status_active: () => 'Active',
  admin_users_status_suspended: () => 'Suspended',
  admin_users_status_offboarded: () => 'Offboarded',
  admin_users_resend_invite: () => 'Resend invite',
  admin_users_edit: () => 'Edit',
  admin_users_delete: () => 'Delete',
  admin_users_delete_confirm: ({ name }: { name: string }) => `Delete ${name}?`,
  admin_users_error_delete_generic: () => 'Delete failed.',
  admin_users_error_resend_invite_generic: () => 'Resend failed.',
  admin_users_error_leave_workspace: () => 'Leave failed.',
  admin_users_confirm_leave_title: () => 'Leave workspace?',
  admin_users_confirm_leave_description: () => 'You will leave this workspace.',
  admin_users_action_leave_workspace: () => 'Leave workspace',
  admin_users_action_change_profile: () => 'Change profile',
}))

import { Route as RouteCfg } from '../index'

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchInterval: false },
      mutations: { retry: false },
    },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

const usersResponse = {
  users: [
    {
      zitadel_user_id: 'u1',
      email: 'ada@example.com',
      first_name: 'Ada',
      last_name: 'Lovelace',
      role: 'admin',
      seat_type: 'knowledge',
      status: 'active',
      preferred_language: 'en',
      created_at: '2026-05-01T00:00:00Z',
      invite_pending: false,
    },
    {
      zitadel_user_id: 'u2',
      email: 'bob@example.com',
      first_name: 'Bob',
      last_name: 'Builder',
      role: 'personal',
      seat_type: 'chat',
      status: 'active',
      preferred_language: 'en',
      created_at: '2026-05-02T00:00:00Z',
      invite_pending: true,
    },
    {
      zitadel_user_id: 'u3',
      email: 'cleo@example.com',
      first_name: 'Cleo',
      last_name: 'Offboarded',
      role: 'company',
      seat_type: 'chat',
      status: 'offboarded',
      preferred_language: 'en',
      created_at: '2026-05-03T00:00:00Z',
      invite_pending: false,
    },
  ],
}

function renderUsersPage() {
  const Cfg = RouteCfg as unknown as { component: () => JSX.Element }
  render(
    <Wrapper>
      <Cfg.component />
    </Wrapper>,
  )
}

beforeEach(() => {
  navigate.mockReset()
  apiFetchMock.mockReset()
  apiFetchMock.mockImplementation((url: string, options?: { method?: string }) => {
    if (url === '/api/admin/users' && !options?.method) return Promise.resolve(usersResponse)
    if (options?.method === 'DELETE') return Promise.resolve(undefined)
    return Promise.resolve(undefined)
  })
})

describe('Admin users index', () => {
  it('renders users and filters client-side by name or email', async () => {
    renderUsersPage()

    await waitFor(() => {
      expect(screen.getByText('Ada Lovelace')).toBeTruthy()
    })

    expect(screen.getByText('Bob Builder')).toBeTruthy()
    expect(screen.getByText('Cleo Offboarded')).toBeTruthy()
    expect(screen.getByText('3 users')).toBeTruthy()

    fireEvent.change(screen.getByLabelText('Search users'), {
      target: { value: 'bob@' },
    })

    expect(screen.queryByText('Ada Lovelace')).toBeNull()
    expect(screen.getByText('Bob Builder')).toBeTruthy()
  })

  it('allows resending invites for active and offboarded users', async () => {
    renderUsersPage()

    await waitFor(() => {
      expect(screen.getByText('Ada Lovelace')).toBeTruthy()
    })

    const resendButtons = screen.getAllByLabelText('Resend invite')
    expect(resendButtons).toHaveLength(3)

    fireEvent.click(resendButtons[0])
    fireEvent.click(resendButtons[2])

    await waitFor(() => {
      expect(
        apiFetchMock.mock.calls.some(
          ([url, options]) =>
            url === '/api/admin/users/u1/resend-invite' &&
            (options as { method?: string } | undefined)?.method === 'POST',
        ),
      ).toBe(true)
      expect(
        apiFetchMock.mock.calls.some(
          ([url, options]) =>
            url === '/api/admin/users/u3/resend-invite' &&
            (options as { method?: string } | undefined)?.method === 'POST',
        ),
      ).toBe(true)
    })
  })

  it('keeps delete available only for pending invites and calls the delete endpoint after confirmation', async () => {
    renderUsersPage()

    await waitFor(() => {
      expect(screen.getByText('Bob Builder')).toBeTruthy()
    })

    expect(screen.getAllByLabelText('Delete')).toHaveLength(1)
    fireEvent.click(screen.getByLabelText('Delete'))
    fireEvent.click(screen.getByText('Delete Bob Builder?'))

    await waitFor(() => {
      expect(
        apiFetchMock.mock.calls.some(
          ([url, options]) =>
            url === '/api/admin/users/u2' &&
            (options as { method?: string } | undefined)?.method === 'DELETE',
        ),
      ).toBe(true)
    })
  })
})
