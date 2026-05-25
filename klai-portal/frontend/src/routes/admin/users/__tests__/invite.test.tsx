import type { JSX } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

const navigate = vi.fn()
vi.mock('@tanstack/react-router', async () => {
  const actual = await vi.importActual<typeof import('@tanstack/react-router')>(
    '@tanstack/react-router',
  )
  return {
    ...actual,
    useNavigate: () => navigate,
    createFileRoute: () => (cfg: unknown) => cfg,
  }
})

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

import { Route as RouteCfg } from '../invite'

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

describe('InviteUserPage', () => {
  it('renders a Select dropdown (no radio-cards) with 5 ladder options', async () => {
    apiFetchMock.mockResolvedValue({ name: 'Org', default_language: 'nl' })

    const Cfg = RouteCfg as unknown as { component: () => JSX.Element }
    render(
      <Wrapper>
        <Cfg.component />
      </Wrapper>,
    )

    await waitFor(() => {
      expect(screen.getByText('Profile')).toBeTruthy()
    })

    // Profile is a Select dropdown (rolled back from PR #317 ProfilePicker).
    // v0.5.0 removed the per-user seat selector entirely; account type is
    // derived from the Profile and shown as a read-only badge below.
    const select = screen.getByLabelText('Profile')
    expect(select.tagName.toLowerCase()).toBe('select')

    const options = Array.from(select.querySelectorAll('option'))
    expect(options).toHaveLength(5)
    expect(options.map((o) => o.value)).toEqual([
      'personal',
      'company',
      'kb_manager',
      'group_manager',
      'admin',
    ])
  })

  it('defaults the profile selection to "personal"', async () => {
    apiFetchMock.mockResolvedValue({ name: 'Org', default_language: 'nl' })

    const Cfg = RouteCfg as unknown as { component: () => JSX.Element }
    render(
      <Wrapper>
        <Cfg.component />
      </Wrapper>,
    )

    await waitFor(() => {
      expect(screen.getByLabelText('Profile')).toBeTruthy()
    })

    const select = screen.getByLabelText('Profile')
    expect((select as HTMLSelectElement).value).toBe('personal')
  })

  // SPEC-PORTAL-PRICING-PER-USER-001 v0.5.0 - account type is derived from
  // the chosen Profile and shown as a display-only badge. There is no admin
  // override; the server runs the same suggest_seat(role) derivation
  // regardless of what the FE sends.
  it('renders the derived account-type badge with chat tier for the default profile', async () => {
    apiFetchMock.mockResolvedValue({ name: 'Org', default_language: 'nl' })

    const Cfg = RouteCfg as unknown as { component: () => JSX.Element }
    render(
      <Wrapper>
        <Cfg.component />
      </Wrapper>,
    )

    await waitFor(() => {
      expect(screen.getByLabelText('Profile')).toBeTruthy()
    })

    // 'personal' role derives chat (€28/mo).
    const badge = screen.getByTestId('account-type-display')
    expect(badge.textContent).toContain('Klai Chat')
    expect(badge.textContent).toContain('€28/mo')
    // Hint copy is part of the read-only badge.
    expect(badge.textContent).toContain('Derived from the chosen Profile.')
    // No radio buttons in the badge container - it is display-only.
    expect(badge.querySelectorAll('input[type="radio"]')).toHaveLength(0)
    // A11y contract: the badge announces itself as a status region with
    // a polite live-region (so SR users hear the re-derivation when the
    // Profile changes), and is labelled by the standalone heading div
    // - NOT by an htmlFor= label, which would mis-imply a focusable
    // form control behind it.
    expect(badge.getAttribute('role')).toBe('status')
    expect(badge.getAttribute('aria-live')).toBe('polite')
    expect(badge.getAttribute('aria-labelledby')).toBe('account-type-label')
    const heading = document.getElementById('account-type-label')
    expect(heading).not.toBeNull()
    expect(heading?.textContent).toBe('Account type')
  })

  it('updates the account-type badge to knowledge (€68/mo) when role flips to kb_manager', async () => {
    apiFetchMock.mockResolvedValue({ name: 'Org', default_language: 'nl' })

    const Cfg = RouteCfg as unknown as { component: () => JSX.Element }
    render(
      <Wrapper>
        <Cfg.component />
      </Wrapper>,
    )

    await waitFor(() => {
      expect(screen.getByLabelText('Profile')).toBeTruthy()
    })

    // Initial state: personal -> chat.
    const badge = screen.getByTestId('account-type-display')
    expect(badge.textContent).toContain('Klai Chat')
    expect(badge.textContent).not.toContain('Klai Chat + Knowledge')
    expect(badge.textContent).toContain('€28/mo')

    // Flip Profile -> kb_manager (knowledge tier).
    const select = screen.getByLabelText('Profile')
    fireEvent.change(select, { target: { value: 'kb_manager' } })

    // Badge re-derives to knowledge (€68/mo) on the same render frame.
    expect(badge.textContent).toContain('Klai Chat + Knowledge')
    expect(badge.textContent).toContain('€68/mo')
  })

  it('does NOT send a seat_type field in the invite payload (server derives it)', async () => {
    apiFetchMock.mockResolvedValueOnce({ name: 'Org', default_language: 'nl' })
    apiFetchMock.mockResolvedValueOnce(undefined) // POST /api/admin/users/invite

    const Cfg = RouteCfg as unknown as { component: () => JSX.Element }
    render(
      <Wrapper>
        <Cfg.component />
      </Wrapper>,
    )

    await waitFor(() => {
      expect(screen.getByLabelText('Profile')).toBeTruthy()
    })

    // Fill the minimum required fields.
    fireEvent.change(screen.getByLabelText('First name'), {
      target: { value: 'Test' },
    })
    fireEvent.change(screen.getByLabelText('Last name'), {
      target: { value: 'User' },
    })
    fireEvent.change(screen.getByLabelText('Email'), {
      target: { value: 'test@example.com' },
    })

    // Submit.
    const submit = screen.getByText('Send')
    fireEvent.click(submit)

    await waitFor(() => {
      expect(apiFetchMock).toHaveBeenCalledTimes(2)
    })

    // Inspect the second call (the POST).
    const lastCall = apiFetchMock.mock.calls[1]
    expect(lastCall?.[0]).toBe('/api/admin/users/invite')
    const body = JSON.parse(lastCall?.[1]?.body ?? '{}')
    // v0.5.0 contract: the FE does NOT send seat_type. Server derives it
    // from role via suggest_seat() in seats.py - preventing client tamper.
    expect(body).not.toHaveProperty('seat_type')
    // Required fields ARE present.
    expect(body).toMatchObject({
      first_name: 'Test',
      last_name: 'User',
      email: 'test@example.com',
      role: 'personal',
    })
  })
})
