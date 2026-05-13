import { useEffect, useReducer } from 'react'
import { ArrowRight, Mail } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { apiFetch } from '@/lib/apiFetch'
import * as m from '@/paraglide/messages'
import { canResendEmailOtp, createInitialEmailOtpState, emailOtpReducer } from './-mfa-state'

export function EmailOTPSetup({
  email,
  onSuccess,
  onBack,
}: {
  email: string
  onSuccess: () => void
  onBack: () => void
}) {
  const [state, dispatch] = useReducer(emailOtpReducer, createInitialEmailOtpState())

  useEffect(() => {
    const id = setInterval(() => dispatch({ type: 'tick', now: Date.now() }), 1000)
    return () => clearInterval(id)
  }, [])

  const canResend = canResendEmailOtp(state)

  async function handleSend() {
    dispatch({ type: 'sendStart' })
    try {
      await apiFetch(`/api/auth/email-otp/setup`, { method: 'POST' })
      dispatch({ type: 'sendSuccess', resendAt: Date.now() + 30_000 })
    } catch {
      dispatch({ type: 'sendFail', error: m.error_connection() })
    }
  }

  async function handleResend() {
    dispatch({ type: 'resendStart' })
    try {
      await apiFetch(`/api/auth/email-otp/resend`, { method: 'POST' })
      dispatch({ type: 'resendSuccess', resendAt: Date.now() + 30_000 })
    } catch {
      dispatch({ type: 'resendFail', error: m.error_connection() })
    }
  }

  async function handleVerify(e: React.FormEvent) {
    e.preventDefault()
    dispatch({ type: 'verifyStart' })
    try {
      await apiFetch(`/api/auth/email-otp/confirm`, {
        method: 'POST',
        body: JSON.stringify({ code: state.code }),
      })
      dispatch({ type: 'verifyFinish' })
      onSuccess()
    } catch {
      dispatch({ type: 'verifyFail', error: m.error_connection() })
    }
  }

  return (
    <div className="space-y-6">
      {state.phase === 'send' ? (
        <>
          <div className="space-y-2">
            <h2 className="text-xl font-semibold text-gray-900">
              {m.setup_mfa_email_heading()}
            </h2>
            <p className="text-sm text-gray-400">
              {m.setup_mfa_email_body({ email })}
            </p>
          </div>
          <Button size="lg" className="w-full gap-3" onClick={handleSend} disabled={state.sending}>
            <Mail size={16} />
            {state.sending ? m.setup_mfa_email_sending() : m.setup_mfa_email_send_button()}
          </Button>
        </>
      ) : (
        <>
          <div className="space-y-2">
            <h2 className="text-xl font-semibold text-gray-900">
              {m.setup_mfa_email_code_heading()}
            </h2>
            <p className="text-sm text-gray-400">
              {m.setup_mfa_email_code_body()}
            </p>
          </div>

          <form onSubmit={handleVerify} className="space-y-4">
            <div className="space-y-1">
              <label htmlFor="email-otp-code" className="block text-sm font-medium text-gray-900">
                {m.setup_mfa_email_field_code()}
              </label>
              <input
                id="email-otp-code"
                type="text"
                inputMode="numeric"
                pattern="[0-9]*"
                maxLength={6}
                value={state.code}
                onChange={(e) => dispatch({ type: 'setCode', code: e.target.value })}
                required
                autoComplete="one-time-code"
                autoFocus
                className="w-full rounded-lg border border-gray-200 bg-[var(--color-background)] px-3 py-2 text-center font-mono text-base tracking-widest outline-none transition focus:ring-2 focus:ring-[var(--color-ring)]"
              />
            </div>

            {state.error && (
              <p className="rounded-lg bg-[var(--color-destructive-bg)] px-3 py-2 text-sm text-[var(--color-destructive-text)]">{state.error}</p>
            )}

            <Button
              type="submit"
              size="lg"
              className="w-full gap-3"
              disabled={state.verifying || state.code.length !== 6}
            >
              {state.verifying ? m.setup_mfa_email_verify_loading() : m.setup_mfa_email_verify_submit()}
              {!state.verifying && <ArrowRight size={16} />}
            </Button>

            <div className="text-center">
              {canResend ? (
                <button
                  type="button"
                  onClick={handleResend}
                  disabled={state.sending}
                  className="text-xs text-[var(--color-rl-accent-dark)] hover:underline"
                >
                  {m.setup_mfa_email_resend()}
                </button>
              ) : (
                <span className="text-xs text-gray-400">
                  {m.setup_mfa_email_resend()} ({Math.ceil(((state.resendAt ?? state.now) - state.now) / 1000)}s)
                </span>
              )}
            </div>
          </form>
        </>
      )}

      <button
        type="button"
        onClick={onBack}
        className="block text-xs text-[var(--color-rl-accent-dark)] hover:underline"
      >
        {m.setup_mfa_back()}
      </button>
    </div>
  )
}
