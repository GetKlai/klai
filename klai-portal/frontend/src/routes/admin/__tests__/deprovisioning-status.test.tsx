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

vi.mock('@/lib/logger', () => ({
  deprovisionLogger: { info: vi.fn(), error: vi.fn(), warn: vi.fn(), debug: vi.fn() },
}))

vi.mock('@/paraglide/messages', () => ({
  deprovisioning_status_heading: () => 'Werkruimte wordt verwijderd...',
  deprovisioning_status_subtitle: () => 'Dit duurt ongeveer 30 seconden.',
  deprovisioning_status_timeout: () => 'Dit duurt langer dan verwacht.',
  deprovisioning_status_failed_heading: () => 'Verwijderen mislukt',
  deprovisioning_status_failed_step: ({ step }: { step: string }) => `Stap: ${step}`,
  deprovisioning_status_failed_support: () => 'Neem contact op met support',
  error_generic: () => 'Er is iets misgegaan.',
  provisioning_error_retry: () => 'Opnieuw proberen',
}))

// Import AFTER all mocks are set up
import { DeprovisioningStatusPage } from '../deprovisioning-status'

// Note: DeprovisioningStatusPage is the named export we need from the file
// The actual route file exports via createFileRoute, so we need to check how it's structured.

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

describe('DeprovisioningStatusPage', () => {
  it('shows spinner and heading when status is deprovisioning', async () => {
    apiFetchMock.mockResolvedValue({ status: 'deprovisioning' })

    render(
      <Wrapper>
        <DeprovisioningStatusPage />
      </Wrapper>,
    )

    await waitFor(() => {
      expect(screen.getByText('Werkruimte wordt verwijderd...')).toBeTruthy()
      expect(screen.getByText('Dit duurt ongeveer 30 seconden.')).toBeTruthy()
    })
  })

  it('navigates to /tenant-deleted when status is gone (404 → gone)', async () => {
    apiFetchMock.mockResolvedValue({ status: 'gone' })

    render(
      <Wrapper>
        <DeprovisioningStatusPage />
      </Wrapper>,
    )

    await waitFor(() => {
      expect(navigate).toHaveBeenCalledWith({ to: '/tenant-deleted' })
    })
  })

  it('navigates back to /admin when status is ready', async () => {
    apiFetchMock.mockResolvedValue({ status: 'ready' })

    render(
      <Wrapper>
        <DeprovisioningStatusPage />
      </Wrapper>,
    )

    await waitFor(() => {
      expect(navigate).toHaveBeenCalledWith({ to: '/admin' })
    })
  })

  it('shows failed view with step info when status is failed_deprovisioning', async () => {
    apiFetchMock.mockResolvedValue({
      status: 'failed_deprovisioning',
      last_failure: {
        step: '_delete_stripe_customer',
        error: 'Stripe timeout',
        attempt: 3,
        failed_at: '2026-05-03T12:00:00Z',
      },
    })

    render(
      <Wrapper>
        <DeprovisioningStatusPage />
      </Wrapper>,
    )

    await waitFor(() => {
      expect(screen.getByText('Verwijderen mislukt')).toBeTruthy()
      expect(screen.getByText('Stap: _delete_stripe_customer')).toBeTruthy()
      expect(screen.getByText('Neem contact op met support')).toBeTruthy()
    })
  })

  it('handles 404 from apiFetch as gone status (org already deleted)', async () => {
    const { ApiError } = await vi.importActual<typeof import('@/lib/apiFetch')>('@/lib/apiFetch')
    apiFetchMock.mockRejectedValue(new ApiError(404, 'Not Found'))

    render(
      <Wrapper>
        <DeprovisioningStatusPage />
      </Wrapper>,
    )

    await waitFor(() => {
      expect(navigate).toHaveBeenCalledWith({ to: '/tenant-deleted' })
    })
  })
})
