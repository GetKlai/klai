/**
 * SPEC-SLACK-001 §8: the /admin/slack page, one test per state. The portal
 * backend (§7.1) is mocked at the apiFetch boundary; hooks and the KB
 * checklist run for real.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const navigate = vi.fn()
let routeSearch: { tab?: string; slack?: string } = {}

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
  id: '4f2c9a1e-0000-4000-8000-000000000007',
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

/** Layers one extra route over whatever `mockBackend` installed. */
function mockRoute(method: string, url: string, answer: () => Promise<unknown>) {
  const baseImpl = apiFetchMock.getMockImplementation()!
  apiFetchMock.mockImplementation((u: string, init?: RequestInit) =>
    u === url && (init?.method ?? 'GET') === method ? answer() : baseImpl(u, init),
  )
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

const assign = vi.fn()
const originalLocation = Object.getOwnPropertyDescriptor(window, 'location')!

beforeEach(() => {
  apiFetchMock.mockReset()
  navigate.mockReset()
  assign.mockReset()
  toast.success.mockReset()
  toast.warning.mockReset()
  toast.error.mockReset()
  routeSearch = {}
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: { ...window.location, assign },
  })
})

afterEach(() => {
  vi.useRealTimers()
  Object.defineProperty(window, 'location', originalLocation)
})

describe('/admin/slack not_connected', () => {
  it('shows one card and sends the browser to connect_url once a KB is chosen', async () => {
    mockBackend(() => status({}))
    mockRoute('POST', '/api/admin/slack/connect', () =>
      Promise.resolve({ connect_url: 'https://slack.com/oauth/v2/authorize?state=abc' }),
    )

    renderPage()

    expect(await screen.findByText(m.admin_slack_connect_title())).toBeTruthy()
    expect(screen.getByText(m.admin_slack_connect_hint())).toBeTruthy()
    expect(screen.getByText(m.admin_slack_connect_redirect_note())).toBeTruthy()
    expect(screen.queryByRole('tab')).toBeNull()

    const connect = screen.getByRole<HTMLButtonElement>('button', {
      name: m.admin_slack_connect_button(),
    })
    await screen.findByText('Handbook')
    expect(connect.disabled).toBe(true)

    fireEvent.click(screen.getAllByRole('checkbox')[0])
    await waitFor(() => expect(connect.disabled).toBe(false))
    fireEvent.click(connect)

    await waitFor(() =>
      expect(assign).toHaveBeenCalledWith('https://slack.com/oauth/v2/authorize?state=abc'),
    )
    expect(calls('POST', '/api/admin/slack/connect')[0][1]).toMatchObject({
      body: JSON.stringify({ kb_ids: [1] }),
    })
    expect(calls('POST', '/api/admin/slack/key')).toHaveLength(0)
  })

  it('shows the backend error and stays on the page when connect is refused', async () => {
    mockBackend(() => status({}))
    mockRoute('POST', '/api/admin/slack/connect', () =>
      Promise.reject(new ApiError(409, 'already has a key')),
    )

    renderPage()
    await screen.findByText('Handbook')
    fireEvent.click(screen.getAllByRole('checkbox')[0])
    fireEvent.click(screen.getByRole('button', { name: m.admin_slack_connect_button() }))

    expect(await screen.findByText(/already has a key/)).toBeTruthy()
    expect(assign).not.toHaveBeenCalled()
  })
})

describe('/admin/slack return from Slack', () => {
  it.each([
    ['connected', () => toast.success, m.admin_slack_return_connected()],
    ['denied', () => toast.error, m.admin_slack_return_denied()],
    ['failed', () => toast.error, m.admin_slack_return_failed()],
    ['already_linked', () => toast.warning, m.admin_slack_return_already_linked()],
  ] as const)('?slack=%s shows its message and strips the param', async (result, channel, message) => {
    routeSearch = { slack: result }
    mockBackend(() => status({}))
    renderPage()

    await waitFor(() => expect(channel()).toHaveBeenCalledWith(message, expect.anything()))
    expect(navigate).toHaveBeenCalledWith({
      to: '/admin/slack',
      search: { tab: undefined },
      replace: true,
    })
  })

  it('?slack=connected lands on the connected view with the success message', async () => {
    routeSearch = { slack: 'connected' }
    mockBackend(() => CONNECTED)
    renderPage()

    expect(await screen.findByRole('heading', { name: /Acme Workspace/ })).toBeTruthy()
    expect(toast.success).toHaveBeenCalledWith(m.admin_slack_return_connected(), expect.anything())
  })
})

