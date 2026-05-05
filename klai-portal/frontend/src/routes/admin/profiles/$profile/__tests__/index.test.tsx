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
    Link: ({ children }: { children: ReactNode }) => <span>{children}</span>,
    createFileRoute: () => (cfg: unknown) => ({
      ...(cfg as object),
      useParams: () => ({ profile: 'company' }),
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
  admin_profiles_back: () => 'Back to profiles',
  admin_profiles_loading: () => 'Loading...',
  admin_profiles_drill_in_empty: () => 'No members in this profile yet.',
  admin_profiles_error_change: () => 'Failed',
  admin_groups_members_title: () => 'Members',
  admin_groups_members_add: () => 'Add member',
  admin_groups_members_remove: () => 'Remove',
  admin_groups_members_remove_confirm: ({ name }: { name: string }) => `Remove ${name}?`,
  admin_groups_members_success_removed: () => 'Removed',
  admin_users_col_name: () => 'Name',
  admin_users_col_email: () => 'Email',
  admin_users_col_invited: () => 'Invited',
  admin_users_cancel: () => 'Cancel',
  profile_company_label: () => 'Company chat',
  profile_company_description: () => 'Company description',
}))

vi.mock('@/paraglide/runtime', () => ({
  getLocale: () => 'en',
}))

vi.mock('@/paraglide/registry', () => ({
  datetime: () => 'Apr 16, 2026',
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

beforeEach(() => {
  navigate.mockReset()
  apiFetchMock.mockReset()
})

describe('AdminProfileDetail drill-in', () => {
  it('filters members to those whose role matches the profile param', async () => {
    apiFetchMock.mockResolvedValue({
      users: [
        { zitadel_user_id: 'u1', email: 'a@x', first_name: 'Alice', last_name: 'Anders', role: 'company', created_at: '2026-01-01' },
        { zitadel_user_id: 'u2', email: 'b@x', first_name: 'Bob', last_name: 'Bouwer', role: 'admin', created_at: '2026-01-02' },
        { zitadel_user_id: 'u3', email: 'c@x', first_name: 'Carol', last_name: 'Cooper', role: 'company', created_at: '2026-01-03' },
      ],
    })

    const Cfg = RouteCfg as unknown as { component: () => JSX.Element }
    render(
      <Wrapper>
        <Cfg.component />
      </Wrapper>,
    )

    await waitFor(() => {
      expect(screen.getByText('Alice Anders')).toBeTruthy()
    })

    expect(screen.getByText('Alice Anders')).toBeTruthy()
    expect(screen.getByText('Carol Cooper')).toBeTruthy()
    expect(screen.queryByText('Bob Bouwer')).toBeNull()
  })

  it('remove dispatches PATCH /role with role: personal (demote)', async () => {
    apiFetchMock.mockResolvedValueOnce({
      users: [
        { zitadel_user_id: 'u1', email: 'a@x', first_name: 'Alice', last_name: 'Anders', role: 'company', created_at: '2026-01-01' },
      ],
    })

    const Cfg = RouteCfg as unknown as { component: () => JSX.Element }
    render(
      <Wrapper>
        <Cfg.component />
      </Wrapper>,
    )

    await waitFor(() => {
      expect(screen.getByText('Alice Anders')).toBeTruthy()
    })

    apiFetchMock.mockResolvedValueOnce(undefined)
    fireEvent.click(screen.getByLabelText('Remove'))
    // Confirm
    await waitFor(() => {
      expect(screen.getByText('Remove Alice Anders?')).toBeTruthy()
    })
    // The InlineDeleteConfirm renders a confirm button — click it
    const confirmButtons = screen.getAllByRole('button')
    const confirmBtn = confirmButtons.find((b) => b.textContent?.includes('Remove Alice'))
    expect(confirmBtn).toBeTruthy()
    if (confirmBtn) fireEvent.click(confirmBtn)

    await waitFor(() => {
      expect(apiFetchMock).toHaveBeenCalledWith(
        '/api/admin/users/u1/role',
        expect.objectContaining({
          method: 'PATCH',
          body: JSON.stringify({ role: 'personal' }),
        }),
      )
    })
  })

  it('does not render any bulk-select checkboxes', async () => {
    apiFetchMock.mockResolvedValue({
      users: [
        { zitadel_user_id: 'u1', email: 'a@x', first_name: 'Alice', last_name: 'Anders', role: 'company', created_at: '2026-01-01' },
      ],
    })

    const Cfg = RouteCfg as unknown as { component: () => JSX.Element }
    render(
      <Wrapper>
        <Cfg.component />
      </Wrapper>,
    )

    await waitFor(() => {
      expect(screen.getByText('Alice Anders')).toBeTruthy()
    })

    expect(screen.queryAllByRole('checkbox')).toHaveLength(0)
  })
})
