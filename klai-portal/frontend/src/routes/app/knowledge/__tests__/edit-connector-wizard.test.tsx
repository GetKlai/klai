import type { JSX, ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ConnectorSummary } from '../$kbSlug/-kb-types'
import type { AuthProbeResult, PreviewResult } from '../-connector-types'

const routerMocks: {
  navigate: ReturnType<typeof vi.fn>
  search: { step?: 'auth' | 'selector'; show?: 'picker' }
} = vi.hoisted(() => ({
  navigate: vi.fn(),
  search: {},
}))

vi.mock('@tanstack/react-router', async () => {
  const actual = await vi.importActual<typeof import('@tanstack/react-router')>(
    '@tanstack/react-router',
  )
  return {
    ...actual,
    useNavigate: () => routerMocks.navigate,
    createFileRoute: () => (config: object) => ({
      ...config,
      useParams: () => ({ kbSlug: 'handbook', connectorId: 'connector-1' }),
      useSearch: () => routerMocks.search,
    }),
  }
})

const apiFetchMock = vi.hoisted(() => vi.fn())
vi.mock('@/lib/apiFetch', async () => {
  const actual = await vi.importActual<typeof import('@/lib/apiFetch')>('@/lib/apiFetch')
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) }
})

vi.mock('@/lib/auth', () => ({
  useAuth: () => ({ isAuthenticated: true }),
}))

vi.mock('@/paraglide/messages', async () => {
  const actual = await vi.importActual<typeof import('@/paraglide/messages')>(
    '@/paraglide/messages',
  )
  return {
    ...actual,
    admin_connectors_edit_title: () => 'Edit connector',
    admin_connectors_webcrawler_step_details: () => 'Details',
    admin_connectors_webcrawler_step_preview: () => 'Preview',
    admin_connectors_webcrawler_step_settings: () => 'Settings',
    admin_connectors_webcrawler_next: () => 'Next',
    admin_connectors_webcrawler_back: () => 'Back',
    admin_connectors_cancel: () => 'Cancel',
    admin_connectors_field_name: () => 'Name',
    admin_connectors_webcrawler_base_url: () => 'Base URL',
    admin_connectors_webcrawler_path_prefix: () => 'Path prefix (optional)',
    admin_connectors_webcrawler_preview_url: () => 'Preview URL',
    admin_connectors_webcrawler_run_preview: () => 'Run preview',
    admin_connectors_webcrawler_try_ai: () => 'Let AI find the content selector',
    admin_connectors_webcrawler_ai_selector_detected: ({
      selector,
      count,
    }: {
      selector: string
      count: string
    }) => `AI detected ${selector} with ${count} words`,
    admin_connectors_webcrawler_ai_selector_use: () => 'Use this selector',
    admin_connectors_webcrawler_max_pages: () => 'Maximum pages',
    admin_connectors_save: () => 'Save changes',
  }
})

import { Route as RouteConfig } from '../$kbSlug_.edit-connector.$connectorId'

const authOk: AuthProbeResult = {
  classification: 'auth_ok',
  match_reasons: [],
  word_count: 640,
  auth_guard: null,
}

function previewResult(overrides: Partial<PreviewResult> = {}): PreviewResult {
  return {
    fit_markdown: '# Article\nUseful body',
    word_count: 420,
    warnings: [],
    content_selector: null,
    selector_source: null,
    auth_guard: null,
    classification: 'success',
    classification_reason: null,
    sample_pages_crawled: 0,
    sample_pages_usable: 0,
    ...overrides,
  }
}

function webCrawler(overrides: Partial<ConnectorSummary> = {}): ConnectorSummary {
  return {
    id: 'connector-1',
    name: 'Team handbook',
    connector_type: 'web_crawler',
    config: {
      base_url: 'https://docs.example.com',
      path_prefix: '/guide',
      max_pages: 350,
      discovery_seed_url: 'https://docs.example.com/guide/article',
    },
    schedule: null,
    is_enabled: true,
    last_sync_status: 'success',
    last_sync_at: null,
    last_sync_documents_ok: 12,
    allowed_assertion_modes: null,
    has_saved_credentials: false,
    ...overrides,
  }
}

let connectorFixture: ConnectorSummary
let credentialMetadata: { cookie_names: string[] }
let authProbeResponse: AuthProbeResult
let previewResponse: PreviewResult
let patchError: Error | null

function installApiBoundary() {
  apiFetchMock.mockImplementation((url: string, options?: RequestInit) => {
    if (url.endsWith('/credential-metadata')) return credentialMetadata
    if (url.endsWith('/auth-probe')) return authProbeResponse
    if (url.endsWith('/crawl-preview')) return previewResponse
    if (options?.method === 'PATCH') {
      if (patchError) throw patchError
      return undefined
    }
    if (url === '/api/app/knowledge-bases/handbook/connectors/') return [connectorFixture]
    throw new Error(`Unexpected apiFetch call: ${url}`)
  })
}

function Wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

async function renderWizard() {
  const config = RouteConfig as unknown as { component: () => JSX.Element }
  const result = render(
    <Wrapper>
      <config.component />
    </Wrapper>,
  )
  await screen.findByText('Team handbook')
  return result
}

async function advanceToPublicPreview() {
  await renderWizard()
  fireEvent.click(screen.getByRole('button', { name: 'Next' }))
  fireEvent.click(screen.getByRole('button', { name: 'Public site' }))
  fireEvent.click(screen.getByRole('button', { name: 'Next' }))
}

async function advanceToCookieSetup() {
  await renderWizard()
  fireEvent.click(screen.getByRole('button', { name: 'Next' }))
  fireEvent.click(screen.getByRole('button', { name: 'Login required' }))
  fireEvent.click(screen.getByRole('button', { name: 'Next' }))
}

async function advanceSavedToAuthSetup() {
  connectorFixture = webCrawler({ has_saved_credentials: true })
  await renderWizard()
  fireEvent.click(screen.getByRole('button', { name: 'Next' }))
}

beforeEach(() => {
  routerMocks.navigate.mockReset()
  routerMocks.search = {}
  apiFetchMock.mockReset()
  connectorFixture = webCrawler()
  credentialMetadata = { cookie_names: ['sessionid', 'csrf'] }
  authProbeResponse = authOk
  previewResponse = previewResult()
  patchError = null
  installApiBoundary()
})

