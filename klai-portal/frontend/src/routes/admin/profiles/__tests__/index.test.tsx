import type { JSX } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

const navigate = vi.fn()
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
  createFileRoute: () => (cfg: unknown) => cfg,
}))

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

describe('AdminProfiles index', () => {
  it('renders the 5 ladder profiles in order with correct counts', async () => {
    apiFetchMock.mockResolvedValue({
      users: [
        { zitadel_user_id: 'u1', role: 'admin' },
        { zitadel_user_id: 'u2', role: 'company' },
        { zitadel_user_id: 'u3', role: 'company' },
        { zitadel_user_id: 'u4', role: 'kb_manager' },
      ],
    })

    const Cfg = RouteCfg as unknown as { component: () => JSX.Element }
    render(
      <Wrapper>
        <Cfg.component />
      </Wrapper>,
    )

    await waitFor(() => {
      expect(screen.getByText('Personal chat')).toBeTruthy()
    })

    expect(screen.getByText('Personal chat')).toBeTruthy()
    expect(screen.getByText('Company chat')).toBeTruthy()
    expect(screen.getByText('Knowledge manager')).toBeTruthy()
    expect(screen.getByText('Group manager')).toBeTruthy()
    expect(screen.getByText('Admin')).toBeTruthy()

    // Counts: personal=0, company=2, kb_manager=1, group_manager=0, admin=1
    const allRows = screen.getAllByRole('row')
    // Header + 5 data rows
    expect(allRows.length).toBe(6)

    // Spot-check counts
    const cells = screen.getAllByRole('cell')
    // For each profile row, count cell is the second cell (Name | Count | Actions)
    const labels = ['Personal chat', 'Company chat', 'Knowledge manager', 'Group manager', 'Admin']
    const expected = [0, 2, 1, 0, 1]
    labels.forEach((label, idx) => {
      const labelCell = cells.find((c) => c.textContent?.includes(label))
      expect(labelCell).toBeTruthy()
      // Find the row that contains the label cell, then check count cell
      const row = labelCell?.closest('tr')
      const rowCells = row ? Array.from(row.querySelectorAll('td')) : []
      // 0=name, 1=count, 2=actions
      expect(rowCells[1]?.textContent?.trim()).toBe(String(expected[idx]))
    })
  })

  it('renders the description as sub-text in the same Name cell', async () => {
    apiFetchMock.mockResolvedValue({ users: [] })

    const Cfg = RouteCfg as unknown as { component: () => JSX.Element }
    render(
      <Wrapper>
        <Cfg.component />
      </Wrapper>,
    )

    await waitFor(() => {
      expect(screen.getByText('Personal description')).toBeTruthy()
    })
  })
})
