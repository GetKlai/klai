import { createLazyFileRoute } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { useAuth } from 'react-oidc-context'
import QRCode from 'react-qr-code'
import { ArrowRight, Fingerprint, Mail, Shield, ShieldCheck } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { AuthPageLayout } from '@/components/layout/AuthPageLayout'
import { apiFetch } from '@/lib/apiFetch'
import * as m from '@/paraglide/messages'
import { useLocale } from '@/lib/locale'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { authLogger } from '@/lib/logger'

export const Route = createLazyFileRoute('/setup/mfa')({
  component: SetupMFAPage,
})

type Method = 'passkey' | 'email' | 'totp'
type Step = 'pick' | 'setup' | 'done'

// ── Helpers ─────────────────────────────────────────────────────────────────

function base64urlToBuffer(base64url: string): ArrayBuffer {
  const base64 = base64url.replace(/-/g, '+').replace(/_/g, '/')
  const binary = atob(base64)
  const buffer = new ArrayBuffer(binary.length)
  const view = new Uint8Array(buffer)
  for (let i = 0; i < binary.length; i++) view[i] = binary.charCodeAt(i)
  return buffer
}

function bufferToBase64url(buffer: ArrayBuffer): string {
  const view = new Uint8Array(buffer)
  let binary = ''
  for (const byte of view) binary += String.fromCharCode(byte)
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '')
}

// Recursively convert ArrayBuffer values in a WebAuthn object to base64url strings
// so they can be JSON-serialised and sent to the backend.
function encodeCredential(credential: PublicKeyCredential): object {
  const resp = credential.response as AuthenticatorAttestationResponse
  return {
    id: credential.id,
    rawId: bufferToBase64url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: bufferToBase64url(resp.clientDataJSON),
      attestationObject: bufferToBase64url(resp.attestationObject),
    },
  }
}

// ── Sub-components ───────────────────────────────────────────────────────────

