import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@tanstack/react-router', () => ({
  createFileRoute: () => (options: object) => ({ options, useSearch: () => ({ authRequest: 'ar-1' }) }),
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
  useNavigate: () => vi.fn(),
}))
vi.mock('@/components/layout/AuthPageLayout', () => ({
  AuthPageLayout: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))
vi.mock('@/lib/locale', () => ({ useLocale: () => ({ locale: 'en' }) }))
vi.mock('@/paraglide/messages', () => ({
  login_hero_heading: () => '',
  login_hero_highlight: () => '',
  login_hero_body: () => '',
  login_hero_bullet_eu: () => '',
  login_hero_bullet_open: () => '',
  login_heading: () => 'Sign in',
  login_subheading: () => '',
  login_field_email: () => 'Email',
  login_field_password: () => 'Password',
  login_submit: () => 'Sign in',
  login_submit_loading: () => 'Signing in',
  login_or_continue_with: () => '',
  login_with_google: () => 'Google',
  login_with_microsoft: () => 'Microsoft',
  login_forgot_password: () => '',
  login_no_account: () => '',
  login_error_generic: () => 'error',
  error_connection: () => 'connection error',
  totp_heading: () => 'Two-step verification',
  totp_subheading: () => 'Enter the code from your authenticator app.',
  totp_subheading_email: () => 'We sent a code to your email address.',
  totp_field_code: () => 'Verification code',
  totp_submit: () => 'Verify',
  totp_submit_loading: () => 'Verifying',
  totp_error_generic: () => 'verification error',
  totp_back: () => 'Back',
}))

import { Route } from '../login'

const fetchMock = vi.fn()

function reply(status: number, body: unknown) {
  return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) })
}

describe('/login second factor', () => {
  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('asks for the mailed code after the password and sends it to finish the login', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url.endsWith('/api/auth/sso-complete')) return reply(401, {})
      if (url.endsWith('/api/auth/login')) return reply(200, { status: 'otp_email_required', temp_token: 'tmp-1' })
      return reply(200, { callback_url: 'http://localhost/cb' })
    })
    const Page = (Route as unknown as { options: { component: React.ComponentType } }).options.component
    render(<Page />)

    fireEvent.change(await screen.findByLabelText('Email'), { target: { value: 'user@example.com' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'secret' } })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText('We sent a code to your email address.')).toBeTruthy()
    const code = screen.getByLabelText('Verification code')
    const verify = screen.getByRole('button', { name: 'Verify' })
    fireEvent.change(code, { target: { value: '123456' } })
    expect((verify as HTMLButtonElement).disabled).toBe(true)
    fireEvent.change(code, { target: { value: '12345678' } })
    fireEvent.click(verify)

    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/api/auth/totp-login'))).toBe(true))
    const [, init] = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/api/auth/totp-login'))!
    expect(JSON.parse(init.body)).toEqual({ temp_token: 'tmp-1', code: '12345678', auth_request_id: 'ar-1' })
  })
})
