import { useEffect, useState } from 'react'
import { ArrowRight, Mail } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { apiFetch } from '@/lib/apiFetch'
import * as m from '@/paraglide/messages'

interface EmailOTPSetupProps {
  token: string
  email: string
  onSuccess: () => void
  onBack: () => void
}

export function EmailOTPSetup({ token, email, onSuccess, onBack }: EmailOTPSetupProps) {
  const [phase, setPhase] = useState<'send' | 'verify'>('send')
  const [sending, setSending] = useState(false)
  const [code, setCode] = useState('')
  const [verifying, setVerifying] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [resendAt, setResendAt] = useState<number | null>(null)
  const [now, setNow] = useState(Date.now())

  // Tick every second to update resend countdown
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])

  const canResend = resendAt === null || now >= resendAt

  async function handleSend() {
    setError(null)
    setSending(true)
    try {
      await apiFetch(`/api/auth/email-otp/setup`, token, { method: 'POST' })
      setPhase('verify')
      setResendAt(Date.now() + 30_000)
    } catch {
      setError(m.error_connection())
    } finally {
      setSending(false)
    }
  }

  async function handleResend() {
    setError(null)
    setSending(true)
    try {
      await apiFetch(`/api/auth/email-otp/resend`, token, { method: 'POST' })
      setResendAt(Date.now() + 30_000)
    } catch {
      setError(m.error_connection())
    } finally {
      setSending(false)
    }
  }

  async function handleVerify(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setVerifying(true)
    try {
      await apiFetch(`/api/auth/email-otp/confirm`, token, {
        method: 'POST',
        body: JSON.stringify({ code }),
      })
      onSuccess()
    } catch {
      setError(m.error_connection())
    } finally {
      setVerifying(false)
    }
  }

  return (
    <div className="space-y-6">
      {phase === 'send' ? (
        <>
          <div className="space-y-2">
            <h2 className="text-2xl font-bold text-[var(--color-foreground)]">
              {m.setup_mfa_email_heading()}
            </h2>
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {m.setup_mfa_email_body({ email })}
            </p>
          </div>
          <Button size="lg" className="w-full gap-3" onClick={handleSend} disabled={sending}>
            <Mail size={16} />
            {sending ? m.setup_mfa_email_sending() : m.setup_mfa_email_send_button()}
          </Button>
        </>
      ) : (
        <>
          <div className="space-y-2">
            <h2 className="text-2xl font-bold text-[var(--color-foreground)]">
              {m.setup_mfa_email_code_heading()}
            </h2>
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {m.setup_mfa_email_code_body()}
            </p>
          </div>

          <form onSubmit={handleVerify} className="space-y-4">
            <div className="space-y-1">
              <label htmlFor="email-otp-code" className="block text-sm font-medium text-[var(--color-foreground)]">
                {m.setup_mfa_email_field_code()}
              </label>
              <input
                id="email-otp-code"
                type="text"
                inputMode="numeric"
                pattern="[0-9]*"
                maxLength={6}
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
                required
                autoComplete="one-time-code"
                autoFocus
                className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-3 py-2 text-center font-mono text-lg tracking-widest outline-none transition focus:ring-2 focus:ring-[var(--color-ring)]"
              />
            </div>

            {error && (
              <p className="rounded-lg bg-[var(--color-destructive-bg)] px-3 py-2 text-sm text-[var(--color-destructive-text)]">{error}</p>
            )}

            <Button
              type="submit"
              size="lg"
              className="w-full gap-3"
              disabled={verifying || code.length !== 6}
            >
              {verifying ? m.setup_mfa_email_verify_loading() : m.setup_mfa_email_verify_submit()}
              {!verifying && <ArrowRight size={16} />}
            </Button>

            <div className="text-center">
              {canResend ? (
                <button
                  type="button"
                  onClick={handleResend}
                  disabled={sending}
                  className="text-xs text-[var(--color-rl-accent-dark)] hover:underline"
                >
                  {m.setup_mfa_email_resend()}
                </button>
              ) : (
                <span className="text-xs text-[var(--color-muted-foreground)]">
                  {m.setup_mfa_email_resend()} ({Math.ceil((resendAt - now) / 1000)}s)
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
