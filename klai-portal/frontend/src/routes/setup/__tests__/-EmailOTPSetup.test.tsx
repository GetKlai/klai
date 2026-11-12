import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '@/lib/apiFetch'
import { EmailOTPSetup } from '../_components/-EmailOTPSetup'

vi.mock('@/lib/apiFetch', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/apiFetch')>()),
  apiFetch: vi.fn(),
}))

vi.mock('@/paraglide/messages', () => ({
  setup_mfa_email_heading: () => 'Email verification',
  setup_mfa_email_body: ({ email }: { email: string }) => `Send a code to ${email}`,
  setup_mfa_email_sending: () => 'Sending',
  setup_mfa_email_send_button: () => 'Send code',
  setup_mfa_email_code_heading: () => 'Enter code',
  setup_mfa_email_code_body: () => 'Check your inbox',
  setup_mfa_email_field_code: () => 'Code',
  setup_mfa_email_verify_loading: () => 'Verifying',
  setup_mfa_email_verify_submit: () => 'Verify',
  setup_mfa_email_resend: () => 'Resend',
  setup_mfa_back: () => 'Back',
  error_connection: () => 'Connection error',
  setup_mfa_email_error_invalid: () => 'Incorrect code',
  setup_mfa_email_error_expired: () => 'Code expired',
}))

const apiFetchMock = vi.mocked(apiFetch)

describe('EmailOTPSetup', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
  })

  it('keeps send-step failures non-visible to preserve the existing setup behavior', async () => {
    apiFetchMock.mockRejectedValueOnce(new Error('network down'))

    render(<EmailOTPSetup email="user@voys.test" onSuccess={vi.fn()} onBack={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: /send code/i }))

    await waitFor(() => {
      expect(apiFetchMock).toHaveBeenCalledWith('/api/auth/email-otp/setup', { method: 'POST' })
    })
    expect(screen.queryByText('Connection error')).toBeNull()
    expect(screen.getByRole<HTMLButtonElement>('button', { name: /send code/i }).disabled).toBe(false)
  })

  it('submits the 8-digit code Zitadel mails and shows a verification failure', async () => {
    apiFetchMock
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new Error('invalid code'))

    render(<EmailOTPSetup email="user@voys.test" onSuccess={vi.fn()} onBack={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: /send code/i }))
    await screen.findByRole('heading', { name: /enter code/i })

    fireEvent.change(screen.getByLabelText(/code/i), { target: { value: '12345678' } })
    fireEvent.click(screen.getByRole('button', { name: /verify/i }))

    await waitFor(() => {
      expect(apiFetchMock).toHaveBeenLastCalledWith('/api/auth/email-otp/confirm', {
        method: 'POST',
        body: JSON.stringify({ code: '12345678' }),
      })
    })
    expect(await screen.findByText('Connection error')).not.toBeNull()
  })

  it.each([
    [400, 'Incorrect code'],
    [410, 'Code expired'],
  ])('tells a %s answer to the code apart: %s', async (status, message) => {
    apiFetchMock.mockResolvedValueOnce(undefined).mockRejectedValueOnce(new ApiError(status, 'x'))

    render(<EmailOTPSetup email="user@example.com" onSuccess={vi.fn()} onBack={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /send code/i }))
    await screen.findByRole('heading', { name: /enter code/i })
    fireEvent.change(screen.getByLabelText(/code/i), { target: { value: '12345678' } })
    fireEvent.click(screen.getByRole('button', { name: /verify/i }))

    expect(await screen.findByText(message)).not.toBeNull()
  })
})
