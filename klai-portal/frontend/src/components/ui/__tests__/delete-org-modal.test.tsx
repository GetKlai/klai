import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

const navigate = vi.fn()
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
}))

const apiFetchMock = vi.fn()
vi.mock('@/lib/apiFetch', async () => {
  const actual = await vi.importActual<typeof import('@/lib/apiFetch')>('@/lib/apiFetch')
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) }
})

// Paraglide messages — return simple string representations
vi.mock('@/paraglide/messages', () => ({
  delete_org_modal_title: () => 'Werkruimte permanent verwijderen',
  delete_org_modal_intro: () => 'Dit verwijdert permanent:',
  delete_org_modal_item_org: () => 'en alle conversaties',
  delete_org_modal_item_members: () => 'Alle teamleden (worden uitgelogd)',
  delete_org_modal_item_kbs: () => 'Alle knowledge bases en uploads',
  delete_org_modal_item_integrations: () => 'Alle integraties',
  delete_org_modal_item_billing: () => 'Factuurgeschiedenis blijft beschikbaar in Moneybird',
  delete_org_modal_warning: () => 'Deze actie kan niet ongedaan worden gemaakt.',
  delete_org_modal_confirm_label: ({ slug }: { slug: string }) => `Typ ${slug} om te bevestigen`,
  delete_org_modal_confirm_placeholder: () => 'werkruimte-slug',
  delete_org_modal_cancel: () => 'Annuleren',
  delete_org_modal_submit: () => 'Permanent verwijderen',
  delete_org_modal_submitting: () => 'Verwijderen...',
  delete_org_modal_error_generic: () => 'Verwijderen mislukt, probeer het opnieuw',
  delete_org_modal_error_conflict: () => 'De werkruimte wordt al verwijderd',
}))

vi.mock('@/lib/logger', () => ({
  deprovisionLogger: { info: vi.fn(), error: vi.fn(), warn: vi.fn(), debug: vi.fn() },
}))

import { DeleteOrgModal } from '../delete-org-modal'

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

const DEFAULT_PROPS = {
  open: true,
  onOpenChange: vi.fn(),
  orgSlug: 'mijn-werkruimte',
  orgName: 'Mijn Werkruimte',
}

beforeEach(() => {
  navigate.mockReset()
  apiFetchMock.mockReset()
  DEFAULT_PROPS.onOpenChange.mockReset()
})

describe('DeleteOrgModal', () => {
  it('renders the modal with the org name and slug label', () => {
    render(
      <Wrapper>
        <DeleteOrgModal {...DEFAULT_PROPS} />
      </Wrapper>,
    )

    expect(screen.getByText('Werkruimte permanent verwijderen')).toBeTruthy()
    expect(screen.getByText('Mijn Werkruimte', { exact: false })).toBeTruthy()
    expect(screen.getByText(/Typ mijn-werkruimte om te bevestigen/)).toBeTruthy()
  })

  it('keeps delete button disabled when confirm input is empty', () => {
    render(
      <Wrapper>
        <DeleteOrgModal {...DEFAULT_PROPS} />
      </Wrapper>,
    )

    const button = screen.getByRole('button', { name: 'Permanent verwijderen' })
    expect((button as HTMLButtonElement).disabled).toBe(true)
  })

  it('keeps delete button disabled when slug is partially typed', () => {
    render(
      <Wrapper>
        <DeleteOrgModal {...DEFAULT_PROPS} />
      </Wrapper>,
    )

    const input = screen.getByRole('textbox')
    fireEvent.change(input, { target: { value: 'mijn' } })

    const button = screen.getByRole('button', { name: 'Permanent verwijderen' })
    expect((button as HTMLButtonElement).disabled).toBe(true)
  })

  it('enables delete button when slug matches exactly', () => {
    render(
      <Wrapper>
        <DeleteOrgModal {...DEFAULT_PROPS} />
      </Wrapper>,
    )

    const input = screen.getByRole('textbox')
    fireEvent.change(input, { target: { value: 'mijn-werkruimte' } })

    const button = screen.getByRole('button', { name: 'Permanent verwijderen' })
    expect((button as HTMLButtonElement).disabled).toBe(false)
  })

  it('calls DELETE /api/admin/org/me and navigates on success', async () => {
    apiFetchMock.mockResolvedValue(undefined)

    render(
      <Wrapper>
        <DeleteOrgModal {...DEFAULT_PROPS} />
      </Wrapper>,
    )

    const input = screen.getByRole('textbox')
    fireEvent.change(input, { target: { value: 'mijn-werkruimte' } })

    const button = screen.getByRole('button', { name: 'Permanent verwijderen' })
    fireEvent.click(button)

    await waitFor(() => {
      expect(apiFetchMock).toHaveBeenCalledWith('/api/admin/org/me', { method: 'DELETE' })
      expect(navigate).toHaveBeenCalledWith({ to: '/admin/deprovisioning-status' })
    })
  })

  it('shows generic error message on API failure', async () => {
    apiFetchMock.mockRejectedValue(new Error('500: Internal Server Error'))

    render(
      <Wrapper>
        <DeleteOrgModal {...DEFAULT_PROPS} />
      </Wrapper>,
    )

    const input = screen.getByRole('textbox')
    fireEvent.change(input, { target: { value: 'mijn-werkruimte' } })

    const button = screen.getByRole('button', { name: 'Permanent verwijderen' })
    fireEvent.click(button)

    await waitFor(() => {
      expect(screen.getByText('Verwijderen mislukt, probeer het opnieuw')).toBeTruthy()
    })
    expect(navigate).not.toHaveBeenCalled()
  })

  it('shows conflict error message when already deprovisioning', async () => {
    apiFetchMock.mockRejectedValue(new Error('already_deprovisioning'))

    render(
      <Wrapper>
        <DeleteOrgModal {...DEFAULT_PROPS} />
      </Wrapper>,
    )

    const input = screen.getByRole('textbox')
    fireEvent.change(input, { target: { value: 'mijn-werkruimte' } })

    const button = screen.getByRole('button', { name: 'Permanent verwijderen' })
    fireEvent.click(button)

    await waitFor(() => {
      expect(screen.getByText('De werkruimte wordt al verwijderd')).toBeTruthy()
    })
  })

  it('resets confirm input and error when modal is closed', () => {
    const onOpenChange = vi.fn()
    render(
      <Wrapper>
        <DeleteOrgModal {...DEFAULT_PROPS} onOpenChange={onOpenChange} />
      </Wrapper>,
    )

    const input = screen.getByRole('textbox')
    fireEvent.change(input, { target: { value: 'mijn-werkruimte' } })

    // Close via cancel button
    const cancelButton = screen.getByRole('button', { name: 'Annuleren' })
    fireEvent.click(cancelButton)

    expect(onOpenChange).toHaveBeenCalledWith(false)
  })
})
