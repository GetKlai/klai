/**
 * SPEC-SLACK-001 §8: the /admin/slack page, one test per state. The portal
 * backend (§7.1) is mocked at the apiFetch boundary; hooks, the KB checklist
 * and the key modal run for real.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const navigate = vi.fn()
let routeSearch: { tab?: string } = {}

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
  createFileRoute: () => (cfg: unknown) => ({
    ...(cfg as object),
    useSearch: () => routeSearch,
  }),
}))

const apiFetchMock = vi.fn()

vi.mock('@/lib/apiFetch', async () => {
  const actual = await vi.importActual<typeof import('@/lib/apiFetch')>('@/lib/apiFetch')
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) }
})

vi.mock('@/lib/auth', () => ({
  useAuth: () => ({ isAuthenticated: true }),
}))

const toast = vi.hoisted(() => ({ success: vi.fn(), warning: vi.fn(), error: vi.fn() }))
vi.mock('sonner', () => ({ toast }))

import * as m from '@/paraglide/messages'
import { ApiError } from '@/lib/apiFetch'
import { Route } from '../index'
import type { SlackStatusResponse } from '../-types'

const KEY = {
  id: "4f2c9a1e-0000-4000-8000-000000000007",
  key_prefix: 'pk_live_ab12',
  rate_limit_rpm: 600,
  last_used_at: null,
  created_at: '2026-10-28T09:00:00Z',
}

const INSTALLATION = {
  team_id: 'T0TEST001',
  team_name: 'Acme Workspace',
  installed_by_slack_user_id: 'U0TEST001',
  installed_at: '2026-10-28T10:00:00Z',
  revoked_at: null,
}

const KBS = {
  knowledge_bases: [
    { id: 1, name: 'Handbook', slug: 'handbook', owner_type: 'org' },
    { id: 2, name: 'Policies', slug: 'policies', owner_type: 'org' },
  ],
}

function status(overrides: Partial<SlackStatusResponse>): SlackStatusResponse {
  return { status: 'not_connected', key: null, installation: null, kb_ids: [], ...overrides }
}

const CONNECTED = status({
  status: 'connected',
  key: KEY,
  installation: INSTALLATION,
  kb_ids: [1],
})

/** Routes apiFetch by method + path; `slack` is what GET /api/admin/slack answers. */
function mockBackend(slack: () => SlackStatusResponse | Promise<never>) {
  apiFetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    if (url.startsWith('/api/app/knowledge-bases')) return KBS
    if (url === '/api/admin/slack' && method === 'GET') return slack()
    return undefined
  })
}

function calls(method: string, url: string) {
  return apiFetchMock.mock.calls.filter(
    ([u, init]) => u === url && ((init as RequestInit | undefined)?.method ?? 'GET') === method,
  )
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retryDelay: 0 }, mutations: { retry: false } },
  })
  const Page = (Route as unknown as { component: () => React.JSX.Element }).component
  return render(
    <QueryClientProvider client={client}>
      <Page />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  apiFetchMock.mockReset()
  navigate.mockReset()
  toast.success.mockReset()
  toast.warning.mockReset()
  routeSearch = {}
})

afterEach(() => {
  vi.useRealTimers()
})

