import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { EMPTY_TEMPLATE_FORM, TemplateFormPage } from '../-template-form'

// @tanstack/react-router would otherwise require a full router context.
// The form only uses useNavigate, so we stub that alone.
const navigate = vi.fn()
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
}))

// useCurrentUser mock — toggles per test.
const currentUserValue: { isAdmin: boolean; user_id: string } = { isAdmin: true, user_id: 'u1' }
vi.mock('@/hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({ data: currentUserValue }),
}))

// apiFetch mock — mutations don't actually hit the network.
const apiFetchMock = vi.fn()
vi.mock('@/lib/apiFetch', async () => {
  const actual = await vi.importActual<typeof import('@/lib/apiFetch')>('@/lib/apiFetch')
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) }
})

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

beforeEach(() => {
  navigate.mockReset()
  apiFetchMock.mockReset()
})

describe('TemplateFormPage — design compliance', () => {
  it('uses mx-auto max-w-lg container on new form', () => {
    currentUserValue.isAdmin = true
    const { container } = render(
      <Wrapper>
        <TemplateFormPage mode="new" initialForm={EMPTY_TEMPLATE_FORM} />
      </Wrapper>,
    )
    const outer = container.querySelector('.mx-auto.max-w-lg')
    expect(outer).not.toBeNull()
    expect(outer?.className).toContain('px-6')
    expect(outer?.className).toContain('py-10')
  })

  it('primary submit button is rounded-full bg-gray-900', () => {
    currentUserValue.isAdmin = true
    render(
      <Wrapper>
        <TemplateFormPage mode="new" initialForm={EMPTY_TEMPLATE_FORM} />
      </Wrapper>,
    )
    const submit = screen.getByRole('button', { name: /save|opslaan/i })
    expect(submit.className).toContain('rounded-full')
    expect(submit.className).toContain('bg-gray-900')
    expect(submit.className).toContain('text-white')
  })

  it('never renders an uppercase or tracking-wider class anywhere', () => {
    currentUserValue.isAdmin = true
    const { container } = render(
      <Wrapper>
        <TemplateFormPage mode="new" initialForm={EMPTY_TEMPLATE_FORM} />
      </Wrapper>,
    )
    const html = container.outerHTML
    expect(html).not.toMatch(/\buppercase\b/)
    expect(html).not.toMatch(/tracking-wider/)
    expect(html).not.toMatch(/tracking-\[0\.04em\]/)
  })
})

describe('TemplateFormPage — admin-gate on scope="org"', () => {
  it('admin sees "Organisatie" enabled + default scope', () => {
    currentUserValue.isAdmin = true
    render(
      <Wrapper>
        <TemplateFormPage mode="new" initialForm={EMPTY_TEMPLATE_FORM} />
      </Wrapper>,
    )
    // eslint-disable-next-line @typescript-eslint/no-unnecessary-type-assertion
    const orgOption = screen.getByRole('option', { name: /organisatie|organization/i }) as HTMLOptionElement
    expect(orgOption.disabled).toBe(false)
    // eslint-disable-next-line @typescript-eslint/no-unnecessary-type-assertion
    const scopeSelect = screen.getByLabelText(/bereik|scope/i) as HTMLSelectElement
    expect(scopeSelect.value).toBe('org')
  })

  it('non-admin sees "Organisatie" disabled + default scope personal', () => {
    currentUserValue.isAdmin = false
    render(
      <Wrapper>
        <TemplateFormPage mode="new" initialForm={EMPTY_TEMPLATE_FORM} />
      </Wrapper>,
    )
    // eslint-disable-next-line @typescript-eslint/no-unnecessary-type-assertion
    const orgOption = screen.getByRole('option', { name: /organisatie|organization/i }) as HTMLOptionElement
    expect(orgOption.disabled).toBe(true)
    // eslint-disable-next-line @typescript-eslint/no-unnecessary-type-assertion
    const scopeSelect = screen.getByLabelText(/bereik|scope/i) as HTMLSelectElement
    expect(scopeSelect.value).toBe('personal')
  })
})

describe('TemplateFormPage — client-side validation', () => {
  it('empty name shows name-required error and does NOT call apiFetch', () => {
    currentUserValue.isAdmin = true
    render(
      <Wrapper>
        <TemplateFormPage mode="new" initialForm={EMPTY_TEMPLATE_FORM} />
      </Wrapper>,
    )
    fireEvent.change(screen.getByLabelText(/prompt/i), { target: { value: 'some text' } })
    fireEvent.click(screen.getByRole('button', { name: /save|opslaan/i }))

    expect(apiFetchMock).not.toHaveBeenCalled()
    const alert = screen.getByRole('alert').textContent?.toLowerCase() ?? ''
    expect(alert).toMatch(/naam|name/)
  })

  it('empty prompt shows prompt-required error and does NOT call apiFetch', () => {
    currentUserValue.isAdmin = true
    render(
      <Wrapper>
        <TemplateFormPage mode="new" initialForm={EMPTY_TEMPLATE_FORM} />
      </Wrapper>,
    )
    fireEvent.change(screen.getByLabelText(/^(naam|name)$/i), { target: { value: 'Some name' } })
    fireEvent.click(screen.getByRole('button', { name: /save|opslaan/i }))

    expect(apiFetchMock).not.toHaveBeenCalled()
    expect(screen.getByRole('alert').textContent?.toLowerCase()).toContain('prompt')
  })

  it('prompt_text > 8000 chars shows prompt-too-long error', () => {
    currentUserValue.isAdmin = true
    render(
      <Wrapper>
        <TemplateFormPage mode="new" initialForm={EMPTY_TEMPLATE_FORM} />
      </Wrapper>,
    )
    fireEvent.change(screen.getByLabelText(/^(naam|name)$/i), { target: { value: 'x' } })
    fireEvent.change(screen.getByLabelText(/prompt/i), { target: { value: 'a'.repeat(8001) } })
    fireEvent.click(screen.getByRole('button', { name: /save|opslaan/i }))

    expect(apiFetchMock).not.toHaveBeenCalled()
    expect(screen.getByRole('alert').textContent?.toLowerCase()).toContain('8000')
  })
})

describe('TemplateFormPage — char counter', () => {
  it('counter renders in gray when well below threshold', () => {
    currentUserValue.isAdmin = true
    render(
      <Wrapper>
        <TemplateFormPage mode="new" initialForm={{ ...EMPTY_TEMPLATE_FORM, prompt_text: 'a'.repeat(100) }} />
      </Wrapper>,
    )
    const counter = screen.getByTestId('prompt-char-count')
    expect(counter.className).toContain('text-gray-400')
  })

  it('counter switches to amber at 7800+ chars', () => {
    currentUserValue.isAdmin = true
    render(
      <Wrapper>
        <TemplateFormPage mode="new" initialForm={{ ...EMPTY_TEMPLATE_FORM, prompt_text: 'a'.repeat(7800) }} />
      </Wrapper>,
    )
    const counter = screen.getByTestId('prompt-char-count')
    expect(counter.className).toContain('text-amber-600')
  })

  it('counter switches to destructive at 8001+ chars', () => {
    currentUserValue.isAdmin = true
    render(
      <Wrapper>
        <TemplateFormPage mode="new" initialForm={{ ...EMPTY_TEMPLATE_FORM, prompt_text: 'a'.repeat(8001) }} />
      </Wrapper>,
    )
    const counter = screen.getByTestId('prompt-char-count')
    expect(counter.className).toContain('text-[var(--color-destructive)]')
  })
})
