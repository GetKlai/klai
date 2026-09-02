import type { JSX, ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AuthProbeResult, PreviewResult } from '../-connector-types'

const routerMocks: {
  navigate: ReturnType<typeof vi.fn>
  search: { type?: string }
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
      useParams: () => ({ kbSlug: 'handbook' }),
      useSearch: () => routerMocks.search,
    }),
  }
})

const apiFetchMock = vi.hoisted(() => vi.fn())
vi.mock('@/lib/apiFetch', async () => {
  const actual = await vi.importActual<typeof import('@/lib/apiFetch')>('@/lib/apiFetch')
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) }
})

vi.mock('@/paraglide/messages', async () => {
  const actual = await vi.importActual<typeof import('@/paraglide/messages')>(
    '@/paraglide/messages',
  )
  return {
    ...actual,
    admin_connectors_step_type: () => 'Connector type',
    admin_connectors_step_configure: () => 'Configure',
    admin_connectors_webcrawler_step_details: () => 'Details',
    admin_connectors_webcrawler_step_preview: () => 'Preview',
    admin_connectors_webcrawler_step_settings: () => 'Settings',
    admin_connectors_webcrawler_next: () => 'Next',
    admin_connectors_webcrawler_back: () => 'Back',
    admin_connectors_cancel: () => 'Cancel',
    admin_connectors_field_name: () => 'Name',
    admin_connectors_type_website: () => 'Website',
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
    admin_connectors_create_submit: () => 'Add connector',
  }
})

import { Route as RouteConfig } from '../$kbSlug_.add-connector'

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

function Wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

function renderWizard() {
  const config = RouteConfig as unknown as { component: () => JSX.Element }
  return render(
    <Wrapper>
      <config.component />
    </Wrapper>,
  )
}

function selectWebsite() {
  fireEvent.click(screen.getByRole('button', { name: 'Website' }))
}

function fillDetails({
  name = 'Team handbook',
  baseUrl = 'https://docs.example.com',
  pathPrefix = '',
}: {
  name?: string
  baseUrl?: string
  pathPrefix?: string
} = {}) {
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: name } })
  fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: baseUrl } })
  if (pathPrefix) {
    fireEvent.change(screen.getByLabelText('Path prefix (optional)'), {
      target: { value: pathPrefix },
    })
  }
}

function advanceToPublicPreview() {
  selectWebsite()
  fillDetails()
  fireEvent.click(screen.getByRole('button', { name: 'Next' }))
  fireEvent.click(screen.getByRole('button', { name: 'Public site' }))
  fireEvent.click(screen.getByRole('button', { name: 'Next' }))
}

function advanceToCookieSetup() {
  selectWebsite()
  fillDetails()
  fireEvent.click(screen.getByRole('button', { name: 'Next' }))
  fireEvent.click(screen.getByRole('button', { name: 'Login required' }))
  fireEvent.click(screen.getByRole('button', { name: 'Next' }))
}

beforeEach(() => {
  routerMocks.navigate.mockReset()
  routerMocks.search = {}
  apiFetchMock.mockReset()
})