describe('/admin/slack not_connected', () => {
  it('enables "Maak Slack-sleutel" at one KB, shows the key once in the modal and then waits for linking', async () => {
    let current = status({})
    mockBackend(() => current)
    const baseImpl = apiFetchMock.getMockImplementation()!
    apiFetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === '/api/admin/slack/key' && init?.method === 'POST') {
        current = status({ status: 'awaiting_link', key: KEY, kb_ids: [1] })
        return Promise.resolve({ api_key: 'pk_live_ab12SECRETSECRET', status: 'awaiting_link', key: KEY })
      }
      return baseImpl(url, init)
    })

    renderPage()

    const create = await screen.findByRole('button', { name: m.admin_slack_create_key() })
    await screen.findByText('Handbook')
    expect((create as HTMLButtonElement).disabled).toBe(true)

    fireEvent.click(screen.getAllByRole('checkbox')[0])
    await waitFor(() => expect((create as HTMLButtonElement).disabled).toBe(false))

    fireEvent.click(create)

    // Key is shown once, with the paste instruction.
    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByText('pk_live_ab12SECRETSECRET')).toBeTruthy()
    expect(within(dialog).getByText(m.admin_slack_key_modal_description())).toBeTruthy()
    expect(calls('POST', '/api/admin/slack/key')[0][1]).toMatchObject({
      body: JSON.stringify({ kb_ids: [1] }),
    })

    fireEvent.click(within(dialog).getByRole('button', { name: m.admin_slack_key_modal_confirm() }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())

    // Page moved on to awaiting_link and no longer holds the key.
    expect(await screen.findByText(m.admin_slack_awaiting_title())).toBeTruthy()
    expect(screen.queryByText('pk_live_ab12SECRETSECRET')).toBeNull()
  })
})

describe('/admin/slack awaiting_link', () => {
  it('shows the prefix, the key removal tab and no overview tab', async () => {
    mockBackend(() => status({ status: 'awaiting_link', key: KEY, kb_ids: [1] }))
    renderPage()

    expect(await screen.findByText(m.admin_slack_awaiting_title())).toBeTruthy()
    expect(screen.getByText(m.admin_slack_awaiting_body({ prefix: 'pk_live_ab12...' }))).toBeTruthy()
    expect(screen.getByRole('tab', { name: m.admin_slack_tab_remove_key() })).toBeTruthy()
    expect(screen.getByRole('tab', { name: m.admin_slack_tab_kbs() })).toBeTruthy()
    expect(screen.queryByRole('tab', { name: m.admin_slack_tab_overview() })).toBeNull()
  })

  it('polls GET every 10 seconds, and only in this state', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let current = status({ status: 'awaiting_link', key: KEY, kb_ids: [1] })
    mockBackend(() => current)
    renderPage()
    await screen.findByText(m.admin_slack_awaiting_title())
    expect(calls('GET', '/api/admin/slack')).toHaveLength(1)

    current = CONNECTED
    await vi.advanceTimersByTimeAsync(10_000)
    await waitFor(() => expect(calls('GET', '/api/admin/slack')).toHaveLength(2))
    expect(await screen.findByRole('heading', { name: /Acme Workspace/ })).toBeTruthy()

    await vi.advanceTimersByTimeAsync(30_000)
    expect(calls('GET', '/api/admin/slack')).toHaveLength(2)
  })

  it('removes the key after the inline confirmation', async () => {
    routeSearch = { tab: 'disconnect' }
    mockBackend(() => status({ status: 'awaiting_link', key: KEY, kb_ids: [1] }))
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: m.admin_slack_remove_key_button() }))
    fireEvent.click(await screen.findByRole('button', { name: m.admin_slack_remove_key_confirm() }))

    await waitFor(() => expect(calls('DELETE', '/api/admin/slack')).toHaveLength(1))
  })
})