describe('edit connector wizard characterization', () => {
  it('loads current values and renders four crawler steps without connector type', async () => {
    await renderWizard()

    expect(screen.getByLabelText<HTMLInputElement>('Base URL').value).toBe(
      'https://docs.example.com',
    )
    for (const step of ['Details', 'Authentication', 'Preview', 'Settings']) {
      expect(screen.getByRole('button', { name: new RegExp(step) })).toBeTruthy()
    }
    expect(screen.queryByRole('button', { name: /Connector type/ })).toBeNull()
  })

  it('blocks Details for empty values but advances for a non-empty invalid URL', async () => {
    await renderWizard()
    fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: '' } })

    const next = screen.getByRole<HTMLButtonElement>('button', { name: 'Next' })
    expect(next.disabled).toBe(true)

    fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'not a URL' } })
    expect(next.disabled).toBe(false)
    fireEvent.click(next)
    expect(screen.getByText('Is this site behind a login?')).toBeTruthy()
  })

  it('preserves the stored discovery seed when Details advances', async () => {
    await renderWizard()

    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    fireEvent.click(screen.getByRole('button', { name: 'Public site' }))
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))

    expect(screen.getByLabelText<HTMLInputElement>('Preview URL').value).toBe(
      'https://docs.example.com/guide/article',
    )
  })

  it('blocks Authentication until a mode is chosen and public mode skips setup', async () => {
    await renderWizard()
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))

    const next = screen.getByRole<HTMLButtonElement>('button', { name: 'Next' })
    expect(next.disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Public site' }))
    fireEvent.click(next)

    expect(screen.getByText('Public site - no login needed')).toBeTruthy()
    expect(screen.queryByText('Authentication cookies')).toBeNull()
  })

  it('without saved credentials, login mode uses editable cookies and blocks before auth succeeds', async () => {
    await advanceToCookieSetup()

    expect(screen.getByLabelText('Cookie name')).toBeTruthy()
    expect(screen.getByLabelText('Cookie value')).toBeTruthy()
    expect(screen.queryByText('Saved authentication configured')).toBeNull()
    expect(screen.getByRole<HTMLButtonElement>('button', { name: 'Next' }).disabled).toBe(true)
  })

  it('with saved credentials, Details skips the login question and opens saved authentication', async () => {
    await advanceSavedToAuthSetup()

    expect(screen.queryByText('Is this site behind a login?')).toBeNull()
    expect(screen.getByText('Saved authentication configured')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Test saved authentication' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Replace cookies' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Use without login' })).toBeTruthy()
  })

  it('tests saved authentication by connector id and exposes Change authentication in the Alert', async () => {
    await advanceSavedToAuthSetup()

    fireEvent.click(screen.getByRole('button', { name: 'Test saved authentication' }))
    expect(await screen.findByText("You're in. Continue to Selector.")).toBeTruthy()
    const authCall = apiFetchMock.mock.calls.find(([url]) =>
      String(url).endsWith('/auth-probe'),
    )
    expect(JSON.parse((authCall?.[1] as RequestInit).body as string)).toEqual({
      url: 'https://docs.example.com/guide/',
      cookies: null,
      connector_id: 'connector-1',
      use_saved_credentials: true,
    })

    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    const alert = screen.getByRole('alert')
    expect(alert.textContent).toContain('Logged in - saved authentication verified')
    fireEvent.click(screen.getByRole('button', { name: 'Change authentication' }))
    expect(screen.getByText('Saved authentication configured')).toBeTruthy()
  })

  it('Replace cookies prefills only saved cookie names and posts replacement values', async () => {
    await advanceSavedToAuthSetup()
    await waitFor(() => {
      expect(apiFetchMock.mock.calls.some(([url]) => String(url).endsWith('/credential-metadata'))).toBe(
        true,
      )
    })

    fireEvent.click(screen.getByRole('button', { name: 'Replace cookies' }))
    expect(screen.getByDisplayValue('sessionid')).toBeTruthy()
    expect(screen.getByDisplayValue('csrf')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Cookie value'), { target: { value: 'new-secret' } })
    fireEvent.click(screen.getByRole('button', { name: 'Test authentication' }))

    expect(await screen.findByText("You're in. Continue to Selector.")).toBeTruthy()
    const authCall = apiFetchMock.mock.calls.find(([url]) =>
      String(url).endsWith('/auth-probe'),
    )
    expect(JSON.parse((authCall?.[1] as RequestInit).body as string)).toEqual({
      url: 'https://docs.example.com/guide/',
      cookies: [
        {
          name: 'sessionid',
          value: 'new-secret',
          domain: 'docs.example.com',
          path: '/',
        },
      ],
      connector_id: null,
      use_saved_credentials: false,
    })
  })

  it('shows cookie confirmation and Edit cookies after replacement auth succeeds', async () => {
    await advanceSavedToAuthSetup()
    fireEvent.click(screen.getByRole('button', { name: 'Replace cookies' }))
    fireEvent.click(screen.getByRole('button', { name: 'Test authentication' }))
    expect(await screen.findByText("You're in. Continue to Selector.")).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Next' }))

    const alert = screen.getByRole('alert')
    expect(alert.textContent).toContain('Logged in - cookies verified')
    expect(screen.getByRole('button', { name: 'Edit cookies' })).toBeTruthy()
  })

  it('renders auth failure and keeps progress blocked', async () => {
    authProbeResponse = {
      ...authOk,
      classification: 'auth_failed_credentials_invalid',
      word_count: 0,
    }
    await advanceToCookieSetup()

    fireEvent.click(screen.getByRole('button', { name: 'Test authentication' }))

    expect(await screen.findByText('401/403 - credentials rejected.')).toBeTruthy()
    expect(screen.getByRole<HTMLButtonElement>('button', { name: 'Next' }).disabled).toBe(true)
  })

  it('renders and applies an AI-found selector', async () => {
    previewResponse = previewResult({
      classification: 'selector_required',
      classification_reason: 'Navigation dominates the page.',
      selector_source: 'ai',
      content_selector: 'main article',
      word_count: 777,
    })
    await advanceToPublicPreview()

    fireEvent.click(screen.getByRole('button', { name: 'Let AI find the content selector' }))

    expect(await screen.findByText('AI detected main article with 777 words')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Use this selector' }))
    expect(
      (document.getElementById('edit-wc-content-selector') as HTMLInputElement).value,
    ).toBe('main article')
    expect(screen.queryByRole('button', { name: 'Use this selector' })).toBeNull()
  })

  it('renders the AI-not-found reason without a Use this selector control', async () => {
    previewResponse = previewResult({
      classification: 'selector_returns_empty',
      classification_reason: 'AI found no selector with enough text.',
      selector_source: 'ai_failed',
      content_selector: null,
      word_count: 0,
      fit_markdown: '',
    })
    await advanceToPublicPreview()

    fireEvent.click(screen.getByRole('button', { name: 'Let AI find the content selector' }))

    expect(await screen.findByText('AI found no selector with enough text.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Use this selector' })).toBeNull()
  })

  it('keeps AI find available when an edit connector already has a selector', async () => {
    connectorFixture = webCrawler({
      config: {
        base_url: 'https://docs.example.com',
        path_prefix: '',
        max_pages: 200,
        content_selector: 'article',
      },
    })
    await advanceToPublicPreview()

    expect(screen.getByDisplayValue('article')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Let AI find the content selector' })).toBeTruthy()
  })

  it('includes explicit saved-auth fields in preview requests', async () => {
    await advanceSavedToAuthSetup()
    fireEvent.click(screen.getByRole('button', { name: 'Test saved authentication' }))
    expect(await screen.findByText("You're in. Continue to Selector.")).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))

    fireEvent.click(screen.getByRole('button', { name: 'Run preview' }))
    await screen.findByText(/You can save the connector/)

    const previewCall = apiFetchMock.mock.calls.find(([url]) =>
      String(url).endsWith('/crawl-preview'),
    )
    expect(JSON.parse((previewCall?.[1] as RequestInit).body as string)).toEqual({
      url: 'https://docs.example.com/guide/article',
      content_selector: null,
      try_ai: false,
      cookies: null,
      connector_id: 'connector-1',
      use_saved_credentials: true,
    })
  })

  it('allows selector_required to reach Settings and enables public Save', async () => {
    previewResponse = previewResult({
      classification: 'selector_required',
      classification_reason: 'Choose a narrower selector.',
      fit_markdown: '',
      word_count: 0,
    })
    await advanceToPublicPreview()

    fireEvent.click(screen.getByRole('button', { name: 'Run preview' }))
    expect(await screen.findByText('Choose a narrower selector.')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))

    expect(screen.getByLabelText('Maximum pages')).toBeTruthy()
    expect(screen.getByRole<HTMLButtonElement>('button', { name: 'Save changes' }).disabled).toBe(
      false,
    )
  })

  it('PATCHes the current crawler payload and navigates after success', async () => {
    await advanceToPublicPreview()
    fireEvent.click(screen.getByRole('button', { name: 'Run preview' }))
    expect(await screen.findByText(/You can save the connector/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => {
      expect(routerMocks.navigate).toHaveBeenCalledTimes(1)
    })
    const patchCall = apiFetchMock.mock.calls.find(
      ([url, options]) =>
        url === '/api/app/knowledge-bases/handbook/connectors/connector-1' &&
        (options as RequestInit | undefined)?.method === 'PATCH',
    )
    expect(patchCall).toBeTruthy()
    expect(JSON.parse((patchCall?.[1] as RequestInit).body as string)).toEqual({
      name: 'Team handbook',
      config: {
        base_url: 'https://docs.example.com',
        path_prefix: '/guide',
        max_pages: 350,
        discovery_seed_url: 'https://docs.example.com/guide/article',
      },
    })
    expect(routerMocks.navigate).toHaveBeenCalledWith({
      to: '/app/knowledge/$kbSlug',
      params: { kbSlug: 'handbook' },
      search: { tab: 'connectors' },
    })
  })

  it('marks saved credentials for clearing when Use without login is saved', async () => {
    await advanceSavedToAuthSetup()
    fireEvent.click(screen.getByRole('button', { name: 'Use without login' }))
    expect(screen.getByText('Public site - no login needed')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Run preview' }))
    await screen.findByText(/You can save the connector/)
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => {
      expect(routerMocks.navigate).toHaveBeenCalledTimes(1)
    })
    const patchCall = apiFetchMock.mock.calls.find(
      ([, options]) => (options as RequestInit | undefined)?.method === 'PATCH',
    )
    expect(JSON.parse((patchCall?.[1] as RequestInit).body as string)).toMatchObject({
      clear_credentials: true,
    })
  })

  it('renders an update failure and does not navigate', async () => {
    patchError = new Error('Update exploded')
    await advanceToPublicPreview()
    fireEvent.click(screen.getByRole('button', { name: 'Run preview' }))
    await screen.findByText(/You can save the connector/)
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))

    expect(await screen.findByText('Update exploded')).toBeTruthy()
    expect(routerMocks.navigate).not.toHaveBeenCalled()
  })

  it('header and Details Cancel both navigate immediately without a confirm dialog', async () => {
    await renderWizard()
    const cancelButtons = screen.getAllByRole('button', { name: 'Cancel' })
    expect(cancelButtons).toHaveLength(2)

    fireEvent.click(cancelButtons[0])
    fireEvent.click(cancelButtons[1])

    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(routerMocks.navigate).toHaveBeenCalledTimes(2)
  })

  it('Auth Back returns to the login question without saved credentials and Details with them', async () => {
    await advanceToCookieSetup()
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    expect(screen.getByText('Is this site behind a login?')).toBeTruthy()

    cleanup()
    await advanceSavedToAuthSetup()
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    expect(screen.getByLabelText('Base URL')).toBeTruthy()
    expect(screen.queryByText('Is this site behind a login?')).toBeNull()
  })

  it('honors auth and selector deep links in the real route component', async () => {
    routerMocks.search = { step: 'auth' }
    await renderWizard()
    expect(screen.getByText('Authentication cookies')).toBeTruthy()

    cleanup()
    routerMocks.search = { step: 'selector' }
    await renderWizard()
    expect(screen.getByLabelText('Preview URL')).toBeTruthy()
    expect(screen.queryByText('Is this site behind a login?')).toBeNull()
  })
})