function MethodCard({
  icon,
  title,
  description,
  recommended,
  selected,
  onClick,
}: {
  icon: React.ReactNode
  title: string
  description: string
  recommended?: boolean
  selected: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full rounded-xl border-2 p-4 text-left transition-all
        ${selected
          ? 'border-[var(--color-rl-accent)] bg-[var(--color-rl-accent)]/5'
          : 'border-[var(--color-border)] bg-[var(--color-background)] hover:border-[var(--color-rl-accent)]/50'
        }`}
    >
      <div className="flex items-start gap-3">
        <div className={`mt-0.5 shrink-0 ${selected ? 'text-[var(--color-rl-accent)]' : 'text-[var(--color-rl-cream)]'}`}>
          {icon}
        </div>
        <div className="flex-1 space-y-0.5">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-[var(--color-foreground)]">{title}</span>
            {recommended && (
              <span className="rounded-full bg-[var(--color-rl-accent)] px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-white">
                {m.setup_mfa_badge_recommended()}
              </span>
            )}
          </div>
          <p className="text-xs text-[var(--color-muted-foreground)]">{description}</p>
        </div>
        {selected && (
          <div className="mt-0.5 shrink-0 text-[var(--color-rl-accent)]">
            <ShieldCheck size={16} />
          </div>
        )}
      </div>
    </button>
  )
}

// ── Passkey setup step ───────────────────────────────────────────────────────

function PasskeySetup({
  token,
  onSuccess,
  onBack,
}: {
  token: string
  onSuccess: () => void
  onBack: () => void
}) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const supportsPasskeys = typeof window !== 'undefined' && !!window.PublicKeyCredential

  async function handleSetup() {
    setError(null)
    setLoading(true)
    try {
      // 1. Get registration options from backend
      const { passkey_id, options } = await apiFetch<{ passkey_id: string; options: { publicKey: PublicKeyCredentialCreationOptions & { challenge: string; user: { id: string } & PublicKeyCredentialUserEntity; excludeCredentials?: { id: string; type: string }[] } } }>(`/api/auth/passkey/setup`, token, {
        method: 'POST',
      })
      // Zitadel wraps WebAuthn options under publicKeyCredentialCreationOptions.publicKey
      const pk = options.publicKey

      // 2. Decode binary fields from base64url (Zitadel encodes them as base64url strings)
      const publicKey: PublicKeyCredentialCreationOptions = {
        ...pk,
        challenge: base64urlToBuffer(pk.challenge),
        user: {
          ...pk.user,
          id: base64urlToBuffer(pk.user.id),
        },
        excludeCredentials: pk.excludeCredentials?.map((c: { id: string; type: string }) => ({
          ...c,
          id: base64urlToBuffer(c.id),
          type: c.type as 'public-key',
        })) ?? [],
      }

      // 3. Trigger browser native dialog
      const credential = await navigator.credentials.create({ publicKey }) as PublicKeyCredential | null
      if (!credential) throw new Error('cancelled')

      // 4. Send credential to backend for verification
      await apiFetch(`/api/auth/passkey/confirm`, token, {
        method: 'POST',
        body: JSON.stringify({
          passkey_id,
          public_key_credential: encodeCredential(credential),
        }),
      })

      onSuccess()
    } catch (err) {
      // NotAllowedError = user dismissed the browser dialog — not a real error
      if (err instanceof DOMException && err.name === 'NotAllowedError') {
        setError(null)
      } else {
        authLogger.error('Passkey setup failed', err)
        setError(m.setup_mfa_passkey_error_failed())
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h2 className="text-xl font-semibold text-[var(--color-foreground)]">
          {m.setup_mfa_passkey_heading()}
        </h2>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.setup_mfa_passkey_body()}
        </p>
      </div>

      {!supportsPasskeys ? (
        <p className="rounded-lg bg-[var(--color-warning-bg)] px-3 py-2 text-sm text-[var(--color-warning-text)]">
          {m.setup_mfa_passkey_error_unsupported()}
        </p>
      ) : (
        <>
          {error && (
            <p className="rounded-lg bg-[var(--color-destructive-bg)] px-3 py-2 text-sm text-[var(--color-destructive-text)]">{error}</p>
          )}
          <Button
            size="lg"
            className="w-full gap-3"
            onClick={handleSetup}
            disabled={loading}
          >
            <Fingerprint size={16} />
            {loading ? m.setup_mfa_passkey_loading() : m.setup_mfa_passkey_button()}
          </Button>
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

// ── Email OTP setup step ─────────────────────────────────────────────────────

function EmailOTPSetup({
  token,
  email,
  onSuccess,
  onBack,
}: {
  token: string
  email: string
  onSuccess: () => void
  onBack: () => void
}) {
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
            <h2 className="text-xl font-semibold text-[var(--color-foreground)]">
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
            <h2 className="text-xl font-semibold text-[var(--color-foreground)]">
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
                className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-3 py-2 text-center font-mono text-base tracking-widest outline-none transition focus:ring-2 focus:ring-[var(--color-ring)]"
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

// ── TOTP setup step ──────────────────────────────────────────────────────────

function TOTPSetup({
  token,
  onSuccess,
  onBack,
}: {
  token: string
  onSuccess: () => void
  onBack: () => void
}) {
  const [uri, setUri] = useState<string | null>(null)
  const [secret, setSecret] = useState<string | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [retryCount, setRetryCount] = useState(0)

  useEffect(() => {
    setUri(null)
    setSecret(null)
    setLoadError(null)
    apiFetch<{ uri: string; secret: string }>(`/api/auth/totp/setup`, token, { method: 'POST' })
      .then((data) => {
        setUri(data.uri)
        setSecret(data.secret)
      })
      .catch((err) => { authLogger.warn('TOTP setup QR fetch failed', err); setLoadError(m.error_connection()) })
  }, [token, retryCount])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      await apiFetch(`/api/auth/totp/confirm`, token, {
        method: 'POST',
        body: JSON.stringify({ code }),
      })
      onSuccess()
    } catch {
      setError(m.error_connection())
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h2 className="text-xl font-semibold text-[var(--color-foreground)]">
          {m.setup_2fa_heading()}
        </h2>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.setup_2fa_subheading()}
        </p>
      </div>

      {loadError ? (
        <div className="space-y-3 text-center">
          <p className="text-sm text-[var(--color-destructive-text)]">{loadError}</p>
          <button
            onClick={() => setRetryCount((c) => c + 1)}
            className="text-xs text-[var(--color-rl-accent-dark)] hover:underline"
          >
            {m.setup_2fa_retry()}
          </button>
        </div>
      ) : (
        <>
          <div className="flex flex-col items-center gap-4">
            {uri ? (
              <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-background)] p-4">
                <QRCode value={uri} size={180} />
              </div>
            ) : (
              <div className="flex h-[212px] w-[212px] items-center justify-center rounded-xl border border-[var(--color-border)] bg-[var(--color-background)]">
                <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-rl-accent)] border-t-transparent" />
              </div>
            )}
            {secret && (
              <details className="w-full text-center">
                <summary className="cursor-pointer select-none text-xs text-[var(--color-rl-accent-dark)] hover:underline">
                  {m.setup_2fa_manual_label()}
                </summary>
                <p className="mt-2 break-all rounded-lg bg-[var(--color-border)] px-3 py-2 font-mono text-xs tracking-widest">
                  {secret}
                </p>
              </details>
            )}
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1">
              <label htmlFor="totp-code" className="block text-sm font-medium text-[var(--color-foreground)]">
                {m.setup_2fa_field_code()}
              </label>
              <input
                id="totp-code"
                type="text"
                inputMode="numeric"
                pattern="[0-9]*"
                maxLength={6}
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
                required
                autoComplete="one-time-code"
                autoFocus
                className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-3 py-2 text-center font-mono text-base tracking-widest outline-none transition focus:ring-2 focus:ring-[var(--color-ring)]"
              />
            </div>
            {error && (
              <p className="rounded-lg bg-[var(--color-destructive-bg)] px-3 py-2 text-sm text-[var(--color-destructive-text)]">{error}</p>
            )}
            <Button
              type="submit"
              size="lg"
              className="w-full gap-3"
              disabled={loading || code.length !== 6 || !uri}
            >
              {loading ? m.setup_2fa_submit_loading() : m.setup_2fa_submit()}
              {!loading && <ArrowRight size={16} />}
            </Button>
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

// ── Main page ────────────────────────────────────────────────────────────────

function SetupMFAPage() {
  useLocale()
  const auth = useAuth()

  const [selectedMethod, setSelectedMethod] = useState<Method | null>(null)
  const [step, setStep] = useState<Step>('pick')

  const { user: currentUser } = useCurrentUser()
  const mfaPolicy = currentUser?.mfa_policy ?? 'optional'
  const isRequired = mfaPolicy === 'required'

  const token = auth.user?.access_token ?? ''
  const email = auth.user?.profile?.email as string ?? ''

  function handleMethodChosen() {
    if (!selectedMethod) return
    setStep('setup')
  }

  function handleSuccess() {
    setStep('done')
    setTimeout(() => {
      window.location.replace(currentUser?.isAdmin ? '/admin' : '/app')
    }, 1500)
  }

  function handleBack() {
    setStep('pick')
    setSelectedMethod(null)
  }

  function handleSkip() {
    window.location.replace(currentUser?.isAdmin ? '/admin' : '/app')
  }

  if (!auth.isAuthenticated) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--color-background)]">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-rl-accent)] border-t-transparent" />
      </div>
    )
  }

  const leftContent = (
    <>
      <h1 className="text-2xl font-semibold leading-tight">
        {m.setup_mfa_hero_heading()}
        <br />
        <span className="text-[var(--color-rl-accent)]">{m.setup_mfa_hero_highlight()}</span>
      </h1>
      <p className="text-base leading-relaxed text-[var(--color-rl-cream)]">
        {m.setup_mfa_hero_body()}
      </p>
      <div className="flex items-center gap-3 text-sm text-[var(--color-rl-cream)]">
        <Shield size={16} className="shrink-0 text-[var(--color-rl-accent)]" />
        {m.setup_mfa_hero_methods()}
      </div>
    </>
  )

  return (
    <AuthPageLayout leftContent={leftContent}>
      {/* ── Done state ── */}
      {step === 'done' && (
        <div className="space-y-4 text-center">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-[var(--color-foreground)]">
            <ShieldCheck size={22} className="text-[var(--color-rl-cream)]" />
          </div>
          <p className="text-xl font-semibold text-[var(--color-foreground)]">
            {m.setup_mfa_done_heading()}
          </p>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {m.setup_mfa_done_body()}
          </p>
        </div>
      )}

      {/* ── Method picker ── */}
      {step === 'pick' && (
        <div className="space-y-5">
          <div className="space-y-1">
            <h2 className="text-xl font-semibold text-[var(--color-foreground)]">
              {m.setup_mfa_heading()}
            </h2>
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {m.setup_mfa_subheading()}
            </p>
          </div>

          <div className="space-y-3">
            <MethodCard
              icon={<Fingerprint size={20} />}
              title={m.setup_mfa_passkey_title()}
              description={m.setup_mfa_passkey_description()}
              recommended
              selected={selectedMethod === 'passkey'}
              onClick={() => setSelectedMethod('passkey')}
            />
            <MethodCard
              icon={<Mail size={20} />}
              title={m.setup_mfa_email_title()}
              description={m.setup_mfa_email_description()}
              selected={selectedMethod === 'email'}
              onClick={() => setSelectedMethod('email')}
            />
            <MethodCard
              icon={<Shield size={20} />}
              title={m.setup_mfa_totp_title()}
              description={m.setup_mfa_totp_description()}
              selected={selectedMethod === 'totp'}
              onClick={() => setSelectedMethod('totp')}
            />
          </div>

          <Button
            size="lg"
            className="w-full gap-3"
            disabled={!selectedMethod}
            onClick={handleMethodChosen}
          >
            {m.setup_mfa_continue()}
            <ArrowRight size={16} />
          </Button>

          {!isRequired && (
            <div className="text-center">
              <button
                type="button"
                onClick={handleSkip}
                className="text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-rl-accent-dark)] hover:underline"
              >
                {m.setup_mfa_skip()}
              </button>
            </div>
          )}
        </div>
      )}

      {/* ── Method-specific setup ── */}
      {step === 'setup' && selectedMethod === 'passkey' && (
        <PasskeySetup token={token} onSuccess={handleSuccess} onBack={handleBack} />
      )}
      {step === 'setup' && selectedMethod === 'email' && (
        <EmailOTPSetup token={token} email={email} onSuccess={handleSuccess} onBack={handleBack} />
      )}
      {step === 'setup' && selectedMethod === 'totp' && (
        <TOTPSetup token={token} onSuccess={handleSuccess} onBack={handleBack} />
      )}
    </AuthPageLayout>
  )
}