describe('/admin/slack legacy awaiting_link', () => {
  it('tells the admin to remove the key and connect again, without tabs or paste instructions', async () => {
    mockBackend(() => status({ status: 'awaiting_link', key: KEY, kb_ids: [1] }))
    renderPage()

    expect(await screen.findByText(m.admin_slack_legacy_body())).toBeTruthy()
    expect(screen.queryByRole('tab')).toBeNull()
    expect(screen.queryByRole('alertdialog')).toBeNull()
  })

  it('does not poll', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mockBackend(() => status({ status: 'awaiting_link', key: KEY, kb_ids: [1] }))
    renderPage()
    await screen.findByText(m.admin_slack_legacy_body())

    await vi.advanceTimersByTimeAsync(30_000)
    expect(calls('GET', '/api/admin/slack')).toHaveLength(1)
  })

  it('removes the key after the inline confirmation', async () => {
    mockBackend(() => status({ status: 'awaiting_link', key: KEY, kb_ids: [1] }))
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: m.admin_slack_remove_key_button() }))
    fireEvent.click(await screen.findByRole('button', { name: m.admin_slack_remove_key_confirm() }))

    await waitFor(() => expect(calls('DELETE', '/api/admin/slack')).toHaveLength(1))
  })
})

describe('/admin/slack connected', () => {
  it('shows the workspace name with a badge, the three tabs and a calm overview', async () => {
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

    expect(screen.getByText(m.admin_slack_usage_title())).toBeTruthy()
    for (const line of [
      m.admin_slack_usage_dm(),
      m.admin_slack_usage_channel(),
      m.admin_slack_usage_kb(),
      m.admin_slack_usage_help(),
    ]) {
      expect(screen.getByText(line)).toBeTruthy()
    }
    // Key details no longer belong on the overview.
    expect(screen.queryByText(/pk_live_ab12/)).toBeNull()
    expect(screen.queryByText(/600/)).toBeNull()

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
    mockRoute('DELETE', '/api/admin/slack', () => Promise.resolve({ slack_revoked: false }))
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: m.admin_slack_disconnect_button() }))
    fireEvent.click(screen.getByRole('button', { name: m.admin_slack_disconnect_confirm() }))

    await waitFor(() => expect(toast.warning).toHaveBeenCalledWith(m.admin_slack_disconnect_partial()))
    expect(toast.success).not.toHaveBeenCalled()
  })
})

describe('/admin/slack revoked', () => {
  it('explains the broken connection and reconnects by deleting the old link', async () => {
    let current = status({
      status: 'revoked',
      key: KEY,
      installation: { ...INSTALLATION, revoked_at: '2026-10-29T10:00:00Z' },
      kb_ids: [1],
    })
    mockBackend(() => current)
    mockRoute('DELETE', '/api/admin/slack', () => {
      current = status({})
      return Promise.resolve(undefined)
    })
    renderPage()

    expect(await screen.findByText(m.admin_slack_revoked_body())).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: m.admin_slack_reconnect() }))

    await waitFor(() => expect(calls('DELETE', '/api/admin/slack')).toHaveLength(1))
    expect(await screen.findByText(m.admin_slack_connect_title())).toBeTruthy()
  })
})

describe('/admin/slack failures', () => {
  it('shows the unlock-required empty state on 403', async () => {
    mockBackend(() => Promise.reject(new ApiError(403, 'platform feature not unlocked')))
    renderPage()

    expect(await screen.findByText(m.admin_slack_unlock_required())).toBeTruthy()
    expect(screen.queryByRole('button', { name: m.admin_slack_connect_button() })).toBeNull()
  })

  it('shows the error state with retry on any other error, never the not-connected card', async () => {
    mockBackend(() => Promise.reject(new ApiError(502, 'bad gateway')))
    renderPage()

    expect(await screen.findByText(/bad gateway/)).toBeTruthy()
    expect(screen.getByRole('button', { name: m.error_retry() })).toBeTruthy()
    expect(screen.queryByRole('button', { name: m.admin_slack_connect_button() })).toBeNull()
  })
})