describe('add connector wizard characterization', () => {
  it('starts with all connector choices and the five-step crawler indicator', () => {
    renderWizard()

    for (const type of [
      'GitHub',
      'Website',
      'Google Drive',
      'Notion',
      'Office 365',
      'Airtable',
      'Confluence',
      'JSON feed',
    ]) {
      expect(screen.getByRole('button', { name: new RegExp(type) })).toBeTruthy()
    }
    for (const step of ['Connector type', 'Details', 'Authentication', 'Preview', 'Settings']) {
      expect(screen.getByRole('button', { name: new RegExp(step) })).toBeTruthy()
    }
  })

  it('collapses a simple connector to Type and Configure steps', () => {
    renderWizard()

    fireEvent.click(screen.getByRole('button', { name: /GitHub/ }))

    expect(screen.getByRole('button', { name: /Connector type/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Configure/ })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Authentication/ })).toBeNull()
    expect(screen.getByLabelText('GitHub App installation ID')).toBeTruthy()
  })

  it('blocks Details while required values are empty but advances for a non-empty invalid URL', () => {
    renderWizard()
    selectWebsite()

    const next = screen.getByRole<HTMLButtonElement>('button', { name: 'Next' })
    expect(next.disabled).toBe(true)

    fillDetails({ baseUrl: 'not a URL' })
    expect(next.disabled).toBe(false)
    fireEvent.click(next)

    expect(screen.getByText('Is this site behind a login?')).toBeTruthy()
  })

  it('advances Details to Authentication and seeds Preview URL from Base URL', () => {
    renderWizard()
    selectWebsite()
    fillDetails({ baseUrl: 'https://docs.example.com/start' })

    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByText('Is this site behind a login?')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Public site' }))
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByLabelText<HTMLInputElement>('Preview URL').value).toBe(
      'https://docs.example.com/start',
    )
  })

  it('blocks Authentication until a mode is chosen and public mode skips cookie setup', () => {
    renderWizard()
    selectWebsite()
    fillDetails()
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))

    const next = screen.getByRole<HTMLButtonElement>('button', { name: 'Next' })
    expect(next.disabled).toBe(true)

    fireEvent.click(screen.getByRole('button', { name: 'Public site' }))
    fireEvent.click(next)

    expect(screen.getByText('Public site - no login needed')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Actually, it needs login' })).toBeTruthy()
    expect(screen.queryByLabelText('Cookie name')).toBeNull()
  })

  it('login mode renders cookie fields and blocks progress before auth succeeds', () => {
    renderWizard()
    advanceToCookieSetup()

    expect(screen.getByText('Authentication cookies')).toBeTruthy()
    expect(screen.getByLabelText('Cookie name')).toBeTruthy()
    expect(screen.getByLabelText('Cookie value')).toBeTruthy()
    expect(screen.getByRole<HTMLButtonElement>('button', { name: 'Next' }).disabled).toBe(true)
  })

  it('posts normalized cookies to auth-probe and keeps Next blocked on auth failure', async () => {
    apiFetchMock.mockResolvedValueOnce({
      ...authOk,
      classification: 'auth_failed_still_walled',
      match_reasons: ['login_marker'],
    })
    renderWizard()
    advanceToCookieSetup()
    fireEvent.change(screen.getByLabelText('Cookie name'), { target: { value: ' session ' } })
    fireEvent.change(screen.getByLabelText('Cookie value'), { target: { value: ' secret ' } })

    fireEvent.click(screen.getByRole('button', { name: 'Test authentication' }))

    expect(await screen.findByText(/Cookies didn't unlock/)).toBeTruthy()
    expect(apiFetchMock).toHaveBeenCalledWith(
      '/api/app/knowledge-bases/handbook/connectors/auth-probe',
      {
        method: 'POST',
        body: JSON.stringify({
          url: 'https://docs.example.com/',
          cookies: [
            {
              name: 'session',
              value: 'secret',
              domain: 'docs.example.com',
              path: '/',
            },
          ],
        }),
      },
    )
    expect(screen.getByRole<HTMLButtonElement>('button', { name: 'Next' }).disabled).toBe(true)
  })

  it('shows the cookie Logged in Alert and Edit cookies control after auth succeeds', async () => {
    apiFetchMock.mockResolvedValueOnce(authOk)
    renderWizard()
    advanceToCookieSetup()

    fireEvent.click(screen.getByRole('button', { name: 'Test authentication' }))
    expect(await screen.findByText("You're in. Continue to Selector.")).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))

    const alert = screen.getByRole('alert')
    expect(alert.textContent).toContain('Logged in - cookies verified')
    fireEvent.click(screen.getByRole('button', { name: 'Edit cookies' }))
    expect(screen.getByText('Authentication cookies')).toBeTruthy()
  })

  it('renders and applies an AI-found selector', async () => {
    apiFetchMock.mockResolvedValueOnce(
      previewResult({
        classification: 'selector_required',
        classification_reason: 'Navigation dominates the page.',
        selector_source: 'ai',
        content_selector: 'main article',
        word_count: 777,
      }),
    )
    renderWizard()
    advanceToPublicPreview()

    fireEvent.click(screen.getByRole('button', { name: 'Let AI find the content selector' }))

    expect(await screen.findByText('AI detected main article with 777 words')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Use this selector' }))
    expect((document.getElementById('wc-preview-selector') as HTMLInputElement).value).toBe(
      'main article',
    )
    expect(screen.queryByRole('button', { name: 'Use this selector' })).toBeNull()
  })

  it('renders the AI-not-found reason without a Use this selector control', async () => {
    apiFetchMock.mockResolvedValueOnce(
      previewResult({
        classification: 'selector_returns_empty',
        classification_reason: 'AI found no selector with enough text.',
        selector_source: 'ai_failed',
        content_selector: null,
        word_count: 0,
        fit_markdown: '',
      }),
    )
    renderWizard()
    advanceToPublicPreview()

    fireEvent.click(screen.getByRole('button', { name: 'Let AI find the content selector' }))

    expect(await screen.findByText('AI found no selector with enough text.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Use this selector' })).toBeNull()
  })

  it('allows a selector_required preview to advance to Settings', async () => {
    apiFetchMock.mockResolvedValueOnce(
      previewResult({
        classification: 'selector_required',
        classification_reason: 'Choose a narrower selector.',
        fit_markdown: '',
        word_count: 0,
      }),
    )
    renderWizard()
    advanceToPublicPreview()

    fireEvent.click(screen.getByRole('button', { name: 'Run preview' }))
    expect(await screen.findByText('Choose a narrower selector.')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))

    expect(screen.getByLabelText('Maximum pages')).toBeTruthy()
  })

  it('returns an auth-walled preview to cookie setup', async () => {
    apiFetchMock.mockResolvedValueOnce(
      previewResult({ classification: 'auth_wall_detected', fit_markdown: '', word_count: 0 }),
    )
    renderWizard()
    advanceToPublicPreview()

    fireEvent.click(screen.getByRole('button', { name: 'Run preview' }))

    expect(await screen.findByText('Authentication cookies')).toBeTruthy()
    expect(screen.getByRole<HTMLButtonElement>('button', { name: 'Next' }).disabled).toBe(true)
  })

  it('POSTs the current crawler payload and navigates after success', async () => {
    apiFetchMock
      .mockResolvedValueOnce(previewResult())
      .mockResolvedValueOnce(undefined)
    renderWizard()
    advanceToPublicPreview()
    fireEvent.change(screen.getByLabelText('Preview URL'), {
      target: { value: 'https://docs.example.com/article' },
    })

    fireEvent.click(screen.getByRole('button', { name: 'Run preview' }))
    expect(await screen.findByText(/You can save the connector/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    fireEvent.click(screen.getByRole('button', { name: 'Add connector' }))

    await waitFor(() => {
      expect(routerMocks.navigate).toHaveBeenCalledTimes(1)
    })
    const createCall = apiFetchMock.mock.calls.find(
      ([url, options]) =>
        url === '/api/app/knowledge-bases/handbook/connectors/' &&
        (options as RequestInit | undefined)?.method === 'POST',
    )
    expect(createCall).toBeTruthy()
    expect(JSON.parse((createCall?.[1] as RequestInit).body as string)).toEqual({
      name: 'Team handbook',
      connector_type: 'web_crawler',
      config: {
        base_url: 'https://docs.example.com',
        discovery_seed_url: 'https://docs.example.com/article',
      },
      schedule: null,
    })
    expect(routerMocks.navigate).toHaveBeenCalledWith({
      to: '/app/knowledge/$kbSlug',
      params: { kbSlug: 'handbook' },
      search: { tab: 'connectors' },
    })
  })

  it('renders a create failure and does not navigate', async () => {
    apiFetchMock.mockRejectedValueOnce(new Error('Create exploded'))
    renderWizard()
    fireEvent.click(screen.getByRole('button', { name: /GitHub/ }))
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Repo docs' } })
    fireEvent.change(screen.getByLabelText('GitHub App installation ID'), {
      target: { value: '42' },
    })
    fireEvent.change(screen.getByLabelText('Repository owner'), {
      target: { value: 'klai' },
    })
    fireEvent.change(screen.getByLabelText('Repository name'), {
      target: { value: 'portal' },
    })

    fireEvent.click(screen.getByRole('button', { name: 'Add connector' }))

    expect(await screen.findByText('Create exploded')).toBeTruthy()
    expect(routerMocks.navigate).not.toHaveBeenCalled()
  })

  it('header Cancel navigates back immediately without a confirm dialog', () => {
    renderWizard()

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(routerMocks.navigate).toHaveBeenCalledWith({
      to: '/app/knowledge/$kbSlug',
      params: { kbSlug: 'handbook' },
      search: { tab: 'connectors' },
    })
  })

  it('Details Back returns to connector type selection without navigation or confirmation', () => {
    renderWizard()
    selectWebsite()

    fireEvent.click(screen.getByRole('button', { name: 'Back' }))

    expect(screen.getByRole('button', { name: 'Website' })).toBeTruthy()
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(routerMocks.navigate).not.toHaveBeenCalled()
  })
})
