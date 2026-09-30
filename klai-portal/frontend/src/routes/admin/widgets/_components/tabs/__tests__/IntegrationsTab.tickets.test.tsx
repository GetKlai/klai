/**
 * SPEC-KNOWLEDGE-ESCALATION-001 §4.2/§5: the "Tickets in HubSpot" admin card
 * on the widget Integrations tab. An admin must be able to fetch the
 * tenant's HubSpot pipelines with their service key, assign a pipeline/stage
 * per ticket target, save, and get a readable message when the key or a
 * pipeline/stage choice is rejected.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

const apiFetchMock = vi.fn()
vi.mock('@/lib/apiFetch', async () => {
  const actual = await vi.importActual<typeof import('@/lib/apiFetch')>('@/lib/apiFetch')
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) }
})

vi.mock('@/lib/auth', () => ({
  useAuth: () => ({ isAuthenticated: true }),
}))

import { toast } from 'sonner'
import { ApiError as ApiErrorCtor } from '@/lib/apiFetch'
import { IntegrationsTab } from '../IntegrationsTab'
import type { WidgetDetailResponse } from '../../../-types'

function makeWidget(): WidgetDetailResponse {
  return {
    id: 'widget-uuid-1',
    name: 'Test Widget',
    widget_id: 'wgt_test',
    description: null,
    allow_any_origin: false,
    public_share_enabled: false,
    rate_limit_rpm: 60,
    kb_access_count: 0,
    last_used_at: null,
    created_at: '2026-01-01T00:00:00Z',
    created_by: 'user-1',
    widget_config: {
      allowed_origins: [],
      title: 'Test Widget',
      welcome_message: '',
      system_prompt: '',
      css_variables: {},
      conversation_starters: [],
      hide_disclaimer: false,
      template_slug: null,
      primary_color: '#fcaa2d',
      theme: 'light',
      show_sources: true,
      show_meta: false,
      collect_user_info: false,
      page_context_enabled: false,
      widget_position: 'right',
      booking_url: null,
    },
    kb_access: [],
  }
}

const PIPELINES = [
  {
    id: 'pl-1',
    label: 'Sales pipeline',
    stages: [
      { id: 'st-1', label: 'Open' },
      { id: 'st-2', label: 'Closed' },
    ],
  },
]

function mockApi({
  tickets,
  saveResult,
}: {
  tickets: Record<string, unknown>
  saveResult?: () => Promise<unknown>
}) {
  apiFetchMock.mockImplementation((path: unknown, init?: RequestInit) => {
    const url = String(path)
    const method = init?.method ?? 'GET'
    // Widgets created before HubSpot connect existed 404 the connect status
    // route (IntegrationsTab.tsx `hubspotUnavailable`) — irrelevant to the
    // tickets card, so keep it out of the way.
    if (url.endsWith('/integrations/hubspot')) {
      return Promise.reject(new ApiErrorCtor(404, 'not_found'))
    }
    if (url.endsWith('/integrations/tickets') && method === 'GET') {
      return Promise.resolve(tickets)
    }
    if (url.endsWith('/integrations/tickets') && method === 'PUT') {
      return saveResult ? saveResult() : Promise.resolve(tickets)
    }
    if (url.endsWith('/integrations/tickets') && method === 'DELETE') {
      return Promise.resolve(undefined)
    }
    if (url.endsWith('/integrations/tickets/pipelines') && method === 'POST') {
      return Promise.resolve(PIPELINES)
    }
    return Promise.reject(new Error(`Unexpected apiFetch: ${method} ${url}`))
  })
}

function renderTab(widget: WidgetDetailResponse) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <IntegrationsTab widget={widget} />
    </QueryClientProvider>,
  )
}

// Every integration card uses the same "Save changes" label, so the save
// click has to be scoped to the Tickets card's own <article>.
const ticketsCard = () => screen.getByText('Tickets in HubSpot').closest('article') as HTMLElement
const clickTicketsSave = () =>
  fireEvent.click(within(ticketsCard()).getByRole('button', { name: /save changes|wijzigingen opslaan/i }))

const saveRequestBody = () => {
  const call = apiFetchMock.mock.calls.find(
    ([path, init]) => String(path).endsWith('/integrations/tickets') && (init as RequestInit)?.method === 'PUT',
  )
  if (!call) return undefined
  const raw = (call[1] as RequestInit).body
  return typeof raw === 'string' ? JSON.parse(raw) : raw
}

beforeEach(() => {
  apiFetchMock.mockReset()
})

describe('IntegrationsTab - Tickets in HubSpot card', () => {
  it('fetching pipelines populates the pipeline and stage selects', async () => {
    mockApi({ tickets: { configured: false, hubspot_portal_id: null, portal_id_source: null, targets: [] } })
    renderTab(makeWidget())

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /pipelines ophalen|fetch pipelines/i })).toBeTruthy(),
    )
    fireEvent.click(screen.getByRole('button', { name: /doel toevoegen|add target/i }))
    fireEvent.click(screen.getByRole('button', { name: /pipelines ophalen|fetch pipelines/i }))

    await waitFor(() => {
      const pipelineSelect = screen.getByLabelText(/^pipeline$/i) as unknown as HTMLSelectElement
      expect(Array.from(pipelineSelect.options).map((o) => o.textContent)).toContain('Sales pipeline')
    })

    fireEvent.change(screen.getByLabelText(/^pipeline$/i), { target: { value: 'pl-1' } })

    await waitFor(() => {
      const stageSelect = screen.getByLabelText(/^stage$|^fase$/i) as unknown as HTMLSelectElement
      expect(Array.from(stageSelect.options).map((o) => o.textContent)).toContain('Open')
    })
  })

  it('saves {service_key, hubspot_portal_id, targets} for a not-yet-configured widget', async () => {
    mockApi({ tickets: { configured: false, hubspot_portal_id: null, portal_id_source: null, targets: [] } })
    renderTab(makeWidget())

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /doel toevoegen|add target/i })).toBeTruthy(),
    )
    // Add target before the initial GET resolves: a real admin can click this
    // fast, and the fetch settling afterward must not wipe the new row.
    fireEvent.click(screen.getByRole('button', { name: /doel toevoegen|add target/i }))
    fireEvent.change(screen.getByLabelText(/label, (bv\.|e\.g\.) sales/i), { target: { value: 'Sales' } })
    fireEvent.click(screen.getByRole('button', { name: /pipelines ophalen|fetch pipelines/i }))
    await waitFor(() =>
      expect((screen.getByLabelText(/^pipeline$/i) as unknown as HTMLSelectElement).options.length).toBeGreaterThan(0),
    )
    fireEvent.change(screen.getByLabelText(/^pipeline$/i), { target: { value: 'pl-1' } })
    fireEvent.change(screen.getByLabelText(/^stage$|^fase$/i), { target: { value: 'st-1' } })
    fireEvent.change(screen.getByLabelText(/servicekey|service key/i), { target: { value: 'shhh-secret' } })
    fireEvent.change(screen.getByLabelText(/hubspot.account.id/i), { target: { value: '999' } })

    clickTicketsSave()

    await waitFor(() => expect(saveRequestBody()).toBeDefined())
    expect(saveRequestBody()).toEqual({
      service_key: 'shhh-secret',
      hubspot_portal_id: 999,
      targets: [{ key: 'sales', label: 'Sales', pipeline_id: 'pl-1', stage_id: 'st-1' }],
    })
  })

  it('saves without a HubSpot account ID, which the server then fetches itself', async () => {
    mockApi({ tickets: { configured: false, hubspot_portal_id: null, portal_id_source: null, targets: [] } })
    renderTab(makeWidget())

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /doel toevoegen|add target/i })).toBeTruthy(),
    )
    fireEvent.click(screen.getByRole('button', { name: /doel toevoegen|add target/i }))
    fireEvent.change(screen.getByLabelText(/label, (bv\.|e\.g\.) sales/i), { target: { value: 'Sales' } })
    fireEvent.click(screen.getByRole('button', { name: /pipelines ophalen|fetch pipelines/i }))
    await waitFor(() =>
      expect((screen.getByLabelText(/^pipeline$/i) as unknown as HTMLSelectElement).options.length).toBeGreaterThan(0),
    )
    fireEvent.change(screen.getByLabelText(/^pipeline$/i), { target: { value: 'pl-1' } })
    fireEvent.change(screen.getByLabelText(/^stage$|^fase$/i), { target: { value: 'st-1' } })
    fireEvent.change(screen.getByLabelText(/servicekey|service key/i), { target: { value: 'shhh-secret' } })
    expect(
      screen.getByText(
        /alleen nodig als de servicekey het accountnummer niet zelf mag ophalen|only needed when the service key may not fetch the account number itself/i,
      ),
    ).toBeTruthy()

    clickTicketsSave()

    await waitFor(() => expect(saveRequestBody()).toBeDefined())
    expect(saveRequestBody()).toEqual({
      service_key: 'shhh-secret',
      targets: [{ key: 'sales', label: 'Sales', pipeline_id: 'pl-1', stage_id: 'st-1' }],
    })
  })

  it('labels an account ID fetched from HubSpot and does not send it back', async () => {
    // A key swap to another account must not trip account_mismatch on an id
    // the admin never typed.
    mockApi({
      tickets: {
        configured: true,
        hubspot_portal_id: 4455,
        portal_id_source: 'hubspot',
        targets: [{ key: 'sales', label: 'Sales', pipeline_id: 'pl-1', stage_id: 'st-1' }],
      },
    })
    renderTab(makeWidget())

    await waitFor(() => expect(screen.getByDisplayValue('4455')).toBeTruthy())
    expect(screen.getByText(/opgehaald uit hubspot|fetched from hubspot/i)).toBeTruthy()
    clickTicketsSave()

    await waitFor(() => expect(saveRequestBody()).toBeDefined())
    expect(saveRequestBody()).toEqual({
      targets: [{ key: 'sales', label: 'Sales', pipeline_id: 'pl-1', stage_id: 'st-1' }],
    })
  })

  it.each([
    ['portal_id_required', /vul het hubspot-account-id in|fill in the hubspot account id/i],
    ['account_mismatch', /hoort niet bij deze servicekey|does not belong to this service key/i],
  ])('maps a 422 %s response to a readable message', async (detail, message) => {
    mockApi({
      tickets: {
        configured: true,
        hubspot_portal_id: 12345,
        portal_id_source: 'manual',
        targets: [{ key: 'sales', label: 'Sales', pipeline_id: 'pl-1', stage_id: 'st-1' }],
      },
      saveResult: () => Promise.reject(new ApiErrorCtor(422, detail)),
    })
    renderTab(makeWidget())

    await waitFor(() => expect(screen.getByDisplayValue('Sales')).toBeTruthy())
    clickTicketsSave()

    await waitFor(() => expect(within(ticketsCard()).getByText(message)).toBeTruthy())
  })

  it('lists every scope the full flow uses in the key help', async () => {
    mockApi({ tickets: { configured: false, hubspot_portal_id: null, portal_id_source: null, targets: [] } })
    renderTab(makeWidget())

    await waitFor(() => expect(screen.getByLabelText(/servicekey|service key/i)).toBeTruthy())
    const help = within(ticketsCard()).getByText(/crm\.objects\.tickets\.write/).textContent ?? ''
    for (const scope of [
      'crm.objects.contacts.read',
      'crm.objects.contacts.write',
      'crm.objects.companies.read',
      'oauth',
    ]) {
      expect(help).toContain(scope)
    }
  })

  it('omits service_key when the field is left empty on an already configured widget, and prefills the account ID', async () => {
    mockApi({
      tickets: {
        configured: true,
        hubspot_portal_id: 12345,
        portal_id_source: 'manual',
        targets: [{ key: 'sales', label: 'Sales', pipeline_id: 'pl-1', stage_id: 'st-1' }],
      },
    })
    renderTab(makeWidget())

    await waitFor(() => expect(screen.getByDisplayValue('Sales')).toBeTruthy())
    expect(screen.getByDisplayValue('12345')).toBeTruthy()
    clickTicketsSave()

    await waitFor(() => expect(saveRequestBody()).toBeDefined())
    expect(saveRequestBody()).toEqual({
      hubspot_portal_id: 12345,
      targets: [{ key: 'sales', label: 'Sales', pipeline_id: 'pl-1', stage_id: 'st-1' }],
    })
  })

  it('maps a 422 invalid_service_key response to a readable message', async () => {
    mockApi({
      tickets: {
        configured: true,
        hubspot_portal_id: 12345,
        portal_id_source: 'manual',
        targets: [{ key: 'sales', label: 'Sales', pipeline_id: 'pl-1', stage_id: 'st-1' }],
      },
      saveResult: () => Promise.reject(new ApiErrorCtor(422, 'invalid_service_key')),
    })
    renderTab(makeWidget())

    await waitFor(() => expect(screen.getByDisplayValue('Sales')).toBeTruthy())
    clickTicketsSave()

    await waitFor(() =>
      expect(screen.getByText(/ongeldig|invalid/i)).toBeTruthy(),
    )
  })

  it('maps a 422 service_key_required response from Fetch pipelines to a readable message', async () => {
    mockApi({ tickets: { configured: false, hubspot_portal_id: null, portal_id_source: null, targets: [] } })
    apiFetchMock.mockImplementation((path: unknown, init?: RequestInit) => {
      const url = String(path)
      const method = init?.method ?? 'GET'
      if (url.endsWith('/integrations/hubspot')) return Promise.reject(new ApiErrorCtor(404, 'not_found'))
      if (url.endsWith('/integrations/tickets') && method === 'GET') {
        return Promise.resolve({ configured: false, hubspot_portal_id: null, portal_id_source: null, targets: [] })
      }
      if (url.endsWith('/integrations/tickets/pipelines') && method === 'POST') {
        return Promise.reject(new ApiErrorCtor(422, 'service_key_required'))
      }
      return Promise.reject(new Error(`Unexpected apiFetch: ${method} ${url}`))
    })
    renderTab(makeWidget())

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /pipelines ophalen|fetch pipelines/i })).toBeTruthy(),
    )
    fireEvent.click(screen.getByRole('button', { name: /pipelines ophalen|fetch pipelines/i }))

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        expect.stringMatching(/vul de servicekey in|fill in the service key/i),
      ),
    )
  })
})
