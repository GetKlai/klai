import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ButtonHTMLAttributes, JSX, ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

let searchParams = { userID: 'uid-1', code: 'expired-code' }

vi.mock('@tanstack/react-router', () => ({
  createFileRoute: () => (cfg: Record<string, unknown>) => ({
    ...cfg,
    useSearch: () => searchParams,
  }),
}))

vi.mock('@/components/layout/AuthPageLayout', () => ({
  AuthPageLayout: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}))

vi.mock('@/components/ui/button', () => ({
  Button: ({
    children,
    asChild: _asChild,
    size: _size,
    ...props
  }: ButtonHTMLAttributes<HTMLButtonElement> & {
    asChild?: boolean
    size?: string
  }) => <button {...props}>{children}</button>,
}))

vi.mock('@/lib/auth', () => ({
  readCsrfCookie: () => 'csrf-token',
}))

vi.mock('@/paraglide/messages', () => ({
  error_connection: () => 'Connection error',
  set_back: () => 'Back to login',
  set_done_body: () => 'You can now log in.',
  set_done_continue: () => 'Log in',
  set_done_heading: () => 'Password set',
  set_error_min_length: () => 'Password must be at least 12 characters and contain one symbol',
  set_error_mismatch: () => 'Passwords do not match',
  set_error_server: () => 'Failed to set password',
  set_field_confirm: () => 'Confirm password',
  set_field_password: () => 'Password',
  set_heading: () => 'Set password',
  set_hero_body: () => 'Choose a password.',
  set_hero_heading: () => 'Set your password',
  set_invalid_link: () => 'This link is invalid or has expired.',
  set_invalid_link_back: () => 'Back to login',
  set_invalid_link_body: () => 'Request a new reset link and use the latest email.',
  set_invalid_link_heading: () => 'This link has expired',
  set_invalid_link_request_new: () => 'Request a new reset link',
  set_submit: () => 'Save',
  set_submit_loading: () => 'Saving...',
  set_subheading: () => 'Enter your new password.',
}))

import { Route } from '../set'

function renderPasswordSetPage() {
  const Cfg = Route as unknown as { component: () => JSX.Element }
  render(<Cfg.component />)
}

describe('PasswordSetPage', () => {
  beforeEach(() => {
    searchParams = { userID: 'uid-1', code: 'expired-code' }
    vi.restoreAllMocks()
  })

  it('clears any existing BFF session when the reset link is expired', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ detail: 'Link has expired or is invalid, request a new reset link' }),
          {
            status: 400,
            headers: { 'Content-Type': 'application/json' },
          },
        ),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }))

    renderPasswordSetPage()

    fireEvent.change(screen.getByLabelText('Password'), {
      target: { value: 'CorrectHorseBatteryStaple!' },
    })
    fireEvent.change(screen.getByLabelText('Confirm password'), {
      target: { value: 'CorrectHorseBatteryStaple!' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByText(/Link has expired or is invalid/)).toBeTruthy()
    expect(screen.getByText('This link has expired')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Request a new reset link' }).getAttribute('href')).toBe(
      '/password/forgot',
    )

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/auth/bff/logout',
      expect.objectContaining({
        method: 'POST',
        credentials: 'include',
        headers: { 'X-CSRF-Token': 'csrf-token' },
      }),
    )
  })

  it('does not clear the session for unrelated validation failures', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'Password does not meet policy' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    renderPasswordSetPage()

    fireEvent.change(screen.getByLabelText('Password'), {
      target: { value: 'CorrectHorseBatteryStaple!' },
    })
    fireEvent.change(screen.getByLabelText('Confirm password'), {
      target: { value: 'CorrectHorseBatteryStaple!' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByText('Password does not meet policy')).toBeTruthy()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
