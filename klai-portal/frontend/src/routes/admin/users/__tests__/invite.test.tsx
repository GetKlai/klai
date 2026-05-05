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
    createFileRoute: () => (cfg: unknown) => cfg,
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

import { adminMessageMocks } from '../../_components/__tests__/_messages'

vi.mock('@/paraglide/messages', () => ({ ...adminMessageMocks }))

import { Route as RouteCfg } from '../invite'

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

describe('InviteUserPage', () => {
  it('renders a Select dropdown (no radio-cards) with 5 ladder options', async () => {
    apiFetchMock.mockResolvedValue({ name: 'Org', default_language: 'nl' })

    const Cfg = RouteCfg as unknown as { component: () => JSX.Element }
    render(
      <Wrapper>
        <Cfg.component />
      </Wrapper>,
    )

    await waitFor(() => {
      expect(screen.getByText('Profile')).toBeTruthy()
    })

    // No radio elements (rolled back from PR #317 ProfilePicker)
    expect(screen.queryAllByRole('radio')).toHaveLength(0)

    const select = screen.getByLabelText('Profile')
    const options = Array.from(select.querySelectorAll('option'))
    expect(options).toHaveLength(5)
    expect(options.map((o) => o.value)).toEqual([
      'personal',
      'company',
      'kb_manager',
      'group_manager',
      'admin',
    ])
  })

  it('defaults the profile selection to "personal"', async () => {
    apiFetchMock.mockResolvedValue({ name: 'Org', default_language: 'nl' })

    const Cfg = RouteCfg as unknown as { component: () => JSX.Element }
    render(
      <Wrapper>
        <Cfg.component />
      </Wrapper>,
    )

    await waitFor(() => {
      expect(screen.getByLabelText('Profile')).toBeTruthy()
    })

    const select = screen.getByLabelText('Profile')
    expect((select as HTMLSelectElement).value).toBe('personal')
  })
})