describe('/admin/slack connected', () => {
  it('shows the workspace name with a badge and the three tabs, defaulting to the overview', async () => {
    mockBackend(() => CONNECTED)
    renderPage()

    expect(await screen.findByRole('heading', { name: /Acme Workspace/ })).toBeTruthy()
    expect(screen.getByText(m.admin_slack_status_connected())).toBeTruthy()
    for (const label of [
      m.admin_slack_tab_overview(),
      m.admin_slack_tab_kbs(),
      m.admin_slack_tab_disconnect(),
    ]) {
      expect(screen.getByRole('tab', { name: label })).toBeTruthy()
    }
    // Overview content
    expect(screen.getByText('T0TEST001')).toBeTruthy()
    expect(screen.getByText('pk_live_ab12...')).toBeTruthy()
    expect(screen.getByText(m.admin_slack_overview_never_used())).toBeTruthy()

    fireEvent.click(screen.getByRole('tab', { name: m.admin_slack_tab_kbs() }))
    expect(navigate).toHaveBeenCalledWith({ to: '/admin/slack', search: { tab: 'kbs' } })
  })

  it('saves a changed KB selection with PATCH', async () => {
    routeSearch = { tab: 'kbs' }
    mockBackend(() => CONNECTED)
    renderPage()

    await screen.findByText('Policies')
    const save = screen.getByRole<HTMLButtonElement>('button', { name: m.admin_shared_save() })
    expect(save.disabled).toBe(true)

    fireEvent.click(screen.getAllByRole('checkbox')[1])
    await waitFor(() => expect(save.disabled).toBe(false))
    fireEvent.click(save)

    await waitFor(() => expect(calls('PATCH', '/api/admin/slack')).toHaveLength(1))
    expect(calls('PATCH', '/api/admin/slack')[0][1]).toMatchObject({
      body: JSON.stringify({ kb_ids: [1, 2] }),
    })
  })

  it('disconnects only after the inline confirmation', async () => {
    routeSearch = { tab: 'disconnect' }
    mockBackend(() => CONNECTED)
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: m.admin_slack_disconnect_button() }))
    expect(calls('DELETE', '/api/admin/slack')).toHaveLength(0)

    fireEvent.click(screen.getByRole('button', { name: m.admin_slack_disconnect_confirm() }))
    await waitFor(() => expect(calls('DELETE', '/api/admin/slack')).toHaveLength(1))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(m.admin_slack_disconnect_success()))
  })

  it('warns to remove the app in Slack when klai-slack could not be reached', async () => {
    routeSearch = { tab: 'disconnect' }
    mockBackend(() => CONNECTED)
    const baseImpl = apiFetchMock.getMockImplementation()!
    apiFetchMock.mockImplementation((url: string, init?: RequestInit) =>
      url === '/api/admin/slack' && init?.method === 'DELETE'
        ? Promise.resolve({ slack_revoked: false })
        : baseImpl(url, init),
    )
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: m.admin_slack_disconnect_button() }))
    fireEvent.click(screen.getByRole('button', { name: m.admin_slack_disconnect_confirm() }))

    await waitFor(() => expect(toast.warning).toHaveBeenCalledWith(m.admin_slack_disconnect_partial()))
    expect(toast.success).not.toHaveBeenCalled()
  })
})

describe('/admin/slack revoked', () => {
  it('explains the broken connection and reconnects by deleting the old link', async () => {
    mockBackend(() =>
      status({
        status: 'revoked',
        key: KEY,
        installation: { ...INSTALLATION, revoked_at: '2026-10-29T10:00:00Z' },
        kb_ids: [1],
      }),
    )
    renderPage()

    expect(await screen.findByText(m.admin_slack_revoked_body())).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: m.admin_slack_reconnect() }))

    await waitFor(() => expect(calls('DELETE', '/api/admin/slack')).toHaveLength(1))
  })
})

describe('/admin/slack failures', () => {
  it('shows the unlock-required empty state on 403', async () => {
    mockBackend(() => Promise.reject(new ApiError(403, 'platform feature not unlocked')))
    renderPage()

    expect(await screen.findByText(m.admin_slack_unlock_required())).toBeTruthy()
    expect(screen.queryByRole('button', { name: m.admin_slack_create_key() })).toBeNull()
  })

  it('shows the error state with retry on any other error, never the not-connected card', async () => {
    mockBackend(() => Promise.reject(new ApiError(502, 'bad gateway')))
    renderPage()

    expect(await screen.findByText(/bad gateway/)).toBeTruthy()
    expect(screen.getByRole('button', { name: m.error_retry() })).toBeTruthy()
    expect(screen.queryByRole('button', { name: m.admin_slack_create_key() })).toBeNull()
  })
})
