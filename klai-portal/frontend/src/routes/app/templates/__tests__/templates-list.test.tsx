import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

// Keep tests import-safe: stub the route-created export before importing it.
const navigate = vi.fn()
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
  createFileRoute: () => (cfg: unknown) => cfg,
  useParams: () => ({ slug: '' }),
}))

const apiFetchMock = vi.fn()
vi.mock('@/lib/apiFetch', async () => {
  const actual = await vi.importActual<typeof import('@/lib/apiFetch')>('@/lib/apiFetch')
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) }
})

const currentUserValue: { isAdmin: boolean; user_id: string } = { isAdmin: false, user_id: 'me' }
vi.mock('@/hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({ data: currentUserValue }),
}))

vi.mock('@/components/layout/ProductGuard', () => ({
  ProductGuard: ({ children }: { children: ReactNode }) => <>{children}</>,
}))

import { TemplatesPage } from '../index'

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

beforeEach(() => {
  navigate.mockReset()
  apiFetchMock.mockReset()
})

function mockTemplates(templates: Array<Record<string, unknown>>) {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === '/api/app/templates') return Promise.resolve(templates)
    if (path === '/api/app/account/kb-preference') return Promise.resolve({ active_template_ids: null })
    return Promise.reject(new Error(`Unexpected apiFetch: ${path}`))
  })
}

describe('TemplatesPage — empty state', () => {
  it('renders dashed-border empty state with CTA when list is empty', async () => {
    currentUserValue.isAdmin = true
    mockTemplates([])

    const { container } = render(
      <Wrapper>
        <TemplatesPage />
      </Wrapper>,
    )

    await waitFor(() => expect(screen.getByText(/nog geen templates|no templates yet/i)).toBeTruthy())
    expect(container.querySelector('.border-dashed')).not.toBeNull()
    expect(screen.getByRole('button', { name: /eerste template aanmaken|create your first template/i })).toBeTruthy()
  })
})

describe('TemplatesPage — CTA label depends on role', () => {
  it('admin CTA label is "Nieuwe template"', async () => {
    currentUserValue.isAdmin = true
    mockTemplates([])

    render(
      <Wrapper>
        <TemplatesPage />
      </Wrapper>,
    )

    await waitFor(() => {
      const btns = screen.getAllByRole('button', { name: /^nieuwe template$|^new template$/i })
      expect(btns.length).toBeGreaterThan(0)
    })
  })

  it('non-admin CTA label is "Nieuwe persoonlijke template"', async () => {
    currentUserValue.isAdmin = false
    mockTemplates([])

    render(
      <Wrapper>
        <TemplatesPage />
      </Wrapper>,
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /nieuwe persoonlijke template|new personal template/i })).toBeTruthy()
    })
  })
})

describe('TemplatesPage — populated list', () => {
  it('renders each template name and scope badge', async () => {
    currentUserValue.isAdmin = true
    mockTemplates([
      { id: 1, name: 'Klantenservice', slug: 'klantenservice', description: 'help', prompt_text: '', scope: 'org', created_by: 'u1', is_active: true, created_at: '', updated_at: '' },
      { id: 2, name: 'Mijn eigen', slug: 'mijn-eigen', description: null, prompt_text: '', scope: 'personal', created_by: 'u2', is_active: true, created_at: '', updated_at: '' },
    ])

    render(
      <Wrapper>
        <TemplatesPage />
      </Wrapper>,
    )

    await waitFor(() => expect(screen.getByText('Klantenservice')).toBeTruthy())
    expect(screen.getByText('Mijn eigen')).toBeTruthy()
    expect(screen.getAllByText(/organisatie|organization/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/persoonlijk|personal/i).length).toBeGreaterThan(0)
  })

  it('non-admin cannot delete a template created by someone else', async () => {
    currentUserValue.isAdmin = false
    currentUserValue.user_id = 'me'
    mockTemplates([
      { id: 1, name: 'Iemand anders', slug: 'iemand-anders', description: null, prompt_text: '', scope: 'personal', created_by: 'not-me', is_active: true, created_at: '', updated_at: '' },
    ])

    render(
      <Wrapper>
        <TemplatesPage />
      </Wrapper>,
    )

    await waitFor(() => expect(screen.getByText('Iemand anders')).toBeTruthy())
    // eslint-disable-next-line @typescript-eslint/no-unnecessary-type-assertion
    const deleteBtn = screen.getByRole('button', { name: /verwijderen|delete/i }) as HTMLButtonElement
    expect(deleteBtn.disabled).toBe(true)
  })

  it('admin can delete every row', async () => {
    currentUserValue.isAdmin = true
    currentUserValue.user_id = 'admin-id'
    mockTemplates([
      { id: 1, name: 'Iemand anders', slug: 'iemand-anders', description: null, prompt_text: '', scope: 'personal', created_by: 'not-me', is_active: true, created_at: '', updated_at: '' },
    ])

    render(
      <Wrapper>
        <TemplatesPage />
      </Wrapper>,
    )

    await waitFor(() => expect(screen.getByText('Iemand anders')).toBeTruthy())
    // eslint-disable-next-line @typescript-eslint/no-unnecessary-type-assertion
    const deleteBtn = screen.getByRole('button', { name: /verwijderen|delete/i }) as HTMLButtonElement
    expect(deleteBtn.disabled).toBe(false)
  })
})

describe('TemplatesPage — design compliance', () => {
  it('list container uses mx-auto max-w-3xl', async () => {
    currentUserValue.isAdmin = true
    mockTemplates([])

    const { container } = render(
      <Wrapper>
        <TemplatesPage />
      </Wrapper>,
    )

    await waitFor(() => expect(screen.getByText('Templates')).toBeTruthy())
    const outer = container.querySelector('.mx-auto.max-w-3xl')
    expect(outer).not.toBeNull()
  })

  it('no uppercase / tracking-wider classes anywhere in the rendered tree', async () => {
    currentUserValue.isAdmin = true
    mockTemplates([])

    const { container } = render(
      <Wrapper>
        <TemplatesPage />
      </Wrapper>,
    )

    await waitFor(() => expect(screen.getByText('Templates')).toBeTruthy())
    const html = container.outerHTML
    expect(html).not.toMatch(/\buppercase\b/)
    expect(html).not.toMatch(/tracking-wider/)
  })
})
